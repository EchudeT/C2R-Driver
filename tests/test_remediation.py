"""Offline functional tests of workflow repairs; no model or real driver certification."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S, MigrationArtifact as A
from driver_port_factory.migration.experiments import execute, run_cases
from tests.migration_support import implemented, public_run


def test_delivery_counterexample_can_revise_analysis_without_changing_scope(tmp_path):
    project, worktree, _ = public_run(tmp_path)
    project.start(S.FINAL_EVIDENCE_REVIEW)
    original = (worktree / 'driver.rs').read_bytes()
    project.retry_from(S.CONTRACTS, trigger=S.FINAL_EVIDENCE_REVIEW,
                       reason='C1 contradicted by actual target ownership definition', progress={'C1': 'counterexample'})
    restored = open_project(project.root)
    assert restored.stage(S.CONTRACTS).status is StageStatus.READY
    restored.start(S.CONTRACTS)
    assert (worktree / 'driver.rs').read_bytes() == original
    from driver_port_factory.environment.contracts import EnvironmentStage
    assert restored.stage(EnvironmentStage.RECOVERY).status is StageStatus.PASS
    restored.verify_integrity()


def test_managed_smoke_reuses_worker_observation_and_preserves_original_logs(tmp_path):
    project, worktree, report = implemented(tmp_path)
    script = worktree / '.dpf-output/implementation-smoke.sh'
    runtime = worktree / '.dpf-output/runtime-artifact'
    # implemented() already ran the identical controller experiment.
    with patch('driver_port_factory.migration.public_qemu._run_public_harness') as run:
        first = execute(project, worktree=worktree, script_path=script, runtime_path=runtime, timeout_seconds=300)
        report.write_text('New explanation; execution unchanged.\n')
        second = execute(open_project(project.root), worktree=worktree, script_path=script,
                         runtime_path=runtime, timeout_seconds=300)
        run.assert_not_called()
    assert first.trace_path == second.trace_path
    log = first.logs[0]
    archived = Path(log['archive_path']).read_bytes()
    (worktree / log['path']).write_text('overwritten by later worker execution\n')
    assert Path(log['archive_path']).read_bytes() == archived
    Path(log['archive_path']).write_text('damaged archive\n')
    from driver_port_factory.migration.public_qemu import _run_public_harness
    with patch('driver_port_factory.migration.public_qemu._run_public_harness', wraps=_run_public_harness) as run:
        execute(project, worktree=worktree, script_path=script, runtime_path=runtime, timeout_seconds=300)
        run.assert_not_called()


def _cases(worktree):
    output = worktree / '.dpf-output'
    harness = output / 'harness'
    harness.mkdir(exist_ok=True)
    cases = []
    for name in ('a', 'b'):
        (harness / f'{name}.input').write_text(name)
        (harness / f'{name}.sh').write_text(
            f'"{output / "qemu-system-smoke-fixture"}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
            f'cat .dpf-output/harness/{name}.input > .dpf-output/qemu-runs/{name}.log\n')
        cases.append({'id': name, 'script': f'.dpf-output/harness/{name}.sh',
                      'dependencies': [f'.dpf-output/harness/{name}.input'], 'contracts': [f'C-{name}'],
                      'timeout_seconds': 300})
    (output / 'experiments.json').write_text(json.dumps(cases))
    return output


def test_passed_cases_survive_dependency_changes_and_force_requests(tmp_path):
    project, worktree, _ = implemented(tmp_path)
    output = _cases(worktree)
    runtime = output / 'runtime-artifact'
    first = run_cases(project, worktree, runtime)
    (output / 'harness/a.input').write_text('changed stimulus')
    second = run_cases(project, worktree, runtime)
    assert first[0]['observation'].trace_path == second[0]['observation'].trace_path
    assert first[1]['observation'].trace_path == second[1]['observation'].trace_path
    third = execute(project, worktree=worktree, script_path=output / 'harness/b.sh',
                    runtime_path=runtime, dependencies=['.dpf-output/harness/b.input'],
                    case_id='b', timeout_seconds=300, force=True)
    assert third.trace_path == second[1]['observation'].trace_path
    fresh = run_cases(project, worktree, runtime, force=True)
    assert all(a['observation'].trace_path == b['observation'].trace_path
               for a, b in zip(second, fresh))


def test_optional_obligation_map_exposes_unexecuted_requirements_without_gate(tmp_path):
    from driver_port_factory.migration.coverage import coverage
    output = tmp_path / '.dpf-output'
    output.mkdir()
    (output / 'obligations.json').write_text(json.dumps([{'id': 'C-a'}, {'id': 'C-b'}]))
    rows = coverage(tmp_path, [{'id': 'a', 'contracts': ['C-a'], 'status': 'PASS'}])['obligations']
    assert rows[0]['execution'] == 'OBSERVED'
    assert rows[1]['execution'] == 'NOT_RUN'
    (output / 'obligations.json').write_text('broken optional navigation')
    assert coverage(tmp_path)['status'] == 'UNREADABLE'


def test_platform_assets_bind_configuration_and_detect_tampering(tmp_path):
    from driver_port_factory.platform_assets import export_asset, import_asset, references
    project, _, _ = implemented(tmp_path)
    facts = project.root / 'platform-facts.md'
    facts.write_text('Platform API source references, toolchain and build recipe; no driver verdict.\n')
    asset = export_asset(project, facts, tmp_path / 'shared-assets', 'x86_64:fixture-toolchain')
    imported = import_asset(project, asset, 'x86_64:fixture-toolchain')
    assert references(project)[0]['digest'] == asset.stem
    with pytest.raises(WorkflowError, match='mismatch'):
        import_asset(project, asset, 'aarch64:different-toolchain')
    imported.write_text('tampered')
    with pytest.raises((WorkflowError, ValueError)):
        references(project)


def test_final_review_rejects_source_drift(tmp_path):
    from driver_port_factory.migration.final_evidence_review import FinalEvidenceReviewService
    from driver_port_factory.migration.implementation import ImplementationChanged
    project, worktree, _ = public_run(tmp_path)
    (worktree / 'driver.rs').write_text('changed after runtime checks\n')
    with pytest.raises(ImplementationChanged):
        FinalEvidenceReviewService.review_inputs(project)


def test_structural_bundle_error_cannot_be_accepted_as_collector_disagreement(tmp_path):
    from driver_port_factory.core.checker_decision import request_decision, CheckerDecisionRequired, accept_decision
    from driver_port_factory.core.models import ArtifactRef
    project, _, report = implemented(tmp_path)
    # A forged pending request carrying syntactically invalid implementation bytes
    # must be revalidated, even if created before typed checker errors existed.
    refs = project.current_artifact_refs(stage=S.DRIVER_IMPLEMENTATION)
    outputs = [r for r in refs if r.kind in {A.IMPLEMENTATION_BUNDLE.value,
                                            A.COMPLIANCE_REPORT.value, A.TARGET_CHANGE_INVENTORY.value}]
    bad = project.artifacts.put_bytes(b'not-json', kind=A.IMPLEMENTATION_BUNDLE.value)
    candidates = [ArtifactRef(bad, 'synthetic') if r.kind == A.IMPLEMENTATION_BUNDLE.value else r
                  for r in outputs]
    with pytest.raises(CheckerDecisionRequired) as caught:
        request_decision(project, S.DRIVER_IMPLEMENTATION, candidates, ['legacy untyped finding'])
    with pytest.raises(WorkflowError):
        accept_decision(project, S.DRIVER_IMPLEMENTATION, caught.value.path, report)


def test_analysis_receipt_recovers_crash_after_acceptance_without_model_reanalysis(tmp_path):
    from tests.workflow_support import ready_implementation, runner
    from tests.submission_support import submit
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.target_study.contracts import TargetStudyStage as TS
    project = ready_implementation(tmp_path, reviewed=False)
    project.start(S.ANALYSIS_REVIEW)
    project.retry_from(TS.STUDY, trigger=S.ANALYSIS_REVIEW, reason='new C1 evidence')
    port = runner(project)
    calls = []
    def gateway(job):
        calls.append(job.stage)
        report = job.execution_root / 'combined-analysis.md'
        report.write_text('Combined source/target contracts and test obligations.\n')
        submit(project, job, report, kind='report', decision='pass')
        return CodexResult(job.job_id, '', 'worker')
    with patch('driver_port_factory.codex.cli.CodexExecGateway.run', side_effect=gateway):
        with patch('driver_port_factory.migration.analysis_delivery.record', side_effect=RuntimeError('crash')):
            with pytest.raises(RuntimeError, match='crash'):
                port._target_study(project)
        project = open_project(project.root)
        port._handoff(project)
        port._contracts(project)
    assert calls == [TS.STUDY]
    assert project.stage(S.CONTRACTS).status is StageStatus.PASS
    project.verify_integrity()


def test_prepared_receipt_rejects_policy_not_read_by_job(tmp_path):
    from driver_port_factory.migration.analysis_delivery import record, prepared
    from tests.workflow_support import ready_implementation
    project = ready_implementation(tmp_path, reviewed=False)
    assert record(project, job_policy='different rules') is False
    assert prepared(project) is None


def test_public_case_manifest_reaches_final_review_with_all_obligations_visible(tmp_path):
    from tests.migration_support import packaged
    from driver_port_factory.migration.public_qemu import PublicQemuService
    project, worktree, report = packaged(tmp_path)
    output = _cases(worktree)
    (output / 'public-qemu.sh').write_text('exit 0\n')
    (output / 'obligations.json').write_text(json.dumps([{'id': 'C-a'}, {'id': 'C-b'}, {'id': 'C-c'}]))
    project.start(S.PUBLIC_QEMU_VALIDATION)
    service = PublicQemuService()
    service.run_script(project, script_path=output / 'public-qemu.sh', work_report_path=report)
    service.accept_self_review(project, work_report_path=report)
    result = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
    assert [c['status'] for c in result['run']['cases']] == ['PASS', 'PASS']
    assert result['coverage']['obligations'][-1]['execution'] == 'NOT_RUN'
    # Missing obligations are visible for semantic review, not papered over by process PASS.
    assert result['semantic_verdict'] == 'REQUIRES_INDEPENDENT_AI_REVIEW'


@pytest.mark.parametrize("timeout", [0, -1, 86401, True])
def test_managed_experiment_rejects_invalid_timeout_before_execution(timeout):
    with pytest.raises(WorkflowError, match='timeout'):
        execute(None, worktree=None, script_path=None, runtime_path=None, timeout_seconds=timeout)


def test_explicit_self_review_is_bound_to_exact_report_and_receipt(tmp_path):
    import uuid
    from driver_port_factory.migration.experiment_ack import record_seen, acknowledge, acknowledged
    project, worktree, report = implemented(tmp_path)
    project.start(S.ARTIFACT_PREPARATION)
    job_id = str(uuid.uuid4())
    metrics = project.control / 'codex'
    metrics.mkdir(exist_ok=True)
    (metrics / f'fixture-{job_id}.metrics.json').write_text(json.dumps({
        'invocation_state': 'RUNNING', 'stage': S.ARTIFACT_PREPARATION.value}))
    observation = execute(project, worktree=worktree,
        script_path=worktree / '.dpf-output/implementation-smoke.sh',
        runtime_path=worktree / '.dpf-output/runtime-artifact', timeout_seconds=300)
    record_seen(project, job_id, [{'id': 'smoke', 'status': 'PASS', 'observation': observation}])
    acknowledge(project, job_id, report)
    public = {'run': {'exec_trace': {'path': str(observation.trace_path)}}}
    assert acknowledged(project, report, public)
    original = report.read_bytes()
    report.write_text('new conclusions require self-review again\n')
    assert not acknowledged(project, report, public)
    report.write_bytes(original)
    receipt = observation.trace_path.parent / 'receipt.json'
    receipt.write_text(receipt.read_text() + '\n')
    assert not acknowledged(project, report, public)


def test_cli_fresh_keeps_already_passed_observations(tmp_path, capsys):
    from argparse import Namespace
    from driver_port_factory.migration.experiment_cli import command_run
    project, worktree, _ = implemented(tmp_path)
    project.start(S.ARTIFACT_PREPARATION)
    _cases(worktree)
    args = Namespace(path=str(project.root), runtime=None, suite=True, fresh=False, job_id=None)
    command_run(args)
    initial = json.loads(capsys.readouterr().out)
    command_run(args)
    reused = json.loads(capsys.readouterr().out)
    assert [v['receipt'] for v in initial] == [v['receipt'] for v in reused]
    args.fresh = True
    command_run(args)
    fresh = json.loads(capsys.readouterr().out)
    assert all(a['receipt'] == b['receipt'] for a, b in zip(initial, fresh))
