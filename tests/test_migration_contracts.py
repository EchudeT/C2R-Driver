from unittest.mock import Mock
import pytest
from driver_port_factory.core.models import FileArtifact, WorkflowError
from driver_port_factory.migration.contracts import MigrationArtifact as A, MigrationStage as S
from tests.test_workflow_alignment import ready_implementation, runner

def test_plan_is_one_gate_with_both_evidence_views(tmp_path):
    p = ready_implementation(tmp_path)
    assert "test_adaptation" not in p.workflow.stage_values
    assert p.artifact(S.CONTRACTS, A.CONTRACTS).digest == p.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest
    p.verify_integrity()

def test_contract_only_cannot_finalize_combined_plan(tmp_path):
    p = ready_implementation(tmp_path, plan=False)
    p.start(S.CONTRACTS)
    report = p.root / "plan.md"
    report.write_text("Incomplete contract-only submission")
    with pytest.raises(WorkflowError):
        p.finalize_stage(S.CONTRACTS, (FileArtifact(A.CONTRACTS, report),))

def test_plan_acceptance_freezes_one_report_without_second_model(tmp_path):
    p = ready_implementation(tmp_path, plan=False)
    p.start(S.CONTRACTS)
    port = runner(p)
    report = p.root / "new-plan.md"
    report.write_text("# Contracts\nDevice init.\n# Adapted tests\nRun init with a wrong-device control.")
    port._materialize_codex_report = Mock(return_value=report)
    port._accept_contracts_result(p, Mock())
    assert p.artifact(S.CONTRACTS, A.CONTRACTS).digest == p.artifact(S.CONTRACTS, A.TEST_PORT_MATRIX).digest
    assert p.stage(S.DRIVER_IMPLEMENTATION).status.value == "READY"
