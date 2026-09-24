"""Current controller policy, with synthetic sources and execution, never driver certification."""

import json


from unittest.mock import patch


import pytest


from driver_port_factory.codex.gateway import CodexResult


from driver_port_factory.composition import open_project


from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S


from tests.workflow_support import ready_implementation, runner
from tests.submission_support import submit


@pytest.mark.parametrize("risk", [False, True])
@pytest.mark.parametrize("append_decision", [False, True])
def test_two_conversations_complete_with_independent_review(tmp_path, risk, append_decision):
    project = ready_implementation(tmp_path, reviewed=False)
    port = runner(project)
    calls = []

    def gateway(job):
        calls.append(job)
        stage = job.stage
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        text = "Synthetic controller fixture, not real driver evidence.\n"
        if stage is S.ANALYSIS_REVIEW:
            assert job.thread_id is None
            assert job.sandbox.value == "danger-full-access"
            report.write_text("Synthetic analysis evidence checked.\n")
            submit(project, job, report, kind="report", decision="pass")
            return CodexResult(job.job_id, "", "reviewer")
        elif stage is S.TARGET_FRAMEWORK_ENABLEMENT:
            report.write_text(
                "Synthetic target framework enablement; no target edits required.\n"
                "DPF_SELF_REVIEW: PASS\n"
            )
        elif stage is S.DRIVER_IMPLEMENTATION:
            assert project.stage(S.ANALYSIS_REVIEW).status.value == "PASS"
            (job.execution_root / "driver.rs").write_text(
                "pub unsafe fn init() {}\n" if risk else "pub fn init() {}\n"
            )
            # Ordinary preexisting integration changes do not create a second audit.
            integration = job.execution_root / "src/driver-api.rs"
            integration.write_text(integration.read_text() + "pub fn register_driver() {}\n")
        elif stage is S.ARTIFACT_PREPARATION:
            variants = output / "harness/variants"
            variants.mkdir(parents=True, exist_ok=True)
            (variants / "fault-image").write_bytes(b"synthetic instrumented image")
            (output / "runtime-artifact").write_bytes(
                (job.execution_root / "driver.rs").read_bytes()
            )
            (output / "check-presence.sh").write_text(
                'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n'
            )
        elif stage is S.PUBLIC_QEMU_VALIDATION:
            ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
            (output / "qemu-runs").mkdir(exist_ok=True)
            qemu = output / "qemu-system-fixture"
            if not qemu.exists():
                qemu.symlink_to("/bin/true")
            (output / "public-qemu.sh").write_text(
                f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                f'"{qemu}" -kernel "{output / "harness/variants/fault-image"}"\n'
                "echo synthetic > .dpf-output/qemu-runs/serial.log\n"
            )
        elif stage is S.FINAL_EVIDENCE_REVIEW:
            assert job.thread_id == "reviewer"
            assert job.sandbox.value == "danger-full-access"
            assert "frozen contract/test IDs" in job.prompt
            payload = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])
            reference_material = payload["reference_material"]
            frozen_inputs = reference_material["frozen_inputs"]
            assert "migration_handoff" not in frozen_inputs
            assert "target_platform_study" not in frozen_inputs
            assert "migration_contracts" not in frozen_inputs
            assert "test_port_matrix" not in frozen_inputs
            assert "implementation_snapshot" in reference_material
            assert "frozen_acceptance_oracles" in reference_material
            assert "analysis_review_path" not in reference_material
            assert "target-platform-study.md" not in job.prompt
            assert "knowledge-contract.md" not in job.prompt
            assert "translation.md" not in job.prompt
            assert "test-porting.md" not in job.prompt
            assert "target-changes.md" not in job.prompt
            report.write_text(
                "Synthetic independent review of fixtures, not driver certification.\n"
            )
            submit(project, job, report, kind="report", decision="pass")
            return CodexResult(job.job_id, "", "reviewer")
        else:
            raise AssertionError(f"unexpected paid stage: {stage}")
        if stage is S.PUBLIC_QEMU_VALIDATION and "controller_execution" not in ctx:
            text += "Prior local self-check.\n"
        elif stage in (S.DRIVER_IMPLEMENTATION, S.PUBLIC_QEMU_VALIDATION):
            if stage is S.PUBLIC_QEMU_VALIDATION:
                from driver_port_factory.migration.public_qemu import PublicQemuService

                report.write_text("Report-only clarification.\n")
                service = PublicQemuService()
                previous = service._latest_attempt(project)[0].digest
                service.run_script(
                    project, script_path=output / "public-qemu.sh", work_report_path=report
                )
                assert service._latest_attempt(project)[0].digest == previous
                text += "Previous request captured and inspected.\n"
        report.write_text(text)
        thread = "worker"
        assert job.thread_id in (None, thread)
        if stage is S.PUBLIC_QEMU_VALIDATION:
            decision = "pass" if "controller_execution" in ctx else "operation"
            submit(
                project,
                job,
                report,
                kind="report",
                decision=decision,
                operation=None if decision == "pass" else "PUBLIC_QEMU",
            )
        elif stage in (
            S.TARGET_FRAMEWORK_ENABLEMENT,
            S.DRIVER_IMPLEMENTATION,
            S.ARTIFACT_PREPARATION,
        ):
            submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", thread)

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        # Use the real controller loop, stage finalizers, script execution, CAS and ledger.
        outcome = port._run_project(project)
    assert outcome.status.value == "PASS"
    assert outcome.stage == S.FINAL_EVIDENCE_REVIEW.value
    from pathlib import Path

    assert Path(outcome.report_path).read_text().strip()
    assert [job.stage for job in calls].count(S.DRIVER_IMPLEMENTATION) == 1
    assert S.COMPLETION_AUDIT.value not in project.workflow.stage_values
    assert [job.stage for job in calls].count(S.FINAL_EVIDENCE_REVIEW) == 1
    assert calls[0].stage is S.ANALYSIS_REVIEW
    worker = [job for job in calls if job.stage not in {S.FINAL_EVIDENCE_REVIEW, S.ANALYSIS_REVIEW}]
    assert len(worker) == 5
    from driver_port_factory.control.statistics import project_statistics

    stats = project_statistics(project)
    groups = stats["by_call_reason"]
    assert groups["stage_work"]["codex_calls"] == 4
    assert groups["execution_self_check"]["codex_calls"] == 1
    assert groups["independent_review"]["codex_calls"] == 1
    assert groups["review_followup"]["codex_calls"] == 1
    assert sum(g["codex_calls"] for g in groups.values()) == stats["totals"]["codex_calls"]
    metric_path = next((project.control / "codex").glob("*.metrics.json"))
    original_metric = metric_path.read_text()
    old_metric = json.loads(original_metric)
    old_metric.pop("call_reason")
    metric_path.write_text(json.dumps(old_metric))
    assert project_statistics(project)["by_call_reason"]["unknown"]["codex_calls"] == 1
    metric_path.write_text(original_metric)
    assert worker[0].thread_id is None
    assert all(job.thread_id == "worker" for job in worker[1:])
    reopened = open_project(project.root)
    review = reopened.load_json_artifact(S.FINAL_EVIDENCE_REVIEW, A.FINAL_EVIDENCE_REVIEW_REPORT)
    assert review["review_mode"] == "independent"
    public = reopened.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
    assert public["runs"][0]["attribution"] == "PUBLIC_HARNESS"
    variants = public["runs"][0]["runtime_variants"]
    assert variants[".dpf-output/harness/variants/fault-image"]["observed_boot"] is True
    variant = variants[".dpf-output/harness/variants/fault-image"]["artifact"]
    assert (
        reopened.artifacts.path_for_digest(variant["digest"]).read_bytes()
        == b"synthetic instrumented image"
    )
    reopened.verify_integrity()


@pytest.mark.parametrize("blocked", [False, True])
def test_reviewer_keeps_its_thread_and_returns_defects_to_worker(tmp_path, blocked):
    from tests.migration_support import public_run
    from driver_port_factory.core.models import StageStatus

    project, worktree, _ = public_run(tmp_path)
    port = runner(project)
    calls = []
    reviews = 0
    prior = [
        (s.name, s.status)
        for s in project.stages()
        if s.position < project.stage(S.PUBLIC_QEMU_VALIDATION).position
    ]

    def gateway(job):
        nonlocal reviews
        calls.append(job)
        report = (
            job.execution_root / "review.md"
            if job.stage is S.FINAL_EVIDENCE_REVIEW
            else worktree / ".dpf-output/repair.md"
        )
        if job.stage is S.FINAL_EVIDENCE_REVIEW:
            assert job.thread_id == (None if reviews == 0 else "reviewer")
            reviews += 1
            if blocked:
                text = "Required external resource unavailable; no functional pass.\n"
            elif reviews == 1:
                text = "Synthetic oracle defect; fix the public harness only.\n"
            else:
                assert "previous_review" in job.prompt
                text = "Synthetic repaired evidence inspected; no new requirements.\n"
            thread = "reviewer"
        else:
            assert job.stage is S.PUBLIC_QEMU_VALIDATION
            ctx = json.loads(job.prompt.split("<job>")[1].split("</job>")[0])["reference_material"]
            assert job.thread_id in (None, "worker")
            if "controller_execution" not in ctx:
                script = worktree / ".dpf-output/public-qemu.sh"
                script.write_text(
                    script.read_text() + "echo repaired > .dpf-output/qemu-runs/repair.log\n"
                )
                text = "Synthetic harness repaired.\n"
            else:
                text = "Synthetic execution inspected.\n"
            thread = "worker"
        report.write_text(text)
        if job.stage is S.FINAL_EVIDENCE_REVIEW:
            if blocked:
                submit(project, job, report, kind="report", decision="blocked")
            elif reviews == 1:
                submit(
                    project,
                    job,
                    report,
                    kind="report",
                    decision="rework",
                    repair_stage="public_qemu_validation",
                )
            else:
                submit(project, job, report, kind="report", decision="pass")
        else:
            decision = "pass" if "controller_execution" in ctx else "operation"
            submit(
                project,
                job,
                report,
                kind="report",
                decision=decision,
                operation=None if decision == "pass" else "PUBLIC_QEMU",
            )
        return CodexResult(job.job_id, "", thread)

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        outcome = port._run_project(project)
    assert outcome.status is (StageStatus.BLOCKED if blocked else StageStatus.PASS)
    assert reviews == (1 if blocked else 2)
    from driver_port_factory.control.statistics import project_statistics

    groups = project_statistics(project)["by_call_reason"]
    assert groups["independent_review"]["codex_calls"] == 1
    if not blocked:
        assert groups["review_followup"]["codex_calls"] == 1
        assert groups["repair"]["codex_calls"] == 1
    assert len(calls) == (1 if blocked else 4)
    assert [(project.stage(s).name, project.stage(s).status) for s, _ in prior] == prior
    assert S.COMPLETION_AUDIT.value not in project.workflow.stage_values
    open_project(project.root).verify_integrity()


def test_public_qemu_repair_progress_tracks_current_harness(tmp_path):
    from driver_port_factory.migration.repair_routing import retry_prerequisite
    from tests.migration_support import public_run

    project, worktree, _ = public_run(tmp_path)
    script = worktree / ".dpf-output/public-qemu.sh"

    with patch.object(project, "retry_from") as retry:
        retry_prerequisite(
            project,
            S.PUBLIC_QEMU_VALIDATION,
            trigger=S.FINAL_EVIDENCE_REVIEW,
            reason="synthetic review finding",
        )
    before = retry.call_args.kwargs["progress"]

    script.write_text(script.read_text() + "echo substantive-harness-repair\n")
    with patch.object(project, "retry_from") as retry:
        retry_prerequisite(
            project,
            S.PUBLIC_QEMU_VALIDATION,
            trigger=S.FINAL_EVIDENCE_REVIEW,
            reason="same synthetic review finding",
        )
    after = retry.call_args.kwargs["progress"]

    assert before["script"] == after["script"]
    assert before["current_script"] != after["current_script"]
    assert before["current_helpers"] == after["current_helpers"]


def test_review_reuse_requires_current_rules(tmp_path):
    import shutil
    from pathlib import Path
    from driver_port_factory.codex.prompts import default_prompt_pack_path
    from driver_port_factory.migration.final_evidence_review import FinalEvidenceReviewService
    from driver_port_factory.core.models import WorkflowError
    from tests.migration_support import accepted

    pack = tmp_path / "prompt-pack"
    shutil.copytree(default_prompt_pack_path(), pack)
    (tmp_path / "run").mkdir()
    with patch("driver_port_factory.codex.prompts.default_prompt_pack_path", return_value=pack):
        project, _, report = accepted(tmp_path / "run")
        service = FinalEvidenceReviewService()
        original_policy = service.policy_digest(project)
        assert service.reusable_review(project) is not None
        manifest = pack / "manifest.json"
        value = json.loads(manifest.read_text())
        value["stages"]["driver_implementation"]["objective"] += " Unrelated clarification."
        manifest.write_text(json.dumps(value))
        assert service.reusable_review(project) is not None
        value["stages"]["final_evidence_review"]["objective"] += " Additional required evidence."
        manifest.write_text(json.dumps(value))
        assert service.reusable_review(project) is None
        with pytest.raises(WorkflowError, match="rules changed"):
            service.finalize(project, review_path=report, policy_digest=original_policy)
        value["stages"]["final_evidence_review"]["objective"] = value["stages"][
            "final_evidence_review"
        ]["objective"].removesuffix(" Additional required evidence.")
        manifest.write_text(json.dumps(value))
        assert service.reusable_review(project) is not None
        skill = (
            Path(project.config.skill_root)
            / "knowledge-guided-driver-port/references/qemu-evidence.md"
        )
        skill.write_text(skill.read_text() + "\nChanged evidence requirements.\n")
        assert service.reusable_review(project) is None
        # Rule edits invalidate future reuse, not the integrity of historical evidence.
        open_project(project.root).verify_integrity()


def test_final_review_identity_ignores_derived_qemu_receipt_fields():
    from copy import deepcopy
    from driver_port_factory.migration.final_evidence_review import _public_evidence_digest

    report = {
        "schema_version": 4,
        "inputs": {"runtime_artifact": {"digest": "runtime"}},
        "recorded_at": "first",
        "attempt_sha256": "attempt-one",
        "self_review_sha256": "worker-one",
        "current_run": 0,
        "runs": [
            {
                "request_job": 4,
                "runtime_artifact": {"sha256": "runtime"},
                "script": {"sha256": "script"},
                "helper_inputs": {},
                "request_report": {"sha256": "derived-worker-report"},
                "exec_trace": {"qemu_execs": ["qemu"], "runtime_bound": True},
            }
        ],
    }
    refreshed = deepcopy(report)
    refreshed["recorded_at"] = "second"
    refreshed["attempt_sha256"] = "attempt-two"
    refreshed["self_review_sha256"] = "worker-two"
    refreshed["runs"][0]["request_report"]["sha256"] = "new-derived-worker-report"

    assert _public_evidence_digest(refreshed) == _public_evidence_digest(report)


def test_latest_public_qemu_run_does_not_assume_first_run():
    import pytest

    from driver_port_factory.core.models import WorkflowError
    from driver_port_factory.migration.public_qemu import latest_public_qemu_run

    report = {
        "current_run": 1,
        "runs": [
            {"request_job": 3, "runtime_artifact": {"sha256": "old"}},
            {"request_job": 8, "runtime_artifact": {"sha256": "current"}},
        ],
    }
    assert latest_public_qemu_run(report)["runtime_artifact"]["sha256"] == "current"
    with pytest.raises(WorkflowError, match="explicit current_run"):
        latest_public_qemu_run({"runs": report["runs"]})


def test_prepared_repair_ignores_report_only_refresh(tmp_path):
    from driver_port_factory.migration.repair_execution import prepared, record
    from tests.migration_support import public_run

    project, _, report = public_run(tmp_path)
    record(project, report)
    assert prepared(project) == report.resolve()

    report.write_text("Refreshed human-readable receipt; substantive files unchanged.\n")
    assert prepared(project) == report.resolve()


def test_public_qemu_recovery_inputs_ignore_artifact_receipts(tmp_path):
    from driver_port_factory.core.checker_decision import stage_inputs
    from tests.migration_support import public_run

    project, _, _ = public_run(tmp_path)
    inputs = stage_inputs(project, S.PUBLIC_QEMU_VALIDATION)
    kinds = {item["kind"] for item in inputs}
    assert "artifact_preparation_attempt" not in kinds
    assert "compliance_report" not in kinds
    identity = next(item for item in inputs if item["kind"] == "artifact_identity")
    assert "semantic_sha256" in identity
    assert "digest" not in identity
