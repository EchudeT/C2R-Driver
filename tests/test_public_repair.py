import pytest
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.contracts import MigrationStage as S
from driver_port_factory.migration.public_repair import PublicRepairService
from tests.migration_support import public_run, accepted

def test_review_binds_passing_evidence(tmp_path):
    p, _, _ = accepted(tmp_path)
    assert p.stage(S.PUBLIC_REPAIR).status.value == "PASS"
    p.verify_integrity()

def test_failed_execution_cannot_be_accepted_via_service(tmp_path):
    p, _, report = public_run(tmp_path, exit_code=1)
    with pytest.raises(WorkflowError):
        PublicRepairService().finalize(p, review_path=report)
    assert p.stage(S.PUBLIC_REPAIR).status.value == "PENDING"
