from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.core.project import Project
from driver_port_factory.intake.service import IntakeService


def config(**overrides) -> ProjectConfig:
    values = {
        "project_id": "ne2000-test",
        "source_platform": "linux",
        "target_platform": "asterinas",
        "driver_name": "ne2k-pci",
        "evaluation_mode": EvaluationMode.DEVELOPER_EVIDENCE,
        "actor_role": ActorRole.DEVELOPER,
    }
    values.update(overrides)
    return ProjectConfig(**values)


class WorkflowTests(unittest.TestCase):
    def test_required_outputs_and_dependencies_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(Path(temporary) / "run", config())
            self.assertEqual(project.store.stage("project_init").status, StageStatus.PASS)
            self.assertEqual(project.store.stage("request_intake").status, StageStatus.READY)
            self.assertEqual(project.store.stage("revision_selection").status, StageStatus.PENDING)

            with self.assertRaises(WorkflowError):
                project.start("revision_selection")
            result = IntakeService().analyze(
                project,
                raw_request="Port Linux ne2k-pci to Asterinas",
            )
            self.assertEqual(result.status.value, "FROZEN")
            self.assertEqual(project.store.stage("revision_selection").status, StageStatus.READY)
            self.assertTrue(project.store.verify_event_chain())

    def test_outputs_cannot_be_attached_before_stage_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(Path(temporary) / "run", config())
            with self.assertRaises(WorkflowError):
                project.add_bytes("revision_selection", "note", b"too early")

    def test_public_repair_accepts_failed_qemu_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Project.initialize(Path(temporary) / "run", config())
            with project.store._connect() as connection:  # arrange a late-stage fixture
                connection.execute(
                    "UPDATE stages SET status = ? WHERE name = ?",
                    (StageStatus.FAIL.value, "public_qemu_validation"),
                )
            project.store.refresh_ready()
            self.assertEqual(project.store.stage("public_repair").status, StageStatus.READY)


if __name__ == "__main__":
    unittest.main()
