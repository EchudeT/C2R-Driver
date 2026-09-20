from unittest.mock import Mock, patch

import pytest

from driver_port_factory.codex.contracts import CodexOutputError
from driver_port_factory.migration.contracts import MigrationStage as Stage
from driver_port_factory.migration.repair_routing import PrerequisiteRepair, repair_target
from driver_port_factory.port import PortRunner


@pytest.mark.parametrize("target", [Stage.DRIVER_IMPLEMENTATION,
    Stage.ARTIFACT_PREPARATION, Stage.PUBLIC_QEMU_VALIDATION])
def test_explicit_smallest_gate(tmp_path, target):
    report = tmp_path / "review.md"
    report.write_text(f"Evidence.\nDPF_REPAIR_STAGE: {target.value}\nDPF_REVIEW: REWORK\n")
    with pytest.raises(PrerequisiteRepair) as outcome:
        PortRunner._report_outcome(report)
    assert outcome.value.target == target


@pytest.mark.parametrize("text", ["", "DPF_REPAIR_STAGE: unknown",
    "DPF_REPAIR_STAGE: artifact_preparation\nDPF_REPAIR_STAGE: driver_implementation"])
def test_ambiguous_routing_never_defaults_to_implementation(text):
    with pytest.raises(CodexOutputError):
        repair_target(text)


def test_empty_review_is_actionable_feedback_not_index_error(tmp_path):
    report = tmp_path / "review.md"
    report.write_text("")
    port = object.__new__(PortRunner)
    port._materialize_codex_report = Mock(return_value=report)
    with pytest.raises(CodexOutputError, match="must end"):
        port._accept_runtime_review(Mock(), Mock())


def test_packaging_source_drift_requires_self_check_then_rebinds_without_ai(tmp_path):
    from tests.migration_support import implemented
    from tests.test_workflow_alignment import runner
    from driver_port_factory.core.models import StageStatus
    from driver_port_factory.migration.contracts import MigrationArtifact as A
    project, worktree, report = implemented(tmp_path)
    (worktree / "driver.rs").write_text("pub fn init() -> u32 { 2 }\n")
    output = worktree / ".dpf-output"
    (output / "runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
    (output / "check-presence.sh").write_text(
        'cmp "$DPF_RUNTIME_ARTIFACT" "$DPF_TARGET_WORKTREE/driver.rs"\n')
    project.start(Stage.ARTIFACT_PREPARATION)
    port = runner(project)
    report.write_text("# Packaging fix; not yet checked\n")
    with patch.object(port, "_materialize_codex_report", return_value=report):
        with pytest.raises(CodexOutputError):
            port._accept_artifact_preparation_result(project, None)
        report.write_text("# Packaging fix checked, unchanged coverage retained.\nDPF_SELF_REVIEW: PASS\n")
        port._accept_artifact_preparation_result(project, None)
    assert project.stage(Stage.ARTIFACT_PREPARATION).status is StageStatus.PASS
    assert project.stage(Stage.DRIVER_IMPLEMENTATION).status is StageStatus.PASS
    bundle = project.load_json_artifact(Stage.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    assert bundle["files"][0]["sha256"]
    project.verify_integrity()
    # Reopen just like a resumed controller. The downstream task must see the
    # completed repair and the new bundle, not an outstanding snapshot defect.
    from driver_port_factory.composition import open_project
    from driver_port_factory.codex.gateway import CodexResult
    import json
    project = open_project(project.root)
    assert project.retry_feedback(Stage.PUBLIC_QEMU_VALIDATION)["status"] == "RESOLVED"
    report.write_text("Fixture external prerequisite.\nDPF_STATUS: BLOCKED\n")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=CodexResult(
            "fixture", f"REPORT_PATH: {report}\n", "fixture-session")) as gateway:
        runner(project)._public_qemu(project)
    context = json.loads(gateway.call_args.args[0].prompt.split("<job>")[1].split("</job>")[0])["context"]
    assert context["repair_state"]["status"] == "RESOLVED"
    assert "prerequisite_repair" not in context
    assert context["frozen_inputs"][A.IMPLEMENTATION_BUNDLE.value]["digest"] == project.artifact(
        Stage.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE).digest
    assert project.stage(Stage.DRIVER_IMPLEMENTATION).status is StageStatus.PASS


def test_every_supported_route_has_a_serializable_progress_identity(tmp_path):
    import json
    from driver_port_factory.migration.repair_routing import ROUTES, retry_prerequisite
    from tests.migration_support import accepted

    project, _, _ = accepted(tmp_path)
    with patch.object(project, "retry_from") as retry:
        for target in ROUTES.values():
            retry_prerequisite(project, target, trigger=Stage.COMPLETION_AUDIT, reason="fixture")
            assert json.dumps(retry.call_args.kwargs["progress"])


def test_repair_resolution_is_scoped_to_the_latest_invalidation(tmp_path):
    from tests.migration_support import implemented
    from driver_port_factory.migration.implementation import DriverImplementationService
    from driver_port_factory.composition import open_project
    project, _, report = implemented(tmp_path)
    for _ in range(2):
        project.start(Stage.ARTIFACT_PREPARATION)
        project.retry_from(Stage.DRIVER_IMPLEMENTATION, trigger=Stage.ARTIFACT_PREPARATION,
                           reason="fixture changed input")
        project = open_project(project.root)
        assert project.retry_feedback(Stage.PUBLIC_QEMU_VALIDATION)["status"] == "OPEN"
        project.start(Stage.DRIVER_IMPLEMENTATION)
        DriverImplementationService().snapshot_worktree(project, report)
        assert project.retry_feedback(Stage.PUBLIC_QEMU_VALIDATION)["status"] == "RESOLVED"
        assert project.retry_feedback(Stage.DRIVER_IMPLEMENTATION) is None
    project.verify_integrity()
