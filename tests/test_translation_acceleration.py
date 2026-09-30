"""Translation assistance tests, including real C/Rust pure-logic comparison."""
import json
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.core.models import WorkflowError
from driver_port_factory.core.execution import CommandRunner
from driver_port_factory.knowledge.translation_facts import prepare, query, scan
from driver_port_factory.migration.diagnostics import summarize
from driver_port_factory.migration.probes import run
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.workflow_support import ready_implementation


def active_project(tmp_path):
    project = ready_implementation(tmp_path)
    project.start(S.DRIVER_IMPLEMENTATION)
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    output = worktree / '.dpf-output'
    output.mkdir(exist_ok=True)
    return project, output


def test_lexical_navigation_keeps_locations_and_does_not_claim_semantics():
    text = '/* dma_map ignored\n irq ignored */\n#define DESC 8\nstruct descriptor { int x; };\n' \
           'dma_map(x); // spinlock not a use\nconst char *s = "irq";\n'
    hits, counts = scan(text)
    assert any(h['topic'] == 'dma_layout' and h['line'] == 5 for h in hits)
    assert not any(h['topic'] == 'concurrency' for h in hits)
    assert counts['declarations'] == 2


def test_fact_packet_is_cached_bounded_and_rechecks_originals(tmp_path):
    project, _ = active_project(tmp_path)
    first = prepare(project)
    with patch('driver_port_factory.knowledge.translation_facts.scan', side_effect=AssertionError('rescan')):
        assert prepare(project) == first
    result = query(project, limit=1)
    assert len(result['matches']) <= 1
    assert 'No macro expansion' in result['limits']
    value = json.loads(Path(first['path']).read_text())
    original = Path(value['files'][0]['path'])
    original.write_text(original.read_text() + '\n/* drift */\n')
    with pytest.raises(WorkflowError, match='hash mismatch'):
        prepare(project)


def test_diagnostics_deduplicate_structured_errors_without_guessing_from_prose(tmp_path):
    row = {'reason': 'compiler-message', 'message': {'level': 'error', 'message': 'wrong type',
           'spans': [{'file_name': 'driver.rs', 'line_start': 4, 'column_start': 2, 'is_primary': True}]}}
    script = tmp_path / 'build.sh'
    script.write_text("cat <<'END'\n" + json.dumps(row) + '\n' + json.dumps(row) + '\nEND\nexit 1\n')
    result = CommandRunner(tmp_path / 'runs').run(['/bin/bash', str(script)], cwd=tmp_path)
    feedback = summarize(result)
    assert feedback['category'] == 'COMPILER_ERROR'
    assert len(feedback['diagnostics']) == 1
    assert feedback['diagnostics'][0]['locations'][0]['line_start'] == 4
    script.write_text('echo "error: perhaps a compiler?" >&2\nexit 1\n')
    result = CommandRunner(tmp_path / 'runs2').run(['/bin/bash', str(script)], cwd=tmp_path)
    assert summarize(result)['category'] == 'COMMAND_FAILURE_UNCLASSIFIED'


def test_probe_records_failure_and_repeats_without_gating(tmp_path):
    project, output = active_project(tmp_path)
    script = output / 'probe.sh'
    script.write_text('echo missing-local-tool >&2\nexit 127\n')
    first = run(project, script)
    second = run(project, script)
    assert first['status'] == second['status'] == 'COMMAND_FAILED'
    assert second['same_input_prior_runs'] == 1
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == 'RUNNING'
    assert Path(first['receipt']).is_file()


def test_probe_changed_source_is_not_reported_as_a_match(tmp_path):
    project, output = active_project(tmp_path)
    script = output / 'probe.sh'
    script.write_text('echo changed > "$DPF_TARGET_WORKTREE/changed.rs"\n')
    assert run(project, script)['status'] == 'INPUTS_CHANGED'


@pytest.mark.skipif(not shutil.which('gcc') or not shutil.which('rustc'), reason='C/Rust toolchains required')
def test_real_c_rust_ring_arithmetic_comparison_finds_boundary_bug(tmp_path):
    project, output = active_project(tmp_path)
    c = output / 'reference.c'
    c.write_text('#include <stdio.h>\n#include <stdlib.h>\nint main(void) { '
                 'FILE *f=fopen(getenv("DPF_PROBE_INPUT"),"r"); unsigned x; '
                 'while(fscanf(f,"%u",&x)==1) printf("%u\\n",(x+1)%8); fclose(f); }\n')
    rust = output / 'candidate.rs'
    correct = 'fn main() { let s=std::fs::read_to_string(std::env::var("DPF_PROBE_INPUT").unwrap()).unwrap(); '
    correct += 'for x in s.split_whitespace() { let x: u32=x.parse().unwrap(); println!("{}",(x+1)%8); }}'
    rust.write_text(correct)
    reference = output / 'reference.sh'
    reference.write_text(f'#!/bin/bash\nset -eu\ngcc "{c}" -o "{output}/c-ref"\n"{output}/c-ref"\n')
    candidate = output / 'candidate.sh'
    candidate.write_text(f'#!/bin/bash\nset -eu\nrustc "{rust}" -o "{output}/rs-candidate"\n"{output}/rs-candidate"\n')
    inputs = output / 'cases.txt'
    inputs.write_text('0 1 6 7 8 15\n')
    matched = run(project, candidate, reference=reference, inputs=inputs, contracts=['ring-wrap'], dependencies=[c, rust])
    assert matched['status'] == 'MATCH'
    rust.write_text(correct.replace('(x+1)%8', '(x+1)%7'))
    failed = run(project, candidate, reference=reference, inputs=inputs, dependencies=[c, rust])
    assert failed['status'] == 'MISMATCH'
    assert failed['comparison']['first_difference_byte'] is not None
    receipt = json.loads(Path(failed['receipt']).read_text())
    assert 'ADVISORY_ONLY' in receipt['authority']


def test_probe_requires_fixed_differential_inputs_and_limits_timeout(tmp_path):
    project, output = active_project(tmp_path)
    script = output / 'probe.sh'
    script.write_text('true\n')
    with pytest.raises(WorkflowError, match='explicit fixed input'):
        run(project, script, reference=script)
    with pytest.raises(WorkflowError, match='timeout'):
        run(project, script, timeout=0)


def test_probe_reference_cannot_change_fixed_input_for_candidate(tmp_path):
    project, output = active_project(tmp_path)
    inputs = output / 'case.txt'
    inputs.write_text('original\n')
    reference = output / 'reference.sh'
    reference.write_text('echo changed > "$DPF_PROBE_INPUT"\ncat "$DPF_PROBE_INPUT"\n')
    candidate = output / 'candidate.sh'
    candidate.write_text('cat "$DPF_PROBE_INPUT"\n')
    result = run(project, candidate, reference=reference, inputs=inputs)
    assert result['status'] == 'INPUTS_CHANGED'
    assert inputs.read_text() == 'original\n'


def test_recent_probes_retain_successful_recipe_across_new_failures(tmp_path):
    from driver_port_factory.migration.probes import recent
    project, output = active_project(tmp_path)
    script = output / 'probe.sh'
    script.write_text('true\n')
    success = run(project, script)
    script.write_text('exit 2\n')
    for _ in range(4):
        run(project, script)
    values = recent(project)
    assert any(v['path'] == success['receipt'] and v['status'] == 'COMMAND_OK' for v in values)
    assert len(values) == 4


def test_fact_lookup_cli_returns_bounded_material(tmp_path, capsys):
    from driver_port_factory.cli import main
    project, _ = active_project(tmp_path)
    assert main(['knowledge', 'facts', str(project.root), '--limit', '1']) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['returned'] <= 1
    assert main(['knowledge', 'facts', str(project.root), '--limit', '0']) == 2


def test_probe_cli_archives_declared_adapter_dependency(tmp_path, capsys):
    from driver_port_factory.cli import main
    project, output = active_project(tmp_path)
    helper = output / 'helper.txt'
    helper.write_text('fixed adapter input\n')
    script = output / 'probe.sh'
    script.write_text(f'cat "{helper}"\n')
    assert main(['experiment', 'probe', str(project.root), '--script', str(script),
                 '--dependency', str(helper)]) == 0
    result = json.loads(capsys.readouterr().out)
    receipt = json.loads(Path(result['receipt']).read_text())
    archive = Path(receipt['archived_inputs'][str(helper)]['path'])
    helper.write_text('later change\n')
    assert archive.read_text() == 'fixed adapter input\n'


def test_failed_reference_does_not_spend_time_running_candidate(tmp_path):
    project, output = active_project(tmp_path)
    inputs = output / 'case.txt'
    inputs.write_text('case\n')
    reference = output / 'reference.sh'
    reference.write_text('exit 1\n')
    candidate = output / 'candidate.sh'
    sentinel = output / 'candidate-ran'
    candidate.write_text(f'touch "{sentinel}"\n')
    result = run(project, candidate, reference=reference, inputs=inputs)
    assert result['status'] == 'COMMAND_FAILED'
    assert not sentinel.exists()
