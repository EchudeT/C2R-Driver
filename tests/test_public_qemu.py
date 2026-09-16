from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.cli import main
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.environment.contracts import EnvironmentArtifact, EnvironmentStage
from driver_port_factory.knowledge.contracts import KnowledgeDomain
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.migration.contracts import (
    EvidenceLadderLevel,
    MigrationArtifact,
    MigrationStage,
)
from tests.test_artifact_preparation import artifact_project, write_plan


def public_qemu_project(root: Path):
    project = artifact_project(root)
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port" / "references"
    (references / "qemu-evidence.md").write_text("# Public QEMU fixture\n", encoding="utf-8")
    if main(
        [
            "artifact-preparation",
            "run",
            str(project.root),
            "--plan",
            str(write_plan(project)),
        ]
    ):
        raise AssertionError("artifact preparation fixture did not finalize")
    return project


def qemu_reference(project) -> dict[str, str]:
    hit = KnowledgeIndex.for_project(project).search(
        "QEMU device model", domain=KnowledgeDomain.QEMU
    )["results"][0]
    return {"chunk_id": hit["chunk_id"], "record_id": hit["record_id"]}


def public_plan(project, *, checker_failure: bool = False, marker_only: bool = False) -> dict:
    identity = project.load_json_artifact(
        MigrationStage.ARTIFACT_PREPARATION,
        MigrationArtifact.ARTIFACT_IDENTITY,
    )
    runtime = project.artifact(
        MigrationStage.ARTIFACT_PREPARATION,
        MigrationArtifact.RUNTIME_ARTIFACT,
    )
    route = project.load_json_artifact(
        EnvironmentStage.RECOVERY,
        EnvironmentArtifact.EXPERIMENT_ROUTE,
    )
    artifact_path = str(project.artifacts.path_for_digest(runtime.digest))
    stimulus = project.root / "public-stimulus.py"
    checker = project.root / "public-checker.py"
    stimulus.write_text("raise SystemExit(0)\n", encoding="utf-8")
    expected = {
        "device_identity": route["device_identity"],
        "direction": "bidirectional",
        "length": 4,
        "payload_sha256": "a" * 64,
    }
    actual = {
        **expected,
        "artifact_sha256": runtime.digest,
        "driver_path_observed": True,
        "positive_control": True,
        "negative_control": True,
    }
    if marker_only:
        actual = {"driver_path_observed": True}
    checker.write_text(
        "import json\n"
        f"print(json.dumps({actual!r}))\n"
        f"raise SystemExit({1 if checker_failure else 0})\n",
        encoding="utf-8",
    )
    implementation = identity["inputs"][MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"]
    evidence = [qemu_reference(project)]

    def run(run_id: str) -> dict:
        command = {
            "environment": {},
            "timeout_seconds": 5,
            "accepted_exit_codes": [0],
        }
        return {
            "run_id": run_id,
            "purpose": "Execute the current migrated driver with external controls.",
            "contract_ids": ["example-driver-behavior"],
            "test_ids": ["example-source-test"],
            "artifact_sha256": runtime.digest,
            "implementation_sha256": implementation,
            "packaged_test_sha256": identity["packaged_test_artifact"]["sha256"],
            "device_identity": route["device_identity"],
            "topology": route["topology"],
            "cpu": "fixture-cpu",
            "memory": "64M",
            "backend": "fixture-backend",
            "cwd": ".",
            "qemu": {
                **command,
                "argv": [route["command"][0], *route["command"][1:], artifact_path],
            },
            "stimulus": {**command, "argv": [sys.executable, str(stimulus)]},
            "checker": {**command, "argv": [sys.executable, str(checker)]},
            "oracle": expected,
            "cleanup": "QMP quit terminates only the owned fixture process.",
            "qemu_evidence": evidence,
        }

    runs = [run("public-1"), run("public-2")]
    return {
        "schema_version": 1,
        "runs": runs,
        "ladder": [
            {
                "level": level.value,
                "disposition": "EXECUTE",
                "run_ids": ["public-1", "public-2"]
                if level is EvidenceLadderLevel.REGRESSION
                else ["public-1"],
                "rationale": "The fixture provides direct QMP and external checker evidence.",
                "qemu_evidence": evidence,
            }
            for level in EvidenceLadderLevel
        ],
    }


def run_public(project, document: dict) -> int:
    result = CodexResult("public-qemu-job", json.dumps(document), "public-qemu-thread")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result):
        return main(["public-qemu-validation", "run", str(project.root)])


class PublicQemuTests(unittest.TestCase):
    def test_artifact_qmp_checker_and_controls_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            self.assertEqual(run_public(project, public_plan(project)), 0)
            self.assertEqual(
                project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status.value,
                "PASS",
            )

    def test_marker_only_evidence_does_not_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            self.assertEqual(run_public(project, public_plan(project, marker_only=True)), 0)
            self.assertEqual(
                project.stage(MigrationStage.PUBLIC_QEMU_VALIDATION).status.value,
                "RUNNING",
            )

    def test_checker_failure_is_public_harness_not_driver_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = public_qemu_project(Path(temporary))
            self.assertEqual(run_public(project, public_plan(project, checker_failure=True)), 0)
            attempt = next((project.control / "public-qemu").glob("*/attempt.json"))
            run = json.loads(attempt.read_text(encoding="utf-8"))["runs"][0]
            self.assertEqual(run["attribution"], "PUBLIC_HARNESS")
            self.assertEqual(run["execution_status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
