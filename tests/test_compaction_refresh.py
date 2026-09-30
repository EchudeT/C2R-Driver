import json

from driver_port_factory.codex.compaction import CURSOR, refresh_inputs
from driver_port_factory.codex.repair_handoff import organize_repair_context
from tests.test_repair_prompt_organization import context

THREAD = "01a0d93e-26ae-7713-9d5c-336f23490a89"


def rollout(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    path = tmp_path / "sessions" / f"rollout-{THREAD}.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': THREAD}}) + '\n')
    return path


def test_compaction_reinjects_once_and_next_compaction_reinjects_again(tmp_path, monkeypatch):
    path = rollout(tmp_path, monkeypatch)
    known = {}
    organize_repair_context(context(), 'environment_recovery', {}, known)
    old, cursor, _ = refresh_inputs(THREAD, known)
    known = {**old, CURSOR: cursor}
    assert 'report_excerpt' not in organize_repair_context(context(), 'environment_recovery', known, {})['repair_task']
    for _ in range(2):
        with path.open('a') as f:
            f.write('{"type":"compacted","payload":{"message":"summary"}}\n')
        fresh, cursor, event = refresh_inputs(THREAD, known)
        assert fresh == {} and event == {'status': 'compacted', 'reinject': True}
        supplied = {CURSOR: cursor}
        packet = organize_repair_context(context(), 'environment_recovery', fresh, supplied)
        assert 'F2' in packet['repair_task']['report_excerpt']
        known, cursor, event = refresh_inputs(THREAD, supplied)
        assert event['status'] == 'unchanged'
        assert 'report_excerpt' not in organize_repair_context(context(), 'environment_recovery', known, {})['repair_task']


def test_partial_record_and_failed_invocation_do_not_acknowledge_event(tmp_path, monkeypatch):
    path = rollout(tmp_path, monkeypatch)
    _, cursor, _ = refresh_inputs(THREAD, {})
    known = {CURSOR: cursor, 'repair_view/environment_recovery': 'old'}
    with path.open('a') as f:
        f.write('{"type":"compacted"')
    _, incomplete, event = refresh_inputs(THREAD, known)
    assert event['status'] == 'unchanged' and incomplete == cursor
    with path.open('a') as f:
        f.write('}\n')
    assert refresh_inputs(THREAD, known)[2]['reinject']
    # Failure leaves old inputs persisted: the event is still actionable on retry.
    assert refresh_inputs(THREAD, known)[2]['reinject']


def test_missing_mismatched_and_replaced_logs(tmp_path, monkeypatch):
    path = rollout(tmp_path, monkeypatch)
    _, cursor, _ = refresh_inputs(THREAD, {})
    known = {CURSOR: cursor, 'x': 'y'}
    path.unlink()
    assert refresh_inputs(THREAD, known)[0] == known
    path.write_text('{"type":"session_meta","payload":{"id":"other"}}\n')
    assert refresh_inputs(THREAD, known)[2]['status'] == 'identity_mismatch'
    replacement = path.with_suffix('.tmp')
    replacement.write_text(json.dumps({'type': 'session_meta', 'payload': {'id': THREAD}}) + '\n')
    replacement.replace(path)
    assert refresh_inputs(THREAD, known)[2]['status'] == 'log_replaced'
    assert refresh_inputs(None, known) == (known, None, None)


def test_token_usage_drop_is_not_mistaken_for_compaction(tmp_path, monkeypatch):
    path = rollout(tmp_path, monkeypatch)
    with path.open('a') as f:
        for n in (200000, 2000):
            f.write(json.dumps({'type': 'event_msg', 'payload': {'type': 'token_count', 'tokens': n}}) + '\n')
    known = {'x': 'y'}
    assert refresh_inputs(THREAD, known)[0] == known


def test_controller_reinjects_after_compaction_inside_previous_call(tmp_path, monkeypatch):
    from unittest.mock import patch
    from driver_port_factory.codex.cli import run_codex_stage
    from driver_port_factory.codex.context_policy import configure_policy
    from driver_port_factory.codex.contracts import CodexBackend
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.migration.contracts import MigrationStage as S
    from tests.workflow_support import ready_implementation
    from tests.submission_support import submit

    home = tmp_path / 'native'
    path = rollout(home, monkeypatch)
    project = ready_implementation(tmp_path)
    configure_policy(project, 'persistent', reason='test native compaction')
    artifact = project.artifacts.put_bytes(b'F1 route; F2 DMA', kind='codex_work_report')
    material = context()
    material['repair_task']['source'] = {
        'kind': artifact.kind, 'digest': artifact.digest,
        'path': str(project.artifacts.path_for_digest(artifact.digest))}
    seen = []

    def gateway(job):
        header = json.loads(job.prompt.split('<job>')[1].split('</job>')[0])
        seen.append('report_excerpt' in header['reference_material']['repair_task'])
        if len(seen) == 1:
            with path.open('a') as f:
                f.write('{"type":"compacted","payload":{"message":"summary"}}\n')
        report = job.execution_root / '.dpf-output/report.md'
        report.parent.mkdir(exist_ok=True)
        report.write_text('Synthetic blocked work, no runtime proof.')
        submit(project, job, report, kind='report', decision='blocked')
        return CodexResult(job.job_id, '', THREAD)

    with patch('driver_port_factory.codex.cli.CodexExecGateway.run', side_effect=gateway):
        for _ in range(3):
            run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context=material,
                            backend=CodexBackend.EXEC, codex_bin='codex', model=None)
    assert seen == [True, True, False]
    metrics = [json.loads(f.read_text()) for f in (project.control / 'codex').glob('*.metrics.json')]
    assert sum((m.get('native_compaction') or {}).get('status') == 'compacted' for m in metrics) == 1
