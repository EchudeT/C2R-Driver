from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.cli import main
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.migration.completion_audit import (
    CompletionAuditService,
    _execution_status,
)
from driver_port_factory.migration.contracts import (
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
)
from driver_port_factory.sealing.contracts import SealingArtifact, SealingStage
from tests.test_candidate_sealing import (
    blind_project,
    evaluator_receipt,
    run_seal,
    run_transfer,
    seal_inputs,
)


class CompletionAuditTests(unittest.TestCase):
    def test_complete_blind_lineage_is_frozen(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = blind_project(Path(temporary))
            request, timestamp = seal_inputs(project, "attempt-1")
            output = project.root / "evaluator-transfer"
            self.assertEqual(run_seal(project, request, timestamp, output), 0)
            self.assertEqual(
                run_transfer(project, evaluator_receipt(project, output, "attempt-1")), 0
            )

            self.assertEqual(main(["completion-audit", "run", str(project.root)]), 0)
            audit = project.load_json_artifact(
                MigrationStage.COMPLETION_AUDIT, MigrationArtifact.EVIDENCE_AUDIT
            )
            self.assertEqual(
                project.stage(MigrationStage.COMPLETION_AUDIT).status, StageStatus.PASS
            )
            self.assertTrue(audit["artifact_lineage"]["verified"])
            self.assertEqual(
                audit["blind_candidate"]["candidate_sha256"],
                project.artifact(
                    SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_BUNDLE
                ).digest,
            )
            self.assertEqual(audit["scope_limits"]["real_hardware"], "NOT_RUN")

    def test_mixed_or_blocked_observations_do_not_become_pass(self) -> None:
        observations = [
            {"execution_status": ContractExecutionStatus.PASS.value},
            {"execution_status": ContractExecutionStatus.BLOCKED.value},
        ]
        self.assertEqual(
            _execution_status(ContractExecutionStatus.NOT_RUN.value, observations),
            ContractExecutionStatus.BLOCKED.value,
        )

    def test_stale_repaired_implementation_cannot_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = blind_project(Path(temporary))
            service = CompletionAuditService()
            implementation = project.load_json_artifact(
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
            )
            compliance = project.load_json_artifact(
                MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT
            )
            identity = project.load_json_artifact(
                MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
            )
            public = project.load_json_artifact(
                MigrationStage.PUBLIC_QEMU_VALIDATION, MigrationArtifact.PUBLIC_QEMU_REPORT
            )
            repair = {
                "implementation": {"digest": "0" * 64, "document": implementation},
            }

            with self.assertRaisesRegex(WorkflowError, "stale compliance or artifact lineage"):
                service._lineage(project, implementation, compliance, identity, public, repair)


if __name__ == "__main__":
    unittest.main()
