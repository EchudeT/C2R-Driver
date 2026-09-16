from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.composition import initialize_project, open_project
from driver_port_factory.control.contracts import ControlStage
from driver_port_factory.core.events import StageEvent
from driver_port_factory.core.ledger import append_event
from driver_port_factory.core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    FileArtifact,
    GeneratedArtifact,
    OutputCardinality,
    ProjectConfig,
    StageStatus,
    WorkflowError,
    utc_now,
)
from driver_port_factory.intake.contracts import IntakeArtifact, IntakeStage
from driver_port_factory.intake.service import IntakeService
from driver_port_factory.migration.contracts import MigrationStage

NE2000_FIXTURE = Path(__file__).parents[1] / "examples" / "fixtures" / "linux-ne2000.catalog.json"


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
    @staticmethod
    def _event_count(project) -> int:
        with project._persistence._connect() as connection:
            return connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    @staticmethod
    def _inject_corrupt_output(project, stage, artifact: GeneratedArtifact) -> None:
        content = project.artifacts.put_bytes(artifact.data, kind=artifact.kind.value)
        with project._persistence._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifact_contents(digest, kind, size, cas_path, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (content.digest, content.kind, content.size, content.cas_path, utc_now()),
            )
            connection.execute(
                """
                INSERT INTO stage_artifacts(
                    stage_name, direction, ordinal, digest, kind, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage.value,
                    ArtifactDirection.OUTPUT.value,
                    0,
                    content.digest,
                    content.kind,
                    artifact.source,
                    utc_now(),
                ),
            )

    @staticmethod
    def _request_artifact() -> GeneratedArtifact:
        return GeneratedArtifact(
            IntakeArtifact.REQUEST_RECORD,
            (
                b'{"source_platform":"linux","target_platform":"asterinas",'
                b'"user_supplied_driver_name":"ne2k-pci","raw_request":"port",'
                b'"intake_status":"ANALYZING"}'
            ),
            "test:request",
        )

    def test_required_outputs_and_dependencies_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            self.assertEqual(project.stage(ControlStage.PROJECT_INIT).status, StageStatus.PASS)
            self.assertEqual(project.stage(IntakeStage.REQUEST).status, StageStatus.READY)
            self.assertEqual(
                project.stage(AcquisitionStage.REVISION_SELECTION).status,
                StageStatus.PENDING,
            )

            with self.assertRaises(WorkflowError):
                project.start(AcquisitionStage.REVISION_SELECTION)
            result = IntakeService().analyze(
                project,
                raw_request="Port Linux ne2k-pci to Asterinas",
                catalog_paths=(NE2000_FIXTURE,),
            )
            self.assertEqual(result.status.value, "FROZEN")
            self.assertEqual(
                project.stage(AcquisitionStage.REVISION_SELECTION).status,
                StageStatus.READY,
            )
            self.assertTrue(project.verify_event_chain())

    def test_project_reopen_restores_required_and_auxiliary_output_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            reopened = open_project(project.root)
            stage = reopened.stage(IntakeStage.SCOPE_CONFIRMATION)
            self.assertEqual(
                tuple(requirement.kind for requirement in stage.required_outputs),
                (IntakeArtifact.SCOPE_CONFIRMATION,),
            )
            self.assertEqual(
                stage.required_outputs[0].cardinality,
                OutputCardinality.EXACTLY_ONE,
            )
            self.assertIn(IntakeArtifact.CONFIRMATION_QUESTION, stage.auxiliary_outputs)

    def test_outputs_cannot_be_attached_before_stage_start(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            with self.assertRaises(WorkflowError):
                project.record_artifact(
                    AcquisitionStage.REVISION_SELECTION,
                    GeneratedArtifact(
                        AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
                        b"{}",
                        "test:too-early",
                    ),
                )

    def test_public_repair_accepts_failed_qemu_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            with project._persistence._connect() as connection:  # arrange a late-stage fixture
                connection.execute(
                    "UPDATE stages SET status = ? WHERE name = ?",
                    (StageStatus.FAIL.value, "public_qemu_validation"),
                )
            project._persistence.refresh_ready()
            self.assertEqual(
                project.stage(MigrationStage.PUBLIC_REPAIR).status,
                StageStatus.READY,
            )

    def test_success_finalization_rolls_back_as_one_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            events_before = self._event_count(project)

            def fail_on_completion(connection, event_type, payload):
                if event_type is StageEvent.COMPLETED:
                    raise RuntimeError("injected ledger failure")
                return append_event(connection, event_type, payload)

            with (
                patch(
                    "driver_port_factory.core.store.append_event",
                    side_effect=fail_on_completion,
                ),
                self.assertRaisesRegex(RuntimeError, "injected ledger failure"),
            ):
                project.finalize_stage(
                    IntakeStage.REQUEST,
                    (self._request_artifact(),),
                )

            self.assertEqual(
                project.stage(IntakeStage.REQUEST).status,
                StageStatus.RUNNING,
            )
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])
            self.assertEqual(self._event_count(project), events_before)
            project.finalize_stage(IntakeStage.REQUEST, (self._request_artifact(),))
            self.assertEqual(
                project.stage(IntakeStage.REQUEST).status,
                StageStatus.PASS,
            )

    def test_required_output_cannot_be_pre_registered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            with self.assertRaisesRegex(WorkflowError, "complete finalization"):
                project.record_artifact(IntakeStage.REQUEST, self._request_artifact())
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])

    def test_direct_pass_transition_is_not_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            with self.assertRaisesRegex(WorkflowError, "PASS"):
                project.complete(IntakeStage.REQUEST, StageStatus.PASS)

    def test_wrong_domain_output_is_rejected_by_stage_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            wrong = GeneratedArtifact(
                AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
                b"{}",
                "test:wrong-domain",
            )
            with self.assertRaisesRegex(WorkflowError, "not declared"):
                project.record_artifact(IntakeStage.REQUEST, wrong)
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])

    def test_final_bundle_rejects_required_plus_wrong_domain_without_db_side_effects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            events_before = self._event_count(project)
            wrong = GeneratedArtifact(
                AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
                b"{}",
                "test:wrong-domain",
            )
            with self.assertRaisesRegex(
                WorkflowError, "unexpected: repository_acquisition_attempt"
            ):
                project.finalize_stage(IntakeStage.REQUEST, (self._request_artifact(), wrong))
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])
            self.assertEqual(
                project.stage(IntakeStage.REQUEST).status,
                StageStatus.RUNNING,
            )
            self.assertEqual(self._event_count(project), events_before)

    def test_injected_wrong_output_is_rejected_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            wrong = GeneratedArtifact(
                AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
                b"{}",
                "test:injected-wrong-domain",
            )
            self._inject_corrupt_output(project, IntakeStage.REQUEST, wrong)
            refs_before = project.artifact_refs(stage=IntakeStage.REQUEST)
            events_before = self._event_count(project)
            with self.assertRaisesRegex(WorkflowError, "invalid pre-finalization outputs"):
                project.finalize_stage(IntakeStage.REQUEST, (self._request_artifact(),))
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), refs_before)
            self.assertEqual(
                project.stage(IntakeStage.REQUEST).status,
                StageStatus.RUNNING,
            )
            self.assertEqual(self._event_count(project), events_before)

    def test_incomplete_bundle_cannot_reuse_injected_required_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            with project._persistence._connect() as connection:
                connection.execute(
                    "UPDATE stages SET status = ? WHERE name = ?",
                    (StageStatus.READY.value, AcquisitionStage.REVISION_SELECTION.value),
                )
            project.start(AcquisitionStage.REVISION_SELECTION)
            revision = GeneratedArtifact(
                AcquisitionArtifact.REVISION_MANIFEST,
                b"{}",
                "test:injected-required",
            )
            self._inject_corrupt_output(project, AcquisitionStage.REVISION_SELECTION, revision)
            plan = GeneratedArtifact(
                AcquisitionArtifact.REPOSITORY_PLAN,
                b"{}",
                "test:incomplete-current-bundle",
            )
            refs_before = project.artifact_refs(stage=AcquisitionStage.REVISION_SELECTION)
            events_before = self._event_count(project)
            with self.assertRaisesRegex(WorkflowError, "revision_manifest: expected exactly one"):
                project.finalize_stage(AcquisitionStage.REVISION_SELECTION, (plan,))
            self.assertEqual(
                project.artifact_refs(stage=AcquisitionStage.REVISION_SELECTION),
                refs_before,
            )
            self.assertEqual(self._event_count(project), events_before)

    def test_duplicate_singleton_required_output_is_rejected_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            first = self._request_artifact()
            second = GeneratedArtifact(
                IntakeArtifact.REQUEST_RECORD,
                first.data + b"\n",
                "test:duplicate-request",
            )
            events_before = self._event_count(project)
            with self.assertRaisesRegex(WorkflowError, "expected exactly one, found 2"):
                project.finalize_stage(IntakeStage.REQUEST, (first, second))
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])
            self.assertEqual(self._event_count(project), events_before)

    def test_public_api_cannot_bypass_artifact_validation_or_success_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            self.assertFalse(hasattr(project, "store"))
            self.assertFalse(hasattr(project._persistence, "finalize_stage_artifacts"))
            project.start(IntakeStage.REQUEST)
            invalid = GeneratedArtifact(
                IntakeArtifact.REQUEST_RECORD,
                b"not-json",
                "test:invalid-request",
            )
            with self.assertRaises(WorkflowError):
                project.finalize_stage(IntakeStage.REQUEST, (invalid,))
            self.assertEqual(project.stage(IntakeStage.REQUEST).status, StageStatus.RUNNING)
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])

    def test_file_artifact_cannot_read_outside_project_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = initialize_project(root / "run", config())
            external = root / "outside.json"
            external.write_bytes(self._request_artifact().data)
            project.start(IntakeStage.REQUEST)
            with self.assertRaisesRegex(WorkflowError, "escapes project root"):
                project.finalize_stage(
                    IntakeStage.REQUEST,
                    (FileArtifact(IntakeArtifact.REQUEST_RECORD, external),),
                )

    def test_conflicting_content_metadata_rolls_back_finalization_and_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            project.start(IntakeStage.REQUEST)
            artifact = self._request_artifact()
            content = project.artifacts.put_bytes(artifact.data, kind=artifact.kind.value)
            with project._persistence._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO artifact_contents(digest, kind, size, cas_path, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        content.digest,
                        content.kind,
                        content.size,
                        "objects/sha256/conflicting-path",
                        utc_now(),
                    ),
                )
            events_before = self._event_count(project)
            with self.assertRaisesRegex(WorkflowError, "metadata conflicts"):
                project.finalize_stage(IntakeStage.REQUEST, (artifact,))
            self.assertEqual(project.artifact_refs(stage=IntakeStage.REQUEST), [])
            self.assertEqual(project.stage(IntakeStage.REQUEST).status, StageStatus.RUNNING)
            self.assertEqual(self._event_count(project), events_before)

    def test_open_fails_closed_for_stage_spec_and_occurrence_tampering(self) -> None:
        mutations = {
            "position": 999,
            "description": "tampered",
            "owner": "independent",
            "dependencies": json.dumps(["tampered"]),
            "required_outputs": json.dumps([]),
            "auxiliary_outputs": json.dumps(["tampered"]),
            "allowed_roles": json.dumps(["auditor"]),
            "accept_failed_dependencies": 1,
        }
        for field, value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                project = initialize_project(Path(temporary) / "run", config())
                with project._persistence._connect() as connection:
                    connection.execute(
                        f"UPDATE stages SET {field} = ? WHERE name = ?",
                        (value, IntakeStage.REQUEST.value),
                    )
                with self.assertRaises(WorkflowError):
                    open_project(project.root)

        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            with project._persistence._connect() as connection:
                connection.execute(
                    "DELETE FROM stage_artifacts WHERE stage_name = ?",
                    (ControlStage.PROJECT_INIT.value,),
                )
            with self.assertRaises(WorkflowError):
                open_project(project.root)

    def test_stage_read_wraps_malformed_persisted_json_as_workflow_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            with project._persistence._connect() as connection:
                connection.execute(
                    "UPDATE stages SET dependencies = ? WHERE name = ?",
                    ("not-json", IntakeStage.REQUEST.value),
                )
            with self.assertRaisesRegex(WorkflowError, "invalid JSON"):
                project.stage(IntakeStage.REQUEST)

    def test_open_rejects_project_config_changes_even_when_semantics_are_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            original = project.config_path.read_text(encoding="utf-8")
            project.config_path.write_text(original + "\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "frozen project manifest"):
                open_project(project.root)

    def test_open_rejects_project_config_that_differs_from_run_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            value = json.loads(project.config_path.read_text(encoding="utf-8"))
            value["driver_name"] = "different-driver"
            project.config_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "differs from run database"):
                open_project(project.root)

    def test_open_rejects_corrupted_cas_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = initialize_project(Path(temporary) / "run", config())
            manifest = next(
                ref for ref in project.artifact_refs() if ref.source == str(project.config_path)
            )
            (project.control / "cas" / manifest.cas_path).write_bytes(b"corrupt")
            with self.assertRaisesRegex(WorkflowError, "artifact"):
                open_project(project.root)


if __name__ == "__main__":
    unittest.main()
