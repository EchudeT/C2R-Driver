import json


from pathlib import Path


import pytest


from driver_port_factory.composition import open_project


from driver_port_factory.core.checker_decision import (
    CheckerDecisionRequired,
    accept_decision,
    pending_decision,
)


from driver_port_factory.core.models import StageStatus, WorkflowError
from tests.submission_support import submit


def test_changed_execution_inputs_do_not_exhaust_recovery_budget(tmp_path):
    from tests.migration_support import packaged
    from driver_port_factory.core.checker_decision import (
        request_recovery, clear_pending, RecoveryPaused,
    )
    from driver_port_factory.migration.contracts import MigrationStage as M
    project, worktree, report = packaged(tmp_path)
    project.start(M.PUBLIC_QEMU_VALIDATION)
    script = worktree / ".dpf-output/public-qemu.sh"
    bad_link = worktree / "bad-link"
    bad_link.symlink_to("driver.rs")
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(project, M.PUBLIC_QEMU_VALIDATION, WorkflowError("unsupported symlink"))
    assert pending_decision(project, M.PUBLIC_QEMU_VALIDATION) is not None
    clear_pending(project, M.PUBLIC_QEMU_VALIDATION)
    bad_link.unlink()
    for iteration in range(5):
        script.write_text(f"exit {iteration + 1}\n")
        with pytest.raises(CheckerDecisionRequired):
            request_recovery(project, M.PUBLIC_QEMU_VALIDATION, WorkflowError("fixture failure"))
        clear_pending(project, M.PUBLIC_QEMU_VALIDATION)
    # Descriptive edits do not reset the same executable failure budget.
    for iteration in range(2):
        report.write_text(f"New explanation {iteration}\n")
        with pytest.raises(CheckerDecisionRequired):
            request_recovery(project, M.PUBLIC_QEMU_VALIDATION, WorkflowError("fixture failure"))
        clear_pending(project, M.PUBLIC_QEMU_VALIDATION)
    with pytest.raises(RecoveryPaused):
        request_recovery(project, M.PUBLIC_QEMU_VALIDATION, WorkflowError("fixture failure"))
    reopened = open_project(project.root)
    with pytest.raises(RecoveryPaused):
        pending_decision(reopened, M.PUBLIC_QEMU_VALIDATION)
    script.write_text("exit 0\n")
    assert pending_decision(reopened, M.PUBLIC_QEMU_VALIDATION) is None
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(reopened, M.PUBLIC_QEMU_VALIDATION, WorkflowError("new fixture observation"))


@pytest.mark.parametrize("tracer", ["installed", "missing", "unusable"])
def test_public_acceptance_reaches_completion_without_rejecting_same_receipt(tmp_path, tracer):
    from contextlib import nullcontext
    from unittest.mock import patch
    from tests.migration_support import packaged
    from driver_port_factory.migration.contracts import MigrationStage as M, MigrationArtifact as B
    from driver_port_factory.migration.public_qemu import PublicQemuService

    project, worktree, report = packaged(tmp_path)
    project.start(M.PUBLIC_QEMU_VALIDATION)
    script = worktree / ".dpf-output/public-qemu.sh"
    script.write_text("echo attempted > .dpf-output/attempted.txt\nexit 7\n")
    report.write_text("Synthetic execution request.\n")
    service = PublicQemuService()
    with patch("driver_port_factory.core.execution.shutil.which", return_value=None if tracer == "missing" else "/bin/false") if tracer != "installed" else nullcontext():
        first = service.run_script(project, script_path=script, work_report_path=report)
    assert (worktree / ".dpf-output/attempted.txt").read_text().strip() == "attempted"
    receipt = json.loads(Path(first["attempt"]).read_text())
    assert receipt["run"]["exec_trace"]["collector"]["available"] is (tracer == "installed")
    assert first["status"] == "FAIL"
    # Restarting the same operation must return its failed receipt, not rerun it.
    report.write_text("Corrected description.\n")
    assert service.run_script(open_project(project.root), script_path=script,
                              work_report_path=report) == first
    report.write_text(
        "Synthetic worker self-check; not real driver evidence.\n"
    )
    with pytest.raises(CheckerDecisionRequired) as caught:
        service.accept_self_review(project, work_report_path=report)
    decision = worktree / ".dpf-output/decision.md"
    decision.write_text(
        "Synthetic acceptance exercising the control path only.\n"
    )
    accept_decision(project, M.PUBLIC_QEMU_VALIDATION, caught.value.path, decision)
    assert project.stage(M.FINAL_EVIDENCE_REVIEW).status is StageStatus.READY
    assert (
        project.load_json_artifact(M.PUBLIC_QEMU_VALIDATION, B.PUBLIC_QEMU_REPORT)[
            "execution_status"
        ]
        == "FAIL"
    )
    assert project.stage(M.PUBLIC_QEMU_VALIDATION).message.startswith("WORKER_ACCEPTED:")
    open_project(project.root).verify_integrity()


@pytest.mark.parametrize("restart", [False, True])
def test_repaired_model_deliverable_is_consumed_once(tmp_path, restart):
    from unittest.mock import patch
    from tests.workflow_support import ready_implementation, runner
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.core.checker_decision import request_recovery
    from driver_port_factory.migration.contracts import MigrationStage as M

    project = ready_implementation(tmp_path, plan=False)
    stage = M.CONTRACTS
    project.start(stage)
    from driver_port_factory.core.checker_decision import clear_pending
    draft = project.root / "work/stage-work/migration_contracts/plan.md"
    draft.parent.mkdir(parents=True, exist_ok=True)
    for section in ("source closure", "target APIs", "IRQ", "test oracle", "lifecycle"):
        draft.write_text(f"Corrected {section}\n")
        with pytest.raises(CheckerDecisionRequired):
            request_recovery(project, stage, WorkflowError("incomplete contract plan"))
        clear_pending(project, stage)
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port/references"
    for name in ("workflow.md", "test-porting.md", "qemu-evidence.md"):
        (references / name).write_text("Inspect originals and self-check.\n")
    with pytest.raises(CheckerDecisionRequired):
        request_recovery(project, stage, WorkflowError("synthetic missing deliverable"))

    def worker(job):
        payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
        assert (
            "references/workflow.md phases 3–5" in payload["instructions"]["objective"]
        )
        assert Path(payload["instructions"]["skill_root"]) == references.parent.parent
        assert "source_path=" in job.prompt
        assert "Two persistent conversations" not in job.prompt
        assert "normal stage deliverable" in payload["instructions"]["recovery"]
        report = job.execution_root / "repaired-plan.md"
        report.write_text("# Source and design\nSynthetic corrected plan.\n")
        submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", "worker")

    port = runner(project)
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker) as model:
        if restart:
            # Simulate interruption after the gateway response was persisted,
            # before its adapter was allowed to consume it.
            with patch.object(port, "_materialize_codex_report", side_effect=KeyboardInterrupt):
                with pytest.raises(KeyboardInterrupt):
                    port._checker_decision(project, stage, pending_decision(project, stage))
            project = open_project(project.root)
            port = runner(project)
            assert port._latest_job_occurrence(project, stage) is not None
            assert (
                port._latest_job_occurrence(project, stage).ordinal
                > json.loads(pending_decision(project, stage).path.read_text())["after_job"]
            )
        port._checker_decision(project, stage, pending_decision(project, stage))
    assert model.call_count == 1
    assert project.stage(stage).status is StageStatus.PASS
    assert pending_decision(project, stage) is None
    open_project(project.root).verify_integrity()


@pytest.mark.parametrize("capture", ["missing", "stale", "current"])
def test_accept_routes_by_capture_state_and_replays_after_restart(tmp_path, capture):
    from unittest.mock import patch
    from tests.migration_support import implemented
    from tests.workflow_support import runner
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.core.checker_decision import request_recovery
    from driver_port_factory.migration.artifact_preparation import ArtifactPreparationService
    from driver_port_factory.migration.contracts import MigrationStage as M, MigrationArtifact as B

    project, worktree, report = implemented(tmp_path)
    stage = M.ARTIFACT_PREPARATION
    project.start(stage)
    runtime = worktree / ".dpf-output/runtime-artifact"
    checker = worktree / ".dpf-output/check-presence.sh"
    checker.write_text("echo attempt >> .dpf-output/check-count\nexit 7\n")
    service = ArtifactPreparationService()
    if capture == "missing":
        # Implementation now produces a smoke image; model a genuinely missing
        # packaging input explicitly rather than relying on the old fixture.
        runtime.unlink()
        with pytest.raises(WorkflowError) as failure:
            service.capture_codex_artifact(project, report)
        with pytest.raises(CheckerDecisionRequired):
            request_recovery(project, stage, failure.value)
    else:
        runtime.write_bytes(b"synthetic runtime")
        with pytest.raises(CheckerDecisionRequired):
            service.capture_codex_artifact(project, report)

    def worker(job):
        if capture != "current":
            runtime.write_bytes(b"repaired synthetic runtime")
            checker.write_text("echo attempt >> .dpf-output/check-count\nexit 0\n")
        report.write_text("Synthetic self-check.\nCurrent files inspected; retain earlier failures.\n")
        submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", "worker")

    port = runner(project)
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker) as model:
        with patch.object(port, "_materialize_codex_report", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                port._checker_decision(project, stage, pending_decision(project, stage))
        project = open_project(project.root)
        port = runner(project)
        port._checker_decision(project, stage, pending_decision(project, stage))
        assert model.call_count == 1
    assert project.stage(stage).status is StageStatus.PASS
    assert pending_decision(project, stage) is None
    assert project.artifacts.read(project.artifact(stage, B.RUNTIME_ARTIFACT)) == runtime.read_bytes()
    attempts = [json.loads(project.artifacts.read(ref)) for ref in
                project.current_artifact_refs(stage=stage)
                if ref.kind == B.ARTIFACT_PREPARATION_ATTEMPT.value]
    assert [item["status"] for item in attempts] == {
        "missing": ["PASS"], "stale": ["FAIL", "PASS"], "current": ["FAIL"]}[capture]
    assert len((worktree / ".dpf-output/check-count").read_text().splitlines()) == len(attempts)
    project.verify_integrity()


def test_report_text_cannot_dispatch_or_change_state(tmp_path):
    from driver_port_factory.migration.review_policy import require_self_review

    report = tmp_path / "report.md"
    report.write_text("DPF_STATUS: BLOCKED\nDPF_REVIEW: REWORK\n")
    require_self_review(report.read_text())
    assert report.read_text().startswith("DPF_STATUS")


@pytest.mark.parametrize("exit_code", [0, 1])
def test_repeated_operation_pauses_across_restart(tmp_path, exit_code):
    from unittest.mock import patch
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.core.checker_decision import (
        RecoveryPaused, guard_operation, resume_recovery,
    )
    from driver_port_factory.migration.contracts import MigrationStage as M
    from tests.migration_support import public_run
    from tests.workflow_support import runner

    project, worktree, report = public_run(tmp_path, self_check=False, exit_code=exit_code)
    calls = []

    def gateway(job):
        calls.append(job)
        report.write_text(f'Request {len(calls)}; same executable inputs.\n')
        submit(project, job, report, kind='report', decision='operation', operation='PUBLIC_QEMU')
        return CodexResult(job.job_id, '', 'worker')

    with patch('driver_port_factory.codex.cli.CodexExecGateway.run', side_effect=gateway):
        with pytest.raises(RecoveryPaused):
            runner(project)._run_project(project)
        assert len(calls) == 4
        reopened = open_project(project.root)
        with pytest.raises(RecoveryPaused):
            runner(reopened)._run_project(reopened)
        assert len(calls) == 4
    resume_recovery(reopened, M.PUBLIC_QEMU_VALIDATION, 'External experiment condition restored')
    assert pending_decision(reopened, M.PUBLIC_QEMU_VALIDATION) is None
    # Same job replay is idempotent; changed executable inputs permit new work.
    for _ in range(5):
        guard_operation(reopened, M.PUBLIC_QEMU_VALIDATION, 100)
    script = worktree / '.dpf-output/public-qemu.sh'
    for ordinal in range(101, 106):
        script.write_text(script.read_text() + f'\n# scenario {ordinal}\n')
        guard_operation(reopened, M.PUBLIC_QEMU_VALIDATION, ordinal)
