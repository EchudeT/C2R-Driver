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


from driver_port_factory.composition import open_project


from driver_port_factory.core.checker_decision import (
    CheckerDecisionRequired,
    accept_decision,
    pending_decision,
)


from driver_port_factory.core.models import StageStatus, WorkflowError


def test_public_acceptance_reaches_completion_without_rejecting_same_receipt(tmp_path):
    from tests.migration_support import packaged
    from driver_port_factory.migration.contracts import MigrationStage as M, MigrationArtifact as B
    from driver_port_factory.migration.public_qemu import PublicQemuService
    from driver_port_factory.migration.completion_audit import CompletionAuditService

    project, worktree, report = packaged(tmp_path)
    project.start(M.PUBLIC_QEMU_VALIDATION)
    script = worktree / ".dpf-output/public-qemu.sh"
    script.write_text("exit 7\n")
    report.write_text("Synthetic execution request.\nDPF_RUN: PUBLIC_QEMU\n")
    service = PublicQemuService()
    assert (
        service.run_script(project, script_path=script, work_report_path=report)["status"] == "FAIL"
    )
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
        decision.write_text("Synthetic local repair completed; rerun the static operation.\n")
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
