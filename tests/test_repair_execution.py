"""Composite repair keeps real evidence gates without extra planning AI turns."""
from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.core.models import StageStatus
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.repair_execution import prepared
from driver_port_factory.migration.public_qemu import PublicQemuService
from tests.migration_support import public_run
from tests.test_workflow_alignment import runner


def repair_fixture(tmp_path, *, failing_harness=False):
    p, worktree, report = public_run(tmp_path)
    p.start(S.PUBLIC_REPAIR)
    p.retry_from(S.DRIVER_IMPLEMENTATION, trigger=S.PUBLIC_REPAIR, reason="fixture functional defect")
    p.start(S.DRIVER_IMPLEMENTATION)
    (worktree / "driver.rs").write_text("pub fn init() -> u32 { 2 }\n")
    (worktree / ".dpf-output/runtime-artifact").write_bytes((worktree / "driver.rs").read_bytes())
    if failing_harness:
        script = worktree / ".dpf-output/public-qemu.sh"
        script.write_text(script.read_text().replace("exit 0\n", "exit 1\n"))
    frozen = p.artifacts.put_bytes(report.read_bytes(), kind="codex_work_report")
    report = p.artifacts.path_for_digest(frozen.digest)
    port = runner(p)
    with patch.object(port, "_materialize_codex_report", return_value=report):
        port._accept_implementation_result(p, None)
    return p, worktree, report


def test_repair_prepares_once_then_captures_and_executes_without_planning_ai(tmp_path):
    p, _, report = repair_fixture(tmp_path)
    p = open_project(p.root)
    port = runner(p)
    assert prepared(p) == report
    with patch.object(port, "_codex_gate", side_effect=AssertionError("no packaging model")):
        port._artifact_preparation(p)
    p = open_project(p.root)
    port = runner(p)
    observed = []
    def self_check(project, stage, context, accept, **kwargs):
        assert stage is S.PUBLIC_QEMU_VALIDATION
        assert context["controller_execution"]["status"] == "PASS"
        observed.append(context["controller_execution"]["attempt"])
        return False  # simulate interruption before final worker self-check
    with patch.object(port, "_codex_gate", side_effect=self_check):
        port._public_qemu(p)
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status is StageStatus.RUNNING
    port = runner(open_project(p.root))
    with patch.object(port, "_codex_gate", side_effect=self_check), patch(
            "driver_port_factory.migration.public_qemu.run_public_harness",
            side_effect=AssertionError("passing execution must be reused after restart")):
        port._public_qemu(open_project(p.root))
    assert observed[0] == observed[1]
    PublicQemuService().accept_self_review(p, work_report_path=report)
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status is StageStatus.PASS
    p.verify_integrity()


@pytest.mark.parametrize("changed", ["driver.rs", ".dpf-output/runtime-artifact", ".dpf-output/public-qemu.sh"])
def test_preparation_is_not_valid_after_input_changes(tmp_path, changed):
    p, worktree, _ = repair_fixture(tmp_path)
    path = worktree / changed
    path.write_bytes(path.read_bytes() + b"\nchanged\n")
    assert prepared(open_project(p.root)) is None


def test_new_invalidation_retires_preparation_even_with_identical_files(tmp_path):
    p, _, _ = repair_fixture(tmp_path)
    p.start(S.ARTIFACT_PREPARATION)
    p.retry_from(S.DRIVER_IMPLEMENTATION, trigger=S.ARTIFACT_PREPARATION, reason="new defect")
    assert prepared(open_project(p.root)) is None


def test_failed_execution_returns_evidence_and_does_not_auto_rerun_on_restart(tmp_path):
    p, _, _ = repair_fixture(tmp_path, failing_harness=True)
    port = runner(p)
    port._artifact_preparation(p)
    def repair(project, stage, context, accept, **kwargs):
        assert context["controller_execution"]["status"] == "FAIL"
        return False
    with patch.object(port, "_codex_gate", side_effect=repair):
        port._public_qemu(p)
    port = runner(open_project(p.root))
    with patch.object(port, "_codex_gate", side_effect=repair), patch(
            "driver_port_factory.migration.public_qemu.run_public_harness",
            side_effect=AssertionError("failure needs worker attribution before re-execution")):
        port._public_qemu(open_project(p.root))
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status is StageStatus.RUNNING
    p.verify_integrity()


def test_prepared_but_stale_image_cannot_skip_presence_validation(tmp_path):
    from driver_port_factory.migration.repair_execution import record
    p, worktree, report = repair_fixture(tmp_path)
    (worktree / ".dpf-output/runtime-artifact").write_text("stale image")
    record(p, report)
    port = runner(p)
    def fix(project, stage, context, accept, **kwargs):
        assert stage is S.ARTIFACT_PREPARATION
        assert "presence checker failed" in context["controller_validation_error"]
        assert kwargs["objective"]
        return False
    with patch.object(port, "_codex_gate", side_effect=fix) as gateway:
        port._artifact_preparation(p)
    assert gateway.call_count == 1
    assert p.stage(S.ARTIFACT_PREPARATION).status is StageStatus.RUNNING
    assert p.stage(S.PUBLIC_QEMU_VALIDATION).status is StageStatus.PENDING
    p.verify_integrity()
