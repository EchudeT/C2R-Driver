"""Bounded assistance: actual compiler queries, public vectors and ccache reuse."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.knowledge.semantic import query
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.migration.source_cases import generate
from driver_port_factory.build_cache import configure, environment, status
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.test_translation_acceleration import active_project


def compilation(output, flags=()):
    source = output / 'sample.c'
    source.write_text('#include "types.h"\nint sample(struct Buffer *p) { return p->value; }\n')
    header = output / 'types.h'
    header.write_text('struct Buffer { int value; };\n')
    database = output / 'compile_commands.json'
    database.write_text(json.dumps([{'directory': str(output), 'file': str(source),
        'arguments': ['cc', *flags, '-c', str(source), '-o', str(output / 'sample.o')]}]))
    return source, header, database


@pytest.mark.skipif(not shutil.which('clang'), reason='Clang required')
def test_semantic_query_is_bounded_caches_parse_and_invalidates_header(tmp_path):
    project, output = active_project(tmp_path)
    source, header, db = compilation(output)
    first = query(project, db, source, 'value', limit=1)
    assert first['status'] == 'INDEXED'
    assert len(first['matches']) == 1
    assert first['matches'][0]['type'] == 'int'
    assert Path(first['matches'][0]['file']) == header
    with patch('driver_port_factory.knowledge.semantic.extract', side_effect=AssertionError('unnecessary AST parse')):
        assert query(project, db, source, 'value', limit=1)['matches'] == first['matches']
    header.write_text('struct Buffer { long value; };\n')
    assert query(project, db, source, 'value', limit=1)['matches'][0]['type'] == 'long'


@pytest.mark.skipif(not shutil.which('clang'), reason='Clang required')
def test_semantic_compile_failure_is_advisory(tmp_path):
    project, output = active_project(tmp_path)
    source, header, db = compilation(output)
    header.unlink()
    assert query(project, db, source, 'Buffer')['status'] == 'UNAVAILABLE'


def source_spec(project, output):
    records = KnowledgeIndex.for_project(project).verified_records()
    original = next(r for r in records if r['domain'] == 'source')
    spec = output / 'source-domain.json'
    spec.write_text(json.dumps({'contract': 'boundary', 'evidence': [
        {'path': original['path'], 'line_start': 1, 'line_end': 1}],
        'integer_fields': {'head': {'min': 0, 'max': 7}, 'length': {'min': 0, 'max': 1500}},
        'critical_cases': [{'head': 7, 'length': 0}]}))
    return spec


def test_public_vectors_respect_budget_prioritize_explicit_case_and_do_not_read_candidate(tmp_path):
    project, output = active_project(tmp_path)
    spec = source_spec(project, output)
    result = generate(project, spec, budget=3)
    value = json.loads(Path(result['path']).read_text())
    assert len(value['cases']) == 3
    assert value['cases'][0] == {'head': 7, 'length': 0}
    assert value['omitted_generated_cases'] > 0
    assert 'PUBLIC_DEVELOPER_INPUTS_ONLY' in value['limits']
    (output / 'candidate.rs').write_text('broken or secret implementation\n')
    assert generate(project, spec, budget=3) == result
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == 'RUNNING'


def test_public_vectors_reject_candidate_as_original_evidence(tmp_path):
    project, output = active_project(tmp_path)
    spec = source_spec(project, output)
    value = json.loads(spec.read_text())
    candidate = output / 'candidate.rs'
    candidate.write_text('fn candidate() {}\n')
    value['evidence'][0]['path'] = str(candidate)
    spec.write_text(json.dumps(value))
    with pytest.raises(WorkflowError, match='controlled original'):
        generate(project, spec)


@pytest.mark.skipif(not shutil.which('ccache') or not shutil.which('gcc'), reason='ccache and GCC required')
def test_shared_ccache_reuses_objects_without_importing_pass(tmp_path):
    project, output = active_project(tmp_path)
    profile = output / 'cache-profile.json'
    profile.write_text(json.dumps({'architecture': 'native-fixture', 'toolchain': 'gcc-fixture',
                                  'configuration': 'no-debug-O0'}))
    configure(project, tmp_path / 'shared', profile)
    env = {**os.environ, **environment(project)}
    first = status(project)['counters']
    for directory in (output / 'build-a', output / 'build-b'):
        directory.mkdir()
        (directory / 'same.c').write_text('int add(int a,int b) { return a+b; }\n')
        subprocess.run(['ccache', 'gcc', '-c', 'same.c', '-o', 'same.o'], cwd=directory,
                       env=env, check=True, capture_output=True)
    last = status(project)['counters']
    assert last.get('direct_cache_hit', 0) + last.get('preprocessed_cache_hit', 0) > (
        first.get('direct_cache_hit', 0) + first.get('preprocessed_cache_hit', 0))
    # Configuration changes select a distinct namespace; no stage acceptance is imported.
    previous = environment(project)['CCACHE_DIR']
    profile.write_text(json.dumps({'architecture': 'different', 'toolchain': 'gcc-fixture',
                                  'configuration': 'no-debug-O0'}))
    configure(project, tmp_path / 'shared', profile)
    assert environment(project)['CCACHE_DIR'] != previous


def test_cache_config_tampering_is_detected(tmp_path):
    project, output = active_project(tmp_path)
    profile = output / 'cache-profile.json'
    profile.write_text(json.dumps({'architecture': 'a', 'toolchain': 'b', 'configuration': 'c'}))
    record = configure(project, tmp_path / 'cache', profile)
    (Path(record['namespace']) / 'ccache.conf').write_text('sloppiness = time_macros\n')
    assert environment(project) == {}
    assert status(project)['enabled'] is False


def test_optional_tools_are_not_injected_into_unrelated_stages():
    from types import SimpleNamespace
    from driver_port_factory.codex.optional_tools import context
    from driver_port_factory.acquisition.contracts import AcquisitionStage
    project = SimpleNamespace(root=Path('/run'))
    assert context(project, AcquisitionStage.EVIDENCE_CLOSURE) == {}
    assert context(project, S.FINAL_EVIDENCE_REVIEW) == {}
    value = context(project, S.DRIVER_IMPLEMENTATION)
    assert 'semantic_lookup' in value and 'optional_tools_guide' in value
    assert 'Clang' not in json.dumps(value)  # detailed instructions are not embedded


@pytest.mark.skipif(not shutil.which('clang'), reason='Clang required')
def test_new_shadow_header_invalidates_semantic_lookup(tmp_path):
    project, output = active_project(tmp_path)
    early, late = output / 'early', output / 'late'
    early.mkdir()
    late.mkdir()
    source, local, db = compilation(output, ['-I', str(early), '-I', str(late)])
    local.unlink()
    (late / 'types.h').write_text('struct Buffer { int value; };\n')
    assert query(project, db, source, 'value', limit=1)['matches'][0]['type'] == 'int'
    (early / 'types.h').write_text('struct Buffer { long value; };\n')
    assert query(project, db, source, 'value', limit=1)['matches'][0]['type'] == 'long'
