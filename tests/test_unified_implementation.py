"""Offline synthetic sources/model, real controller/executor; no driver certification."""

import json
from dataclasses import replace
from unittest.mock import patch

import pytest

from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.composition import initialize_project, open_project
from driver_port_factory.core.models import EvaluationMode, ProjectConfig, StageStatus
from driver_port_factory.migration.contracts import MigrationArtifact as A
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.orchestration.migration import migration_workflow
from tests.submission_support import submit
from tests.test_optional_reviews import config
from tests.workflow_support import ready_implementation, runner


@pytest.mark.parametrize("mode", list(EvaluationMode))
def test_one_gate_owns_all_six_outputs_and_no_dangling_dependencies(mode):
    settings = replace(config(mode=mode), unified_implementation=True)
    specs = migration_workflow(settings)
    names = {s.name for s in specs}
    assert S.TARGET_FRAMEWORK_ENABLEMENT not in names
    implementation = next(s for s in specs if s.name is S.DRIVER_IMPLEMENTATION)
    assert {r.kind for r in implementation.required_outputs} == {
        A.TARGET_FRAMEWORK_BUNDLE,
        A.TARGET_FRAMEWORK_REPORT,
        A.TARGET_FRAMEWORK_CHANGE_INVENTORY,
        A.IMPLEMENTATION_BUNDLE,
        A.COMPLIANCE_REPORT,
        A.TARGET_CHANGE_INVENTORY,
    }
    assert all(set(s.dependencies) <= names and s.name not in s.dependencies for s in specs)
    assert S.PUBLIC_QEMU_VALIDATION in names
    assert S.FINAL_EVIDENCE_REVIEW in names


def test_new_default_and_old_config_select_distinct_frozen_dags():
    value = config().to_dict()
    value.pop("unified_implementation")
    assert ProjectConfig.from_dict(value).unified_implementation is False
    value.pop("evaluation_mode")
    value.pop("actor_role")
    fresh = ProjectConfig(
        **value, evaluation_mode=config().evaluation_mode, actor_role=config().actor_role
    )
    assert fresh.unified_implementation is True
    assert len(migration_workflow(fresh)) + 1 == len(migration_workflow(config()))


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("fail_first", [False, True])
def test_unified_delivery_recovers_without_handoff_and_keeps_public_acceptance(
    tmp_path, review, fail_first
):
    def initialize(root, settings):
        return initialize_project(
            root,
            replace(
                settings,
                unified_implementation=True,
                enable_analysis_review=False,
                enable_final_evidence_review=review,
            ),
        )

    with patch("tests.knowledge_support.initialize_project", side_effect=initialize):
        project = ready_implementation(tmp_path)
    calls = []
    implementation_calls = 0

    def gateway(job):
        nonlocal implementation_calls
        calls.append(job)
        output = job.execution_root / ".dpf-output"
        output.mkdir(exist_ok=True)
        report = output / "report.md"
        report.write_text("Synthetic executor fixture.\nDPF_SELF_REVIEW: PASS\n")
        if job.stage is S.FINAL_EVIDENCE_REVIEW:
            assert job.thread_id is None
            submit(project, job, report, kind="report", decision="pass")
            return CodexResult(job.job_id, "", "independent-review")
        assert job.stage is S.DRIVER_IMPLEMENTATION
        implementation_calls += 1
        if implementation_calls > 1:
            assert job.thread_id == "worker"
            assert project.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.RUNNING
            assert not any(
                ref.kind == A.TARGET_FRAMEWORK_BUNDLE.value
                for ref in project.current_artifact_refs(stage=S.DRIVER_IMPLEMENTATION)
            )
            assert (job.execution_root / "driver.rs").is_file()
        (job.execution_root / "driver.rs").write_text("pub fn init() {}\n")
        integration = job.execution_root / "src/driver-api.rs"
        if implementation_calls == 1:
            integration.write_text(integration.read_text() + "pub fn framework_api() {}\n")
        from tests.migration_support import smoke_fixture

        smoke_fixture(job.execution_root)
        if fail_first and implementation_calls == 1:
            (output / "implementation-smoke.sh").write_text("exit 1\n")
        else:
            qemu = output / "qemu-system-fixture"
            qemu.symlink_to("/bin/true")
            (output / "public-qemu.sh").write_text(
                f'"{qemu}" -kernel "$DPF_RUNTIME_ARTIFACT"\n'
                "echo synthetic > .dpf-output/qemu-runs/serial.log\n"
            )
            from driver_port_factory.migration.experiment_ack import acknowledge, record_seen
            from driver_port_factory.migration.experiments import execute

            observation = execute(
                project,
                worktree=job.execution_root,
                script_path=output / "public-qemu.sh",
                runtime_path=output / "runtime-artifact",
                timeout_seconds=3600,
            )
            assert observation.passed
            record_seen(
                project,
                job.job_id,
                [{"id": "public-qemu.sh", "status": "PASS", "observation": observation}],
            )
            acknowledge(project, job.job_id, report)
        submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", "worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        result = runner(project)._run_project(project)
    assert result.status is StageStatus.PASS
    assert implementation_calls == (2 if fail_first else 1)
    assert len(calls) == implementation_calls + int(review)
    framework = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.TARGET_FRAMEWORK_BUNDLE)
    driver = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    assert all(framework[key] == driver[key] for key in ("files", "work_report", "target_worktree"))
    assert {f["path"] for f in driver["files"]} >= {"driver.rs", "src/driver-api.rs"}
    public = project.load_json_artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
    assert public["run"]["execution_status"] == "PASS"
    receipts = list(project.root.rglob("implementation-smoke/*/receipt.json"))
    statuses = [json.loads(path.read_text())["status"] for path in receipts]
    assert "PASS" in statuses
    assert ("FAIL" in statuses) is fail_first
    open_project(project.root).verify_integrity()
