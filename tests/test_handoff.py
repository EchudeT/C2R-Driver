from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.models import StageStatus, WorkflowError
from driver_port_factory.knowledge.bootstrap import KnowledgeBootstrapper
from driver_port_factory.migration.contracts import HandoffMode, MigrationArtifact, MigrationStage
from driver_port_factory.migration.handoff import MigrationHandoff
from driver_port_factory.source_analysis.contracts import SourceAnalysisStage
from driver_port_factory.target_study.service import TargetStudyService
from tests.test_knowledge import prepare_project, probe_plan
from tests.test_target_study import target_study_inputs


def handoff_ready_project(root: Path):
    project, checkouts = prepare_project(root)
    KnowledgeBootstrapper().bootstrap(project, probe_plan_path=probe_plan(project.root))
    inputs, _ = target_study_inputs(project.root, project, checkouts)
    TargetStudyService().validate(project, **inputs)
    return project, checkouts


class MigrationHandoffTests(unittest.TestCase):
    def test_freezes_complete_bootstrap_for_downstream(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _ = handoff_ready_project(Path(temporary))
            record = MigrationHandoff().create(project)

            self.assertEqual(record["evaluation"], {"mode": HandoffMode.DEVELOPER.value})
            self.assertEqual(
                record["environment"]["experiment_ready_evidence"]["milestone"], "EXPERIMENT_READY"
            )
            self.assertEqual(record["knowledge"]["status_result"]["status"], "READY")
            self.assertTrue(record["target_study"]["artifacts"])
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
