from __future__ import annotations

import hashlib
import json
import stat
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.cli import main
from driver_port_factory.composition import initialize_project
from driver_port_factory.core.models import (
    ActorRole,
    EvaluationMode,
    GeneratedArtifact,
    StageStatus,
)
from driver_port_factory.evaluation.contracts import EvaluationArtifact, EvaluationStage
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.sealing.candidate import CandidateSealer, SealRequest
from driver_port_factory.sealing.contracts import SealingArtifact, SealingStage
from tests.test_public_qemu import public_plan, public_qemu_project, run_public


def blind_project(root: Path):
    def initialize(root_path, config):
        project = initialize_project(
            root_path,
            replace(
                config,
                evaluation_mode=EvaluationMode.PROSPECTIVE_BLIND,
                actor_role=ActorRole.MIGRATION_OPERATOR,
            ),
        )
        project.start(EvaluationStage.BLIND_BINDING)
        project.finalize_stage(
            EvaluationStage.BLIND_BINDING,
            (
                GeneratedArtifact(
                    EvaluationArtifact.PUBLIC_BUNDLE,
                    b'{"schema_version":1,"public":"fixture"}\n',
                    "fixture:public-bundle",
                ),
                GeneratedArtifact(
                    EvaluationArtifact.CURATOR_COMMITMENT,
                    b'{"schema_version":1,"commitment":"fixture"}\n',
                    "fixture:curator-commitment",
                ),
            ),
        )
        return project

    with patch("tests.test_knowledge.initialize_project", side_effect=initialize):
        project = public_qemu_project(root)
    if run_public(project, public_plan(project)) != 0:
        raise AssertionError("public QEMU fixture did not finalize")
    if main(["public-repair", "run", str(project.root)]) != 0:
        raise AssertionError("public repair fixture did not finalize")
    return project


def request_document(project, attempt_id: str) -> dict:
    return {
        "schema_version": 1,
        "experiment_id": "experiment-1",
        "task_id": "task-1",
        "attempt_id": attempt_id,
        "batch_id": "batch-1",
        "control_plane_version": "control-1",
        "migration_started_at": project.config.created_at,
        "public_bundle_sha256": project.artifact(
            EvaluationStage.BLIND_BINDING, EvaluationArtifact.PUBLIC_BUNDLE
        ).digest,
        "pmc_sha256": project.artifact(
            MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS
        ).digest,
        "candidate_format": "TAR",
        "frozen_migrator": {
            "identity": "fixture-migrator",
            "version": "1",
            "rules_version": "fixture-rules",
            "prompt_budget": 1000,
        },
        "human_interventions": [],
        "private_material_access": False,
        "no_cross_task_update": True,
    }


def seal_inputs(project, attempt_id: str, *, matching: bool = True) -> tuple[Path, Path]:
    request_path = project.root / f"{attempt_id}-request.json"
    request_path.write_text(json.dumps(request_document(project, attempt_id)), encoding="utf-8")
    project.start(SealingStage.CANDIDATE_SEALING)
    sealer = CandidateSealer()
    request = SealRequest.read(request_path)
    occurrences = sealer._occurrences(project)
    entities = sealer._entities(project, occurrences, request)
    manifest = sealer._entity_manifest(project, request, entities)
    digest = hashlib.sha256(
        sealer._archive({"manifest.json": _json(manifest), **entities})
    ).hexdigest()
    receipt_path = project.root / f"{attempt_id}-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_sha256": digest if matching else "0" * 64,
                "kind": "TRUSTED_TIMESTAMP",
                "authority": "fixture-tsa",
                "receipt_id": f"receipt-{attempt_id}",
                "issued_at": "2026-09-16T00:00:00Z",
                "proof": "fixture-external-proof",
            }
        ),
        encoding="utf-8",
    )
    return request_path, receipt_path


def run_seal(project, request: Path, receipt: Path, output: Path) -> int:
    return main(
        [
            "candidate",
            "seal",
            str(project.root),
            "--request",
            str(request),
            "--timestamp-receipt",
            str(receipt),
            "--output",
            str(output),
        ]
    )


class CandidateSealingTests(unittest.TestCase):
    def test_entities_digest_ledger_receipt_and_read_only_transfer_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = blind_project(Path(temporary))
            request, receipt = seal_inputs(project, "attempt-1")
            output = project.root / "evaluator-transfer"
            self.assertEqual(run_seal(project, request, receipt, output), 0)
            self.assertEqual(project.stage(SealingStage.CANDIDATE_SEALING).status, StageStatus.PASS)
            bundle = output / "candidate.tar"
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE(bundle.stat().st_mode), 0o444)
            self.assertEqual(
                hashlib.sha256(bundle.read_bytes()).hexdigest(),
                project.load_json_artifact(
                    SealingStage.CANDIDATE_SEALING, SealingArtifact.CANDIDATE_MANIFEST
                )["candidate_sha256"],
            )

    def test_receipt_mismatch_does_not_seal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = blind_project(Path(temporary))
            request, receipt = seal_inputs(project, "attempt-1", matching=False)
            self.assertEqual(
                run_seal(project, request, receipt, project.root / "evaluator-transfer"), 2
            )
            self.assertEqual(
                project.stage(SealingStage.CANDIDATE_SEALING).status, StageStatus.RUNNING
            )
            self.assertEqual(project.artifact_refs(stage=SealingStage.CANDIDATE_SEALING), [])

    def test_sealed_attempt_cannot_be_overwritten_after_source_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = blind_project(Path(temporary))
            request, receipt = seal_inputs(project, "attempt-1")
            output = project.root / "evaluator-transfer"
            self.assertEqual(run_seal(project, request, receipt, output), 0)
            original = (output / "candidate.tar").read_bytes()
            worktree = CandidateSealer._worktree(project)
            driver = next(worktree.rglob("*.rs"))
            driver.write_text(driver.read_text(encoding="utf-8") + "// changed\n", encoding="utf-8")
            self.assertEqual(run_seal(project, request, receipt, output), 2)
            self.assertEqual((output / "candidate.tar").read_bytes(), original)


def _json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


if __name__ == "__main__":
    unittest.main()
