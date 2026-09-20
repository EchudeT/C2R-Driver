from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.codex.contracts import CodexArtifact
from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.environment.contracts import EnvironmentArtifact
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.knowledge.contracts import KnowledgeArtifact
from driver_port_factory.migration.contracts import HandoffMode, MigrationArtifact, MigrationStage
from driver_port_factory.migration.handoff import MigrationHandoff
from driver_port_factory.source_analysis.contracts import SourceAnalysisStage
from driver_port_factory.target_study.contracts import TargetStudyArtifact
from tests.test_knowledge import prepare_project
from tests.test_target_study import accept_target_study


def handoff_ready_project(root: Path):
    project, checkouts = prepare_project(root)
    KnowledgeBootstrapper().build_infrastructure(project)
    accept_target_study(project)
    return project, checkouts


class MigrationHandoffTests(unittest.TestCase):
    def test_freezes_complete_bootstrap_for_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _ = handoff_ready_project(Path(temporary))
            record = MigrationHandoff().create(project)

            self.assertEqual(record["schema_version"], 2)
            self.assertEqual(record["evaluation"], {"mode": HandoffMode.DEVELOPER.value})
            artifacts = {item["kind"]: item for item in record["upstream_artifacts"]}
            self.assertIn(EnvironmentArtifact.RECOVERY_ATTEMPT.value, artifacts)
            self.assertTrue(
                {
                    CodexArtifact.JOB_RESULT.value,
                    CodexArtifact.EVENT_LOG.value,
                    TargetStudyArtifact.VALIDATION_ATTEMPT.value,
                }.isdisjoint(artifacts)
            )
            for kind in (
                EnvironmentArtifact.EXPERIMENT_ROUTE,
                KnowledgeArtifact.QUERY_CONTRACT,
                TargetStudyArtifact.CHANGE_PLAN,
            ):
                self.assertTrue(Path(artifacts[kind.value]["path"]).is_file())
            self.assertFalse(
                {"repositories", "evidence", "environment", "knowledge", "target_study"}
                & record.keys()
            )
            self.assertEqual(project.stage(MigrationStage.HANDOFF).status, StageStatus.PASS)
            self.assertEqual(
                project.stage(SourceAnalysisStage.SOURCE_CLOSURE).status, StageStatus.READY
            )
            persisted = project.load_json_artifact(
                MigrationStage.HANDOFF, MigrationArtifact.HANDOFF
            )
            self.assertEqual(persisted, record)

    def test_incomplete_environment_or_knowledge_cannot_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _ = prepare_project(Path(temporary))
            with self.assertRaisesRegex(WorkflowError, "not READY"):
                MigrationHandoff().create(project)

    def test_frozen_upstream_drift_cannot_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, checkouts = handoff_ready_project(Path(temporary))
            target = project.root / checkouts["target"].checkout_path / "docs/driver-contract.md"
            target.write_text(target.read_text(encoding="utf-8") + "drift\n", encoding="utf-8")
            with self.assertRaises(WorkflowError):
                MigrationHandoff().create(project)


if __name__ == "__main__":
    unittest.main()
