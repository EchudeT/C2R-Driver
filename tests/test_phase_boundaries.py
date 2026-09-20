from unittest.mock import patch

import pytest

from driver_port_factory.composition import open_project
from driver_port_factory.core.models import StageStatus
from driver_port_factory.core.phases import PhaseBoundaryError, phase
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.source_analysis.contracts import SourceAnalysisStage as C
from tests.test_workflow_alignment import ready_implementation


@pytest.mark.parametrize("target", [C.SOURCE_CLOSURE, S.CONTRACTS])
def test_cross_phase_repair_preserves_evidence_and_waits(tmp_path, target):
    p = ready_implementation(tmp_path)
    p.start(S.DRIVER_IMPLEMENTATION)
    before = {s.name: p.current_artifact_refs(stage=s.name) for s in p.stages()}
    p.retry_from(target, trigger=S.DRIVER_IMPLEMENTATION, reason="frozen premise changed")
    p = open_project(p.root)
    assert p.stage(target).status is StageStatus.PASS
    assert p.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.WAITING_FOR_USER
    assert before == {s.name: p.current_artifact_refs(stage=s.name) for s in p.stages()}
    p.resume_after_user(S.DRIVER_IMPLEMENTATION, answer="continue")
    p.retry_from(target, trigger=S.DRIVER_IMPLEMENTATION, reason="same request")
    assert p.stage(target).status is StageStatus.PASS
    assert p.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.WAITING_FOR_USER
    p.verify_integrity()


def test_direct_persistence_cannot_bypass_boundary(tmp_path):
    p = ready_implementation(tmp_path)
    p.start(S.DRIVER_IMPLEMENTATION)
    with pytest.raises(PhaseBoundaryError):
        p._persistence.retry_from(S.CONTRACTS, trigger=S.DRIVER_IMPLEMENTATION,
            actor_role=p.config.actor_role, reason="direct call")
    assert p.stage(S.CONTRACTS).status is StageStatus.PASS


def test_historical_phase_entry_seals_earlier_phase_after_status_reset():
    from driver_port_factory.core.phases import require_local_repair
    rows = [{"name": "driver_implementation", "status": "PENDING", "entered": 1}]
    with pytest.raises(PhaseBoundaryError, match="sealed"):
        require_local_repair(C.SOURCE_CLOSURE, S.CONTRACTS, rows)


def test_preparation_is_recorded_before_implementation_commit(tmp_path):
    from tests.migration_support import packaged
    from tests.test_workflow_alignment import runner
    from driver_port_factory.migration.repair_execution import prepared
    p, w, report = packaged(tmp_path)
    p.start(S.PUBLIC_QEMU_VALIDATION)
    p.retry_from(S.DRIVER_IMPLEMENTATION, trigger=S.PUBLIC_QEMU_VALIDATION, reason="defect")
    p.start(S.DRIVER_IMPLEMENTATION)
    (w / ".dpf-output/public-qemu.sh").write_text("exit 1\n")
    port = runner(p)
    with patch.object(port, "_materialize_codex_report", return_value=report), patch(
            "driver_port_factory.port.DriverImplementationService.snapshot_worktree",
            side_effect=RuntimeError("crash before commit")):
        with pytest.raises(RuntimeError):
            port._accept_implementation_result(p, None)
    assert prepared(open_project(p.root)) == report.resolve()
    assert p.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.RUNNING
    assert phase(S.PUBLIC_REPAIR) == phase(S.DRIVER_IMPLEMENTATION)
    p = open_project(p.root)
    port = runner(p)
    with patch.object(port, "_codex_gate", side_effect=AssertionError("no repeated worker")):
        port._implementation(p)
    assert p.stage(S.DRIVER_IMPLEMENTATION).status is StageStatus.PASS
    p.verify_integrity()
