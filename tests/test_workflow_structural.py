"""Structural cost regressions, using synthetic sources and no model requests."""
import subprocess
from unittest.mock import patch

import pytest

from driver_port_factory.codex.contracts import CodexSandbox
from driver_port_factory.codex.gateway import CodexExecGateway, CodexJob
from driver_port_factory.core.models import ActorRole
from driver_port_factory.migration.contracts import MigrationStage as S


def test_gateway_preserves_recovered_cargo_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "proven-cache"))
    job = CodexJob(S.TARGET_FRAMEWORK_ENABLEMENT, ActorRole.DEVELOPER,
                   "fixture", "fixture", tmp_path, CodexSandbox.UNRESTRICTED)
    with patch("driver_port_factory.codex.gateway.relay_overrides", return_value=[]), patch(
        "driver_port_factory.codex.gateway.execute",
        return_value=subprocess.CompletedProcess([], 0, '{"type":"turn.completed"}\n', ''),
    ) as execute:
        CodexExecGateway().run(job)
    assert not any("CARGO_HOME" in arg for arg in execute.call_args.args[0])
    assert not (tmp_path / ".dpf-output/cargo-home").exists()


def test_framework_recovery_observes_source_and_smoke_not_report_prose(tmp_path):
    from tests.workflow_support import ready_implementation
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    from driver_port_factory.core.recovery_state import repair_inputs
    project = ready_implementation(tmp_path)
    worktree = project.root / load_repository_acquisition(project).target_worktree.path
    before = repair_inputs(project, S.TARGET_FRAMEWORK_ENABLEMENT)
    source = worktree / "src/driver-api.rs"
    source.write_text(source.read_text() + "// actual source edit\n")
    changed = repair_inputs(project, S.TARGET_FRAMEWORK_ENABLEMENT)
    assert changed != before
    output = worktree / ".dpf-output"
    output.mkdir(exist_ok=True)
    (output / "report.md").write_text("New description, same source.\n")
    assert repair_inputs(project, S.TARGET_FRAMEWORK_ENABLEMENT) == changed
    (output / "implementation-smoke.sh").write_text("exit 1\n")
    assert repair_inputs(project, S.TARGET_FRAMEWORK_ENABLEMENT) != changed


@pytest.mark.parametrize("mutation", ["edit", "new", "delete", "executable"])
def test_joint_snapshot_detects_drift_even_on_former_framework_paths(tmp_path, mutation):
    from tests.migration_support import implemented
    from driver_port_factory.migration.implementation import validate_worktree_snapshot, ImplementationChanged
    from driver_port_factory.migration.contracts import MigrationArtifact as A
    project, worktree, _ = implemented(tmp_path)
    bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    assert bundle["schema_version"] == 2
    # Legacy ownership hints must never exclude a path from final drift checks.
    bundle["target_framework_files"] = ["driver.rs"]
    validate_worktree_snapshot(project.root, bundle)
    source = worktree / "driver.rs"
    if mutation == "edit":
        source.write_text("changed\n")
    elif mutation == "new":
        (worktree / "new-integration.rs").write_text("new\n")
    elif mutation == "delete":
        source.unlink()
    else:
        source.chmod(source.stat().st_mode | 0o111)
    with pytest.raises(ImplementationChanged):
        validate_worktree_snapshot(project.root, bundle)


def test_study_reuse_never_resurrects_an_older_matching_receipt(tmp_path):
    from tests.workflow_support import ready_implementation
    from driver_port_factory.target_study.reuse import remember, restore
    from driver_port_factory.target_study.contracts import TargetStudyStage as TS
    from driver_port_factory.core.events import RunEvent
    project = ready_implementation(tmp_path)
    remember(project)
    project.record_event(RunEvent.TASK_REUSE, {
        "stage": TS.STUDY.value, "identity": "newer correction", "report": "different report"})
    assert restore(project) is False


def test_fused_delivery_survives_restart_and_receipt_invalidates_on_smoke_or_retry(tmp_path):
    from tests.workflow_support import ready_implementation, runner
    from tests.migration_support import smoke_fixture
    from tests.submission_support import submit
    from driver_port_factory.codex.gateway import CodexResult
    from driver_port_factory.composition import open_project
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    from driver_port_factory.migration.repair_execution import prepared

    project = ready_implementation(tmp_path)
    project.start(S.DRIVER_IMPLEMENTATION)
    project.retry_from(S.TARGET_FRAMEWORK_ENABLEMENT, trigger=S.DRIVER_IMPLEMENTATION,
                       reason="synthetic coherent delivery repair")
    port = runner(project)
    calls = []

    def gateway(job):
        calls.append(job.stage)
        assert job.stage is S.TARGET_FRAMEWORK_ENABLEMENT
        worktree = job.execution_root
        output = worktree / ".dpf-output"
        output.mkdir(exist_ok=True)
        (worktree / "driver.rs").write_text("pub fn init() {}\n")
        smoke_fixture(worktree)
        (output / "public-qemu.sh").write_text("exit 0\n")
        report = output / "report.md"
        report.write_text("Synthetic coherent delivery, public assertions NOT_RUN.\n")
        submit(project, job, report, kind="report", decision="pass")
        return CodexResult(job.job_id, "", "worker")

    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", side_effect=gateway):
        port._target_framework_enablement(project)
        project = open_project(project.root)
        assert prepared(project) is not None
        worktree = project.root / load_repository_acquisition(project).target_worktree.path
        smoke = worktree / ".dpf-output/implementation-smoke.sh"
        original = smoke.read_text()
        smoke.write_text(original + "exit 1\n")
        assert prepared(project) is None
        smoke.write_text(original)
        port._implementation(project)
        port._artifact_preparation(project)
        assert calls == [S.TARGET_FRAMEWORK_ENABLEMENT]
        assert project.stage(S.ARTIFACT_PREPARATION).status.value == "PASS"
    project.start(S.PUBLIC_QEMU_VALIDATION)
    project.retry_from(S.TARGET_FRAMEWORK_ENABLEMENT, trigger=S.PUBLIC_QEMU_VALIDATION,
                       reason="synthetic interface correction")
    assert prepared(open_project(project.root)) is None


def test_execution_and_repair_rules_invalidate_only_applicable_stage_policies(tmp_path):
    import shutil
    from pathlib import Path
    from tests.workflow_support import ready_implementation
    from driver_port_factory.codex.prompts import SkillPromptComposer
    from driver_port_factory.composition import WORKFLOW_STAGE_CATALOG
    import driver_port_factory
    project = ready_implementation(tmp_path)
    pack = tmp_path / "prompt-pack"
    shutil.copytree(Path(driver_port_factory.__file__).parent / "data/prompt-packs/default", pack)
    composer = SkillPromptComposer(Path(project.config.skill_root), WORKFLOW_STAGE_CATALOG,
                                   project.workflow.stage_values, pack)
    stages = (S.TARGET_FRAMEWORK_ENABLEMENT, S.DRIVER_IMPLEMENTATION,
              S.ARTIFACT_PREPARATION, S.FINAL_EVIDENCE_REVIEW)
    before = {stage: composer.policy_digest(stage) for stage in stages}
    rule = pack / "execution.md"
    rule.write_text(rule.read_text() + "Changed execution obligations.\n")
    execution_changed = {stage: composer.policy_digest(stage) for stage in stages}
    assert execution_changed[S.FINAL_EVIDENCE_REVIEW] == before[S.FINAL_EVIDENCE_REVIEW]
    for stage in stages[:-1]:
        assert execution_changed[stage] != before[stage]
    rule = pack / "repair.md"
    rule.write_text(rule.read_text() + "Changed repair obligations.\n")
    for stage in stages:
        changed = composer.policy_digest(stage) != execution_changed[stage]
        assert changed is (stage in (S.DRIVER_IMPLEMENTATION, S.ARTIFACT_PREPARATION))
