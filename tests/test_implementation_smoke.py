"""Controller policy tests with synthetic QEMU, not NE2000 certification."""
import json

import pytest

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.codex.contracts import CodexContinuation
from driver_port_factory.migration.contracts import MigrationArtifact as A
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.implementation import DriverImplementationService
from tests.migration_support import smoke_fixture
from tests.workflow_support import ready_implementation


def setup_smoke(tmp_path):
    project = ready_implementation(tmp_path)
    tree = project.root / load_repository_acquisition(project).target_worktree.path
    (tree / "driver.rs").write_text("pub fn init() {}\n")
    output = tree / ".dpf-output"
    output.mkdir()
    report = output / "report.md"
    report.write_text("Synthetic self-test. DPF_SELF_REVIEW: PASS\n")
    smoke_fixture(tree)
    project.start(S.DRIVER_IMPLEMENTATION)
    return project, tree, report


@pytest.mark.parametrize("failure", ["missing", "oracle", "presence", "no_qemu"])
def test_failed_smoke_stays_in_implementation(tmp_path, failure):
    project, tree, report = setup_smoke(tmp_path)
    script = tree / ".dpf-output/implementation-smoke.sh"
    if failure == "missing":
        script.unlink()
    elif failure == "oracle":
        script.write_text(script.read_text() + "exit 1\n")
    elif failure == "presence":
        (tree / ".dpf-output/runtime-artifact").write_bytes(b"stale image")
    else:
        script.write_text("mkdir -p .dpf-output/qemu-runs\n"
                          "echo claimed-pass > .dpf-output/qemu-runs/smoke.log\n")
    with pytest.raises(CodexContinuation):
        DriverImplementationService().snapshot_worktree(project, report)
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"
    assert not project.current_artifact_refs(stage=S.DRIVER_IMPLEMENTATION)
    receipts = list((project.control / "implementation-smoke").glob("*/receipt.json"))
    if failure != "missing":
        assert len(receipts) == 1
        assert json.loads(receipts[0].read_text())["status"] == "FAIL"


def test_smoke_reuses_report_edits_but_rechecks_source_and_runtime(tmp_path):
    from driver_port_factory.migration.implementation_smoke import implementation_smoke

    project, tree, report = setup_smoke(tmp_path)
    base = load_repository_acquisition(project).target_worktree.base_commit
    first = implementation_smoke(project, tree, base)
    report.write_text("Updated prose. DPF_SELF_REVIEW: PASS\n")
    assert implementation_smoke(project, tree, base) == first
    (tree / "driver.rs").write_text("pub fn init() { /* repair */ }\n")
    with pytest.raises(CodexContinuation):
        implementation_smoke(project, tree, base)
    smoke_fixture(tree)
    DriverImplementationService().snapshot_worktree(project, report)
    bundle = project.load_json_artifact(S.DRIVER_IMPLEMENTATION, A.IMPLEMENTATION_BUNDLE)
    assert bundle["functional_smoke"]["status"] == "PASS"
    assert bundle["functional_smoke"]["receipt_path"] != first["receipt_path"]
    assert len(list((project.control / "implementation-smoke").glob("*/receipt.json"))) == 3


def test_asterinas_host_smoke_is_rejected(tmp_path):
    from dataclasses import replace
    from unittest.mock import patch

    from driver_port_factory.core.project import Project

    project, _tree, report = setup_smoke(tmp_path)
    config = replace(project.config, target_platform="asterinas")
    with (
        patch.object(Project, "config", property(lambda _: config)),
        pytest.raises(CodexContinuation),
    ):
        DriverImplementationService().snapshot_worktree(project, report)
    saved = next((project.control / "implementation-smoke").glob("*/receipt.json"))
    evidence = json.loads(saved.read_text())["execution"]
    assert evidence["container_execution"]["satisfied"] is False
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "RUNNING"


def test_smoke_receipt_rechecks_changed_validation_policy(tmp_path):
    from unittest.mock import patch

    from driver_port_factory.migration.implementation_smoke import implementation_smoke
    project, tree, _ = setup_smoke(tmp_path)
    base = load_repository_acquisition(project).target_worktree.base_commit
    first = implementation_smoke(project, tree, base)
    with patch("driver_port_factory.migration.implementation_smoke.smoke_policy_digest",
               return_value="new-validation-policy"):
        second = implementation_smoke(project, tree, base)
    assert first["receipt_path"] != second["receipt_path"]
    assert second["status"] == "PASS"
