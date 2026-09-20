"""Current controller policy, with synthetic sources and execution, never driver certification."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from driver_port_factory.codex.contracts import CodexOutputError
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.composition import open_project
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from driver_port_factory.migration.public_repair import PublicRepairService, validate_public_repair_bundle
from driver_port_factory.migration.review_policy import require_self_review
from tests.migration_support import public_run, accepted
from tests.test_workflow_alignment import ready_implementation, runner


@pytest.mark.parametrize("text", ["", "Looks fine", "DPF_REVIEW: PASS", "Failed\nDPF_SELF_REVIEW: FAIL"])
def test_missing_self_check_does_not_advance(text):
    with pytest.raises(CodexOutputError):
        require_self_review(text)


@pytest.mark.parametrize("implementation,kind", [
    ({"source": "pub unsafe fn init() {}\n"}, "unsafe_boundary"),
    ({"request": "DPF_INDEPENDENT_REVIEW: verify IRQ ownership premise"}, "worker_request"),
])
def test_required_independent_review_cannot_be_replaced_by_worker(tmp_path, implementation, kind):
    project, worktree, _ = public_run(tmp_path, **implementation)
    decision = PublicRepairService.decision(project)
    assert decision["independent_required"]
    assert any(item["kind"] == kind for item in decision["reasons"])
    with pytest.raises(WorkflowError, match="independent review is required"):
        PublicRepairService().finalize(project)
    report = worktree / ".dpf-output/review.md"
    report.write_text("Inspected triggered paths.\nDPF_REVIEW: PASS\n")
    PublicRepairService().finalize(project, review_path=report)
    assert project.load_json_artifact(S.PUBLIC_REPAIR, A.PUBLIC_REPAIR_REPORT)["review_mode"] == "independent"
    project.verify_integrity()
    project.start(S.COMPLETION_AUDIT)
    project.retry_from(S.PUBLIC_REPAIR, trigger=S.COMPLETION_AUDIT,
                       reason="verify unchanged review reuse")
    port = runner(project)
    with patch.object(port, "_codex", side_effect=AssertionError("unchanged risks must reuse review")):
        port._public_repair(project)
    assert project.load_json_artifact(S.PUBLIC_REPAIR, A.PUBLIC_REPAIR_REPORT)["reused_review_sha256"]


def test_review_digest_and_decision_remain_bound(tmp_path):
    project, _, _ = accepted(tmp_path)
    report = project.load_json_artifact(S.PUBLIC_REPAIR, A.PUBLIC_REPAIR_REPORT)
    inputs = {kind: (project.artifact(stage, kind), project.artifacts.read(project.artifact(stage, kind)))
              for stage, kind in ((S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT),
                                  (S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_WORK_REPORT),
                                  (S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE),
                                  (S.DRIVER_IMPLEMENTATION, A.COMPLIANCE_REPORT),
                                  (S.CONTRACTS, A.CONTRACTS), (S.CONTRACTS, A.TEST_PORT_MATRIX))}
    context = SimpleNamespace(project_root=project.root,
        one_current=lambda _: (None, json.dumps(report).encode()),
        one_dependency=lambda kind: inputs[kind])
    validate_public_repair_bundle(context)
    report["review"]["text"] = "tampered"
    with pytest.raises(WorkflowError, match="content changed"):
        validate_public_repair_bundle(context)
    report = project.load_json_artifact(S.PUBLIC_REPAIR, A.PUBLIC_REPAIR_REPORT)
    report["decision"]["reasons"] = [{"kind": "invented"}]
    with pytest.raises(WorkflowError, match="detached"):
        validate_public_repair_bundle(context)


def test_resolved_blocker_reopens_without_replaying_old_answer(tmp_path):
    from driver_port_factory.codex.contracts import CodexArtifact
    from driver_port_factory.core.models import GeneratedArtifact, StageStatus

    project = ready_implementation(tmp_path)
    project.start(S.DRIVER_IMPLEMENTATION)
    project.record_artifact(S.DRIVER_IMPLEMENTATION,
        GeneratedArtifact(CodexArtifact.JOB_RESULT, b"old blocked reply", "fixture"))
    project.complete(S.DRIVER_IMPLEMENTATION, StageStatus.BLOCKED, message="tool unavailable")
    reopened = open_project(project.root)
    reopened.reopen_blocked(S.DRIVER_IMPLEMENTATION, reason="tool installed")
    assert reopened.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.READY
    assert not reopened.current_artifact_refs(stage=S.DRIVER_IMPLEMENTATION)
    assert "tool installed" in reopened.retry_feedback(S.DRIVER_IMPLEMENTATION)["reason"]
    reopened.verify_integrity()


def test_identical_prerequisite_budget_survives_restart(tmp_path):
    from driver_port_factory.core.models import FileArtifact, StageStatus

    project = ready_implementation(tmp_path)
    outputs = tuple(FileArtifact(kind, project.artifacts.path_for_digest(
        project.artifact(S.CONTRACTS, kind).digest)) for kind in (A.CONTRACTS, A.TEST_PORT_MATRIX))
    for attempt in range(3):
        project.start(S.DRIVER_IMPLEMENTATION)
        project.retry_from(S.CONTRACTS, trigger=S.DRIVER_IMPLEMENTATION,
                           reason=f"same missing premise; new report-{attempt}.md")
        project = open_project(project.root)
        project.start(S.CONTRACTS)
        project.finalize_stage(S.CONTRACTS, outputs)
    project.start(S.DRIVER_IMPLEMENTATION)
    project.retry_from(S.CONTRACTS, trigger=S.DRIVER_IMPLEMENTATION, reason="same missing premise")
    assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.BLOCKED
    assert project.stage(S.CONTRACTS).status is StageStatus.PASS
    project.verify_integrity()


@pytest.mark.parametrize("risk,repair", [(False, False), (True, False), (True, True)])
def test_worker_to_completion_with_only_scoped_optional_review(tmp_path, risk, repair):
    project = ready_implementation(tmp_path)
    port = runner(project)
    calls = []
    review_calls = 0
    original_feedback = "Guest entrypoint prerequisite"

    def gateway(job):
        nonlocal review_calls
        calls.append(job)
        stage = job.stage
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        text = "Synthetic controller fixture, not real driver evidence.\n"
        if stage is S.DRIVER_IMPLEMENTATION:
            (job.execution_root / "driver.rs").write_text(
                "pub unsafe fn init() {}\n" if risk else "pub fn init() {}\n")
            # Ordinary preexisting integration changes do not create a second audit.
            integration = job.execution_root / "src/driver-api.rs"
            integration.write_text(integration.read_text() + "pub fn register_driver() {}\n")
        elif stage is S.ARTIFACT_PREPARATION:
            (output / "runtime-artifact").write_bytes((job.execution_root / "driver.rs").read_bytes())
            (output / "check-presence.sh").write_text(
                'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n')
            if review_calls:
                ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["context"]
                assert original_feedback in Path(ctx["runtime_review_path"]).read_text()
                assert ctx["repair_state"]["stage"] == S.ARTIFACT_PREPARATION.value
                assert ctx["repair_state"]["trigger"] == S.PUBLIC_REPAIR.value
                assert ctx["repair_state"]["status"] == "OPEN"
        elif stage is S.PUBLIC_QEMU_VALIDATION:
            ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["context"]
            (output / "qemu-runs").mkdir(exist_ok=True)
            qemu = output / "qemu-system-fixture"
            if not qemu.exists():
                qemu.symlink_to("/bin/true")
            (output / "public-qemu.sh").write_text(
                f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                'echo synthetic > .dpf-output/qemu-runs/serial.log\n')
        elif stage is S.PUBLIC_REPAIR:
            review_calls += 1
            ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["context"]
            assert any(reason["kind"] == "unsafe_boundary" and reason["path"] == "driver.rs"
                       and reason["scope"] == "init"
                       for reason in ctx["review_decision"]["reasons"])
            text += (original_feedback + "\nDPF_REPAIR_STAGE: artifact_preparation\nDPF_REVIEW: REWORK\n"
                     if repair and review_calls == 1 else "DPF_REVIEW: PASS\n")
        else:
            raise AssertionError(f"unexpected paid stage: {stage}")
        if stage is S.PUBLIC_QEMU_VALIDATION and "controller_execution" not in ctx:
            text += "DPF_RUN: PUBLIC_QEMU\n"
        elif stage in (S.DRIVER_IMPLEMENTATION, S.PUBLIC_QEMU_VALIDATION) or (
                stage is S.ARTIFACT_PREPARATION and review_calls):
            text += "DPF_SELF_REVIEW: PASS\n"
        report.write_text(text)
        thread = "reviewer" if stage is S.PUBLIC_REPAIR else "worker"
        assert job.thread_id in (None, thread)
        return CodexResult(job.job_id, f"REPORT_PATH: {report}\n", thread)

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        # Use the real controller loop, stage finalizers, script execution, CAS and ledger.
        outcome = port._run_project(project)
    assert outcome.status.value == "PASS"
    assert [job.stage for job in calls].count(S.DRIVER_IMPLEMENTATION) == 1
    assert review_calls == (2 if repair else int(risk))
    worker = [job for job in calls if job.stage is not S.PUBLIC_REPAIR]
    assert len(worker) == (6 if repair else 4)
    assert worker[0].thread_id is None
    assert all(job.thread_id == "worker" for job in worker[1:])
    reopened = open_project(project.root)
    audit = reopened.load_json_artifact(S.COMPLETION_AUDIT, A.EVIDENCE_AUDIT)
    assert audit["work_products"]["review_mode"] == ("independent" if risk else "worker_self_check")
    assert audit["failure_attribution"] == []
    assert audit["scope_limits"]["real_hardware"] == "NOT_RUN"
    reopened.verify_integrity()
