"""Cost/recovery policy tests; synthetic observations are not driver certification."""
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from driver_port_factory.acquisition.job import ArtifactOccurrence
from driver_port_factory.codex.cli import run_codex_stage
from driver_port_factory.codex.context_reset import reset_session
from driver_port_factory.codex.contracts import CodexBackend, CodexContinuation
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.codex.policy import CodexExecutionPolicy
from driver_port_factory.codex.sessions import read_session, save_session, stage_session
from driver_port_factory.composition import open_project
from driver_port_factory.control.statistics import job_elapsed
from driver_port_factory.core.continuation import record_continuation
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.port import PortRunner
from tests.submission_support import submit
from tests.workflow_support import ready_implementation


def test_interrupted_legacy_timing_does_not_grow_with_controller_downtime():
    now = datetime(2026, 9, 26, tzinfo=UTC)
    job = {"started_at": (now - timedelta(hours=7)).isoformat(), "elapsed_seconds": 0.212}
    assert job_elapsed(job, now, controller_active=False) == 0.212
    assert job_elapsed(job, now, controller_active=True) == 0.212
    job["invocation_state"] = "RUNNING"
    assert job_elapsed(job, now, controller_active=True) == 7 * 3600
    assert job_elapsed(job, now, controller_active=False) == 0.212
    job["completed_at"] = now.isoformat()
    assert job_elapsed(job, now, controller_active=True) == 0.212


def test_continuation_survives_reopen_without_counting_replayed_submission(tmp_path):
    project = ready_implementation(tmp_path)
    first = ArtifactOccurrence("a" * 64, 1)
    second = ArtifactOccurrence("b" * 64, 2)
    row = record_continuation(project, S.DRIVER_IMPLEMENTATION, first, "same", detail="missing")
    assert row["consecutive"] == 1
    reopened = open_project(project.root)
    assert record_continuation(reopened, S.DRIVER_IMPLEMENTATION, first,
                               "same", detail="missing")["consecutive"] == 1
    assert record_continuation(reopened, S.DRIVER_IMPLEMENTATION, second,
                               "same", detail="missing")["consecutive"] == 2
    assert record_continuation(reopened, S.DRIVER_IMPLEMENTATION, second,
                               "new-observation", detail="different")["consecutive"] == 1
    assert project.verify_event_chain()


def test_helper_repair_and_new_observation_are_not_stagnation(tmp_path):
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    project = ready_implementation(tmp_path)
    tree = project.root / load_repository_acquisition(project).target_worktree.path
    helper = tree / ".dpf-output/harness/qmp.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# before\n")
    finding = CodexContinuation("smoke failed", observation={"runtime_bound": False})
    before = PortRunner._continuation_fingerprint(project, S.DRIVER_IMPLEMENTATION, finding)
    helper.write_text("# repaired QMP helper\n")
    after = PortRunner._continuation_fingerprint(project, S.DRIVER_IMPLEMENTATION, finding)
    assert before != after
    newer = CodexContinuation("smoke failed", observation={"runtime_bound": True})
    assert after != PortRunner._continuation_fingerprint(project, S.DRIVER_IMPLEMENTATION, newer)
    (tree / ".dpf-output/report.md").write_text("Report wording alone is not progress.\n")
    assert after == PortRunner._continuation_fingerprint(project, S.DRIVER_IMPLEMENTATION, finding)


def test_explicit_context_reset_reloads_rules_and_preserves_frozen_evidence(tmp_path):
    project = ready_implementation(tmp_path)
    grant = CodexExecutionPolicy().grant(project, S.DRIVER_IMPLEMENTATION)
    key, _ = stage_session(project, S.DRIVER_IMPLEMENTATION, grant, None, "exec")
    save_session(project, key, "old-worker", {"knowledge-guided-driver-port/SKILL.md": "old"})
    packet = reset_session(project, S.DRIVER_IMPLEMENTATION, key, reason="stale diagnosis")
    state = read_session(project, key)
    assert not state.get("thread_id") and state["documents"] == {}
    evidence = json.loads(project.artifacts.path_for_digest(packet["digest"]).read_text())
    assert any(r["kind"] == "migration_contracts" for r in evidence["current_evidence"])
    assert (project.control / "codex/sessions/archive").is_dir()
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "READY"

    def gateway(job):
        assert job.thread_id is None
        assert '<skill_document path="knowledge-guided-driver-port/SKILL.md"' in job.prompt
        assert packet["digest"] in job.prompt
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "reset-report.md"
        report.write_text("Partial implementation; required behavior remains unresolved.")
        submit(project, job, report, kind="report", decision="blocked")
        return CodexResult(job.job_id, "", "new-worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context={}, backend=CodexBackend.EXEC,
                        codex_bin="codex", model=None, thread_id="stale-caller-thread")
    assert read_session(project, key)["thread_id"] == "new-worker"
    metric = json.loads(next((project.control / "codex").glob("*.metrics.json")).read_text())
    assert metric["usage_baseline"]["input_tokens"] == 0
    assert metric["context_epoch"] == packet["epoch"]
    assert metric["invocation_state"] == "COMPLETED"
    project.verify_integrity()


def test_invocation_interrupt_persists_terminal_metrics(tmp_path):
    project = ready_implementation(tmp_path)
    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=KeyboardInterrupt),
        pytest.raises(KeyboardInterrupt),
    ):
        run_codex_stage(project, S.DRIVER_IMPLEMENTATION, context={},
                        backend=CodexBackend.EXEC, codex_bin="codex", model=None)
    metric = json.loads(next((project.control / "codex").glob("*.metrics.json")).read_text())
    assert metric["invocation_state"] == "INTERRUPTED"
    assert metric["completed_at"] and metric["usage"] is None


def test_guard_stops_three_distinct_unchanged_submissions_across_restart(tmp_path):
    from tests.workflow_support import runner
    project = ready_implementation(tmp_path)
    project.start(S.DRIVER_IMPLEMENTATION)
    port = runner(project)
    occurrences = [ArtifactOccurrence(str(i) * 64, i) for i in (1, 2, 3)]
    # Simulate a prior process that captured the first failure before dying.
    error = CodexContinuation("same failure", observation={"harness_exit": 1})
    fingerprint = port._continuation_fingerprint(project, S.DRIVER_IMPLEMENTATION, error)
    record_continuation(project, S.DRIVER_IMPLEMENTATION, occurrences[0], fingerprint,
                        detail=port._continuation_detail(error))
    with (
        patch.object(port, "_latest_job_occurrence", return_value=occurrences[0]),
        patch.object(port, "_submission_for_job", return_value=None),
        patch.object(port, "_codex", return_value=(
            CodexResult("job", "", "worker"), None, None)) as call,
        patch.object(port, "_job_occurrence", side_effect=occurrences[1:]),
    ):
        def accept(*_):
            raise error
        assert port._codex_gate(project, S.DRIVER_IMPLEMENTATION, {}, accept) is False
    assert call.call_count == 2
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "BLOCKED"
    with sqlite3.connect(project.database_path) as db:
        count = db.execute("SELECT count(*) FROM events WHERE event_type='run.continuation'")
        assert count.fetchone()[0] == 3
    project.reopen_blocked(S.DRIVER_IMPLEMENTATION, reason="external runtime condition restored")
    history = record_continuation(project, S.DRIVER_IMPLEMENTATION,
                                  ArtifactOccurrence("4" * 64, 4), fingerprint, detail="same")
    assert history["consecutive"] == 1


def test_execution_success_is_not_independent_functional_assessment(tmp_path):
    from driver_port_factory.control.evidence import evidence_summary
    from tests.migration_support import public_run
    project, _, _ = public_run(tmp_path)
    summary = evidence_summary(project)
    assert summary["execution"] == "PASS"
    assert summary["functional_assessment"] == "WORKER_REPORT_ONLY"
    assert "public_qemu_work_report" in summary["reports"]
    # Status presentation neither reopens a passed stage nor requires a new call.
    assert project.stage(S.PUBLIC_QEMU_VALIDATION).status.value == "PASS"
