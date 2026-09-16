from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.composition import (
    ARTIFACT_VALIDATORS,
    initialize_project,
    workflow_for,
)
from driver_port_factory.control.contracts import ControlArtifact, ControlStage
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    WorkflowError,
)
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.evaluation.contracts import EvaluationArtifact, EvaluationStage
from driver_port_factory.intake.contracts import IntakeArtifact, IntakeStage
from driver_port_factory.knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.sealing.candidate import CandidateSealer
from driver_port_factory.sealing.contracts import SealingArtifact, SealingStage
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.target_study.contracts import (
    TargetStudyArtifact,
    TargetStudyStage,
)


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
    def test_every_domain_artifact_has_a_validator(self) -> None:
        artifact_groups = (
            ControlArtifact,
            IntakeArtifact,
            AcquisitionArtifact,
            EnvironmentArtifact,
            KnowledgeArtifact,
            TargetStudyArtifact,
            SourceAnalysisArtifact,
            MigrationArtifact,
            SealingArtifact,
            EvaluationArtifact,
            CodexArtifact,
        )
        missing = [
            artifact.value
            for group in artifact_groups
            for artifact in group
            if not ARTIFACT_VALIDATORS.contains(artifact)
        ]
        self.assertEqual(missing, [])

    def test_every_domain_stage_appears_in_a_legal_role_workflow(self) -> None:
        configurations = (
            make_config(ActorRole.DEVELOPER, EvaluationMode.DEVELOPER_EVIDENCE),
            make_config(ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            make_config(
                ActorRole.MIGRATION_OPERATOR,
                EvaluationMode.POST_HOC_SEALED_BLIND,
            ),
            make_config(ActorRole.CURATOR, EvaluationMode.PROSPECTIVE_BLIND),
            make_config(ActorRole.CURATOR, EvaluationMode.POST_HOC_SEALED_BLIND),
            make_config(ActorRole.EVALUATOR, EvaluationMode.PROSPECTIVE_BLIND),
            make_config(ActorRole.AUDITOR, EvaluationMode.PROSPECTIVE_BLIND),
        )
        used = {
            stage.name.value
            for configuration in configurations
            for stage in workflow_for(configuration).stages
        }
        stage_groups = (
            ControlStage,
            IntakeStage,
            AcquisitionStage,
            EnvironmentStage,
            KnowledgeStage,
            TargetStudyStage,
            SourceAnalysisStage,
            MigrationStage,
            SealingStage,
            EvaluationStage,
        )
        declared = {stage.value for group in stage_groups for stage in group}
        self.assertEqual(declared - used, set())

    def test_candidate_seal_uses_phase_ten_inputs_not_final_audit(self) -> None:
        self.assertIn(MigrationArtifact.PUBLIC_REPAIR_REPORT, CandidateSealer.REQUIRED_ARTIFACTS)
        self.assertNotIn(MigrationArtifact.EVIDENCE_AUDIT, CandidateSealer.REQUIRED_ARTIFACTS)

    def test_developer_evidence_skips_blind_candidate_sealing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.DEVELOPER, EvaluationMode.DEVELOPER_EVIDENCE),
            )
            stages = project.stages()
            names = {stage.name for stage in stages}
            self.assertIn(MigrationStage.COMPLETION_AUDIT, names)
            self.assertNotIn(SealingStage.CANDIDATE_SEALING, names)
            self.assertTrue(names.isdisjoint(set(EvaluationStage)))
            audit = project.stage(MigrationStage.COMPLETION_AUDIT)
            self.assertEqual(audit.dependencies, (MigrationStage.PUBLIC_REPAIR,))

    def test_prospective_blind_seals_transfers_then_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = [stage.name for stage in project.stages()]
            ordered = (
                MigrationStage.PUBLIC_REPAIR,
                SealingStage.CANDIDATE_SEALING,
                SealingStage.CANDIDATE_TRANSFER,
                MigrationStage.COMPLETION_AUDIT,
            )
            self.assertEqual(sorted(ordered, key=names.index), list(ordered))
            self.assertEqual(
                project.stage(SealingStage.CANDIDATE_SEALING).dependencies,
                (MigrationStage.PUBLIC_REPAIR,),
            )
            self.assertEqual(
                project.stage(MigrationStage.COMPLETION_AUDIT).dependencies,
                (SealingStage.CANDIDATE_TRANSFER,),
            )

    def test_posthoc_blind_seals_exports_digest_then_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(
                    ActorRole.MIGRATION_OPERATOR,
                    EvaluationMode.POST_HOC_SEALED_BLIND,
                ),
            )
            names = [stage.name for stage in project.stages()]
            ordered = (
                MigrationStage.PUBLIC_REPAIR,
                SealingStage.CANDIDATE_SEALING,
                SealingStage.OPAQUE_DIGEST_EXPORT,
                MigrationStage.COMPLETION_AUDIT,
            )
            self.assertEqual(sorted(ordered, key=names.index), list(ordered))
            self.assertEqual(
                project.stage(MigrationStage.COMPLETION_AUDIT).dependencies,
                (SealingStage.OPAQUE_DIGEST_EXPORT,),
            )

    def test_developer_cannot_claim_blind_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, self.assertRaises(WorkflowError):
            initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.DEVELOPER, EvaluationMode.PROSPECTIVE_BLIND),
            )

    def test_prospective_migrator_requires_blind_binding_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.MIGRATION_OPERATOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = [stage.name for stage in project.stages()]
            self.assertEqual(names[1], EvaluationStage.BLIND_BINDING)
            self.assertEqual(project.stage(EvaluationStage.BLIND_BINDING).status.value, "READY")
            self.assertEqual(project.stage(IntakeStage.REQUEST).status.value, "PENDING")

    def test_posthoc_curator_accepts_digest_before_contract_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.CURATOR, EvaluationMode.POST_HOC_SEALED_BLIND),
            )
            names = [stage.name for stage in project.stages()]
            self.assertLess(
                names.index(EvaluationStage.OPAQUE_CANDIDATE_ACCEPTANCE),
                names.index(EvaluationStage.CONTRACT_FREEZE),
            )

    def test_evaluator_has_no_migration_implementation_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.EVALUATOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = {stage.name for stage in project.stages()}
            self.assertIn(EvaluationStage.ISOLATION_GATE, names)
            self.assertIn(EvaluationStage.FAULT_INJECTION, names)
            self.assertNotIn(MigrationStage.DRIVER_IMPLEMENTATION, names)
            self.assertNotIn(MigrationStage.PUBLIC_REPAIR, names)

    def test_auditor_has_read_only_claim_stages(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(
                Path(temporary) / "run",
                make_config(ActorRole.AUDITOR, EvaluationMode.PROSPECTIVE_BLIND),
            )
            names = {stage.name for stage in project.stages()}
            self.assertIn(EvaluationStage.INDEPENDENCE_AUDIT, names)
            self.assertIn(EvaluationStage.CLAIM_AUDIT, names)
            self.assertNotIn("blind_evaluation", names)
            self.assertNotIn(MigrationStage.DRIVER_IMPLEMENTATION, names)


if __name__ == "__main__":
    unittest.main()
