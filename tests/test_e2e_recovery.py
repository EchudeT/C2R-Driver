import json


from pathlib import Path


import pytest


def test_persistent_tool_fault_does_not_buy_unlimited_worker_turns(tmp_path):
    from unittest.mock import patch
    from tests.migration_support import accepted
    from tests.workflow_support import runner
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.core.checker_decision import RecoveryPaused
    from driver_port_factory.migration.contracts import MigrationStage as M

    project, _, _ = accepted(tmp_path)
    port = runner(project)

    def failing(_):
        raise WorkflowError("synthetic unrepairable tool failure")

    port._actions[M.COMPLETION_AUDIT] = failing

    def worker(job):
        report = job.execution_root / "repair.md"
        report.write_text("Synthetic attempted repair.\n")
        return CodexResult(job.job_id, f"REPORT_PATH: {report}\n", "worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker) as model:
        with pytest.raises(RecoveryPaused):
            port._run_project(project)
        assert model.call_count == 3
        with pytest.raises(RecoveryPaused):
            port._run_project(open_project(project.root))
        assert model.call_count == 3
        from driver_port_factory.core.checker_decision import resume_recovery
        resume_recovery(project, M.COMPLETION_AUDIT, "operator resolved external fixture prerequisite")
        port._actions[M.COMPLETION_AUDIT] = port._completion_audit
        assert port._run_project(project).status.value == "PASS"
        assert model.call_count == 3


from driver_port_factory.composition import open_project


from driver_port_factory.core.checker_decision import (
    CheckerDecisionRequired,
    accept_decision,
    pending_decision,
)


from driver_port_factory.core.models import StageStatus, WorkflowError


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
    from driver_port_factory.migration.completion_audit import CompletionAuditService

    project, worktree, report = packaged(tmp_path)
    project.start(M.PUBLIC_QEMU_VALIDATION)
    script = worktree / ".dpf-output/public-qemu.sh"
    script.write_text("echo attempted > .dpf-output/attempted.txt\nexit 7\n")
    report.write_text("Synthetic execution request.\nDPF_RUN: PUBLIC_QEMU\n")
    service = PublicQemuService()
    with patch("driver_port_factory.core.execution.shutil.which", return_value=None if tracer == "missing" else "/bin/false") if tracer != "installed" else nullcontext():
        first = service.run_script(project, script_path=script, work_report_path=report)
    assert (worktree / ".dpf-output/attempted.txt").read_text().strip() == "attempted"
    receipt = json.loads(Path(first["attempt"]).read_text())
    assert receipt["runs"][0]["exec_trace"]["collector"]["available"] is (tracer == "installed")
    assert first["status"] == "FAIL"
    # Restarting the same operation must return its failed receipt, not rerun it.
    report.write_text("Corrected description.\nDPF_RUN: PUBLIC_QEMU\n")
    assert service.run_script(open_project(project.root), script_path=script,
                              work_report_path=report) == first
    report.write_text(
        "Synthetic worker self-check; not real driver evidence.\nDPF_SELF_REVIEW: PASS\n"
    )
    with pytest.raises(CheckerDecisionRequired) as caught:
        service.accept_self_review(project, work_report_path=report)
    decision = worktree / ".dpf-output/decision.md"
    decision.write_text(
        "Synthetic acceptance exercising the control path only.\nDPF_CHECKER_DECISION: ACCEPT\n"
    )
    accept_decision(project, M.PUBLIC_QEMU_VALIDATION, caught.value.path, decision)
    audit = CompletionAuditService().run(project)
    assert project.stage(M.COMPLETION_AUDIT).status is StageStatus.PASS
    assert (
        project.load_json_artifact(M.PUBLIC_QEMU_VALIDATION, B.PUBLIC_QEMU_REPORT)[
            "execution_status"
        ]
        == "FAIL"
    )
    assert audit["worker_acceptances"][0]["stage"] == M.PUBLIC_QEMU_VALIDATION.value
    assert audit["failure_attribution"] and audit["unresolved"]
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
            "smallest relevant compiler/preprocessor probe" in payload["instructions"]["objective"]
        )
        assert "normal stage deliverable" in payload["instructions"]["recovery"]
        report = job.execution_root / "repaired-plan.md"
        report.write_text("# Source and design\nSynthetic corrected plan.\nDPF_SELF_REVIEW: PASS\n")
        return CodexResult(job.job_id, f"REPORT_PATH: {report}\n", "worker")

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
        report.write_text("Synthetic self-check.\nDPF_SELF_REVIEW: PASS\n"
                          "Current files inspected; retain earlier failures.\n"
                          "DPF_CHECKER_DECISION: ACCEPT\n")
        return CodexResult(job.job_id, f"REPORT_PATH: {report}\n", "worker")

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


@pytest.mark.parametrize(
    "failure",
    [
        WorkflowError("C compiler effective-target probe failed: unknown GCC option"),
        WorkflowError("unrecognized future tool diagnostic"),
        OSError("executable unavailable"),
        ValueError("invalid tool result encoding"),
    ],
)
def test_execution_failure_uses_durable_worker_recovery_without_rollback(tmp_path, failure):
    from unittest.mock import patch
    from tests.migration_support import accepted
    from tests.workflow_support import runner
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.codex.contracts import ModelInvocationError
    from driver_port_factory.migration.contracts import MigrationStage as M

    project, _, _ = accepted(tmp_path)
    port = runner(project)
    final = port._actions[M.COMPLETION_AUDIT]
    prior = [(s.name, s.status) for s in project.stages() if s.name is not M.COMPLETION_AUDIT]
    port._actions[M.COMPLETION_AUDIT] = lambda p: (_ for _ in ()).throw(failure)
    # A transport failure must pause, preserving the pending recovery for restart.
    with patch(
        "driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=WorkflowError("offline")
    ) as offline:
        with pytest.raises(ModelInvocationError, match="offline"):
            port._run_project(project)
    assert offline.call_count == 1
    pending = pending_decision(open_project(project.root), M.COMPLETION_AUDIT)
    assert pending is not None
    assert json.loads(pending.path.read_text())["artifacts"] is None
    report = project.root / "bad-accept.md"
    report.write_text("No outputs.\nDPF_CHECKER_DECISION: ACCEPT\n")
    with pytest.raises(WorkflowError, match="before outputs"):
        accept_decision(project, M.COMPLETION_AUDIT, pending.path, report)

    def worker(job):
        payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
        assert str(failure) in payload["reference_material"]["checker_findings"]
        assert "artifacts: null" in payload["instructions"]["objective"]
        decision = job.execution_root / "recovery.md"
        decision.write_text("Synthetic local repair completed; rerun the static operation.\n"
                            "DPF_CHECKER_DECISION: ACCEPT\n")
        return CodexResult(job.job_id, f"REPORT_PATH: {decision}\n", "worker")

    resumed = runner(project)
    resumed._actions[M.COMPLETION_AUDIT] = final
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=worker) as model:
        outcome = resumed._run_project(open_project(project.root))
    assert outcome.status is StageStatus.PASS
    assert model.call_count == 1
    assert [
        (s.name, s.status) for s in project.stages() if s.name is not M.COMPLETION_AUDIT
    ] == prior
    open_project(project.root).verify_integrity()
def test_report_history_cannot_dispatch_or_override_current_action(tmp_path):
    from driver_port_factory.core.models import WorkflowError
    from driver_port_factory.migration.repair_routing import (
        PrerequisiteRepair, WorkerBlocked, repair_target,
    )
    from driver_port_factory.orchestration.protocol import operation
    from driver_port_factory.port import PortRunner

    history = (
        "Prior evidence:\nDPF_RUN: PUBLIC_QEMU\nDPF_STATUS: BLOCKED\n"
        "DPF_REPAIR_STAGE: driver_implementation\nDPF_REVIEW: REWORK\n"
    )
    report = tmp_path / "report.md"
    for footer in ("DPF_SELF_REVIEW: PASS", "DPF_CHECKER_DECISION: ACCEPT"):
        report.write_text(history + footer + "\n")
        assert operation("public_qemu_validation", report.read_text()) is None
        PortRunner._report_outcome(report)
    report.write_text(history + "DPF_STATUS: BLOCKED\n")
    with pytest.raises(WorkerBlocked):
        PortRunner._report_outcome(report)
    report.write_text(history + "DPF_REPAIR_STAGE: artifact_preparation\nDPF_REVIEW: REWORK\n")
    assert repair_target(report.read_text()).value == "artifact_preparation"
    with pytest.raises(PrerequisiteRepair) as error:
        PortRunner._report_outcome(report)
    assert error.value.target.value == "artifact_preparation"
    assert operation("public_qemu_validation", history + "DPF_RUN: PUBLIC_QEMU\n") == "PUBLIC_QEMU"
    for stage, footer in (("public_qemu_validation", "DPF_RUN: UNKNOWN"),
                          ("driver_implementation", "DPF_RUN: PUBLIC_QEMU"),
                          ("public_qemu_validation", "- DPF_RUN: PUBLIC_QEMU")):
        with pytest.raises(WorkflowError):
            operation(stage, history + footer)
