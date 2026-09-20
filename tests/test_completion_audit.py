import pytest
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.migration.completion_audit import CompletionAuditService, _execution_status
from driver_port_factory.migration.contracts import ContractExecutionStatus as Status, MigrationStage as S
from tests.migration_support import accepted

def test_developer_completion_preserves_runtime_limits(tmp_path):
    p, _, _ = accepted(tmp_path)
    audit = CompletionAuditService().run(p)
    assert p.stage(S.COMPLETION_AUDIT).status.value == "PASS"
    assert audit["artifact_lineage"]["verified"]
    assert audit["scope_limits"]["real_hardware"] == "NOT_RUN"
    assert audit["blind_candidate"] is None
    assert CompletionAuditService().run(p) == audit

def test_mixed_observations_do_not_become_pass():
    observations = [{"execution_status": "PASS"}, {"execution_status": "BLOCKED"}]
    assert _execution_status("NOT_RUN", observations) == "BLOCKED"
