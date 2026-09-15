from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.models import ActorRole, EvaluationMode, ProjectConfig, WorkflowError
from driver_port_factory.core.project import Project


def make_config(role: ActorRole, mode: EvaluationMode) -> ProjectConfig:
    return ProjectConfig(
        project_id=f"{role.value}-test",
        source_platform="linux",
        target_platform="asterinas",
        driver_name="ne2k-pci",
        evaluation_mode=mode,
        actor_role=role,
    )


class RoleBoundaryTests(unittest.TestCase):
    def test_developer_cannot_claim_blind_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(WorkflowError):
                Project.initialize(
                    Path(temporary) / "run",
                    make_config(ActorRole.DEVELOPER, EvaluationMode.PROSPECTIVE_BLIND),
                )

    def test_prospective_migrator_requires_blind_binding_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(
                Path(temporary) / "run",
                make_config(ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = [stage.name for stage in project.store.stages()]
            self.assertEqual(names[1], "blind_binding")
            self.assertEqual(project.store.stage("blind_binding").status.value, "READY")
            self.assertEqual(project.store.stage("driver_identity").status.value, "PENDING")

    def test_posthoc_curator_accepts_digest_before_contract_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(
                Path(temporary) / "run",
                make_config(ActorRole.CURATOR, EvaluationMode.POST_HOC_SEALED_BLIND),
            )
            names = [stage.name for stage in project.store.stages()]
            self.assertLess(names.index("opaque_candidate_acceptance"), names.index("contract_freeze"))

    def test_evaluator_has_no_migration_implementation_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(
                Path(temporary) / "run",
                make_config(ActorRole.EVALUATOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = {stage.name for stage in project.store.stages()}
            self.assertIn("isolation_gate", names)
            self.assertIn("fault_injection", names)
            self.assertNotIn("rust_implementation", names)
            self.assertNotIn("public_repair", names)

    def test_auditor_has_read_only_claim_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(
                Path(temporary) / "run",
                make_config(ActorRole.AUDITOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = {stage.name for stage in project.store.stages()}
            self.assertIn("independence_audit", names)
            self.assertIn("claim_audit", names)
            self.assertNotIn("blind_evaluation", names)
            self.assertNotIn("rust_implementation", names)


if __name__ == "__main__":
    unittest.main()
