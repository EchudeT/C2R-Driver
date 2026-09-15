from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driver_port_factory.acquisition.contracts import AcquisitionStage
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from driver_port_factory.intake.contracts import IntakeStage, IntakeStatus
from driver_port_factory.intake.service import IntakeService

NE2000_FIXTURE = Path(__file__).parents[1] / "examples" / "fixtures" / "linux-ne2000.catalog.json"


def project_config(driver_name: str) -> ProjectConfig:
    return ProjectConfig(
        project_id="intake-test",
        source_platform="linux",
        target_platform="asterinas",
        driver_name=driver_name,
        evaluation_mode=EvaluationMode.DEVELOPER_EVIDENCE,
        actor_role=ActorRole.DEVELOPER,
    )


class IntakeServiceTests(unittest.TestCase):
    def test_exact_driver_is_frozen_without_question(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", project_config("ne2k-pci"))
            result = IntakeService().analyze(
                project,
                raw_request="把 Linux ne2k-pci 迁移到星绽OS",
                catalog_paths=(NE2000_FIXTURE,),
            )
            self.assertEqual(result.status, IntakeStatus.FROZEN)
            self.assertEqual(result.selected_candidate_id, "linux-ne2k-pci")
            self.assertEqual(
                project.stage(IntakeStage.ENVELOPE_FREEZE).status,
                StageStatus.PASS,
            )
            envelope = IntakeService().show(project)["migration_envelope"]
            self.assertEqual(envelope["bus_or_transport"], "PCI")
            self.assertEqual(envelope["canonical_source_driver_name"], "ne2k-pci")

    def test_ambiguous_name_waits_once_then_resumes_same_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", project_config("NE2000"))
            service = IntakeService()
            result = service.analyze(
                project,
                raw_request="把 Linux NE2000 驱动迁移到星绽OS",
                catalog_paths=(NE2000_FIXTURE,),
            )
            self.assertEqual(result.status, IntakeStatus.WAITING_FOR_USER)
            self.assertEqual(len(result.candidates), 3)
            self.assertEqual(
                project.stage(IntakeStage.SCOPE_CONFIRMATION).status,
                StageStatus.WAITING_FOR_USER,
            )
            self.assertEqual(
                project.stage(AcquisitionStage.REVISION_SELECTION).status,
                StageStatus.PENDING,
            )
            with self.assertRaises(WorkflowError):
                service.analyze(project, raw_request="repeat")

            answered = service.answer(
                project,
                candidate_id="linux-ne2k-pci",
                answer_text="选择 PCI ne2k-pci，排除 ISA 和 PCMCIA",
            )
            self.assertEqual(answered.status, IntakeStatus.FROZEN)
            self.assertEqual(
                project.stage(AcquisitionStage.REVISION_SELECTION).status,
                StageStatus.READY,
            )
            shown = service.show(project)
            self.assertIn("ne:ISA", shown["migration_envelope"]["excluded_variants"])
            self.assertEqual(len(shown["resolution"]["metadata_sources"]), 1)
            self.assertEqual(len(shown["resolution"]["metadata_sources"][0]["digest"]), 64)
            self.assertEqual(shown["question"]["question_count"], 1)
            self.assertTrue(project.verify_event_chain())

    def test_unknown_driver_requires_explicit_manual_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", project_config("my-special-uart"))
            service = IntakeService()
            result = service.analyze(project, raw_request="迁移一个内部 UART 驱动")
            self.assertEqual(result.status, IntakeStatus.WAITING_FOR_USER)
            self.assertEqual(result.candidates, ())
            with self.assertRaises(WorkflowError):
                service.answer(
                    project,
                    candidate_id="manual",
                    answer_text="manual",
                )
            result = service.answer(
                project,
                candidate_id="manual-uart",
                answer_text="确认这个 MMIO UART 驱动",
                canonical_name="my-special-uart",
                source_path="drivers/tty/serial/my_uart.c",
                device_family="My UART",
                bus="MMIO",
            )
            self.assertEqual(result.status, IntakeStatus.FROZEN)


if __name__ == "__main__":
    unittest.main()
