from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.cli import main
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus
from driver_port_factory.knowledge.contracts import KnowledgeDomain
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.target_study.contracts import TargetStudyArtifact, TargetStudyStage
from tests.test_migration_contracts import (
    contract_project,
    contract_response,
    run_with_response,
)


def adaptation_project(root: Path):
    project = contract_project(root)
    if run_with_response(project, contract_response(project)) != 0:
        raise AssertionError("contract fixture did not finalize")
    skill = Path(project.config.skill_root) / "knowledge-guided-driver-port" / "references"
    for name in ("workflow.md", "test-porting.md"):
        (skill / name).write_text("# Test adaptation fixture\n", encoding="utf-8")
    return project


def reference(project, query: str, domain: KnowledgeDomain) -> dict[str, str]:
    hit = KnowledgeIndex.for_project(project).search(query, domain=domain)["results"][0]
    return {
        "domain": domain.value,
        "chunk_id": hit["chunk_id"],
        "record_id": hit["record_id"],
    }


def source_mapping(project) -> dict:
    target = project.load_json_artifact(
        TargetStudyStage.STUDY,
        TargetStudyArtifact.API_EVIDENCE,
    )["entries"][0]["definition_evidence"]
    return {
        "test_id": "example-source-test",
        "origin": "SOURCE_TEST",
        "source_test": "tests/example-driver-test.c",
        "primary_class": "PORTABLE_INTENT_PLATFORM_HARNESS",
        "disposition": "ADAPT",
        "rationale": "Device intent is portable while the source harness is not.",
        "evidence": [
            reference(project, "source test for the example device", KnowledgeDomain.SOURCE)
        ],
        "contract_ids": ["example-driver-behavior"],
        "original_command": "source-test-runner example-driver-test",
        "setup": "Create the example device and initialize the driver.",
        "stimulus": "Issue the source test's example device operation.",
        "oracle": "The operation completes with the expected device result.",
        "boundary_cases": ["single operation"],
        "negative_paths": ["initialization failure"],
        "cleanup": "Stop the device and release driver-owned resources.",
        "source_only_assertions_removed": ["source framework bookkeeping"],
        "adapter": {
            "required": True,
            "description": "Use the thinnest target driver harness.",
            "target_evidence": [
                {
                    "domain": KnowledgeDomain.TARGET.value,
                    "chunk_id": target["chunk_id"],
                    "record_id": target["record_id"],
                }
            ],
        },
        "limitations": [],
        "expected_result": "The portable device assertion passes.",
        "execution_status": "NOT_RUN",
        "public_developer_evidence": True,
    }


def response(mapping: dict) -> dict:
    return {"schema_version": 1, "tests": [mapping]}


def run_adaptation(project, document: dict) -> int:
    result = CodexResult("test-matrix-job", json.dumps(document), "test-matrix-thread")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result):
        return main(["test-adaptation", "run", str(project.root)])


class TestAdaptationTests(unittest.TestCase):
    def test_portable_harness_is_adapted_and_retained(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = adaptation_project(Path(temporary))
            self.assertEqual(run_adaptation(project, response(source_mapping(project))), 0)
            self.assertEqual(project.stage(MigrationStage.TEST_ADAPTATION).status, StageStatus.PASS)
            matrix = project.load_json_artifact(
                MigrationStage.TEST_ADAPTATION,
                MigrationArtifact.TEST_PORT_MATRIX,
            )
            self.assertEqual(matrix["tests"][0]["disposition"], "ADAPT")

    def test_source_only_cannot_be_retained_and_omission_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = adaptation_project(Path(temporary))
            source_only = source_mapping(project)
            source_only["primary_class"] = "SOURCE_PLATFORM_SEMANTICS"
            source_only["disposition"] = "RETAIN"
            source_only["adapter"] = {
                "required": False,
                "description": "No target adapter applies.",
                "target_evidence": [],
            }
            source_only["execution_status"] = "NOT_APPLICABLE"
            self.assertEqual(run_adaptation(project, response(source_only)), 2)

            omitted = copy.deepcopy(source_mapping(project))
            omitted["test_id"] = "new-test"
            omitted["origin"] = "NEW_MIGRATION_TEST"
            omitted["source_test"] = None
            self.assertEqual(run_adaptation(project, response(omitted)), 2)

            source_only["disposition"] = "EXCLUDE"
            self.assertEqual(run_adaptation(project, response(source_only)), 0)
            matrix = project.load_json_artifact(
                MigrationStage.TEST_ADAPTATION,
                MigrationArtifact.TEST_PORT_MATRIX,
            )
            self.assertEqual(matrix["tests"][0]["disposition"], "EXCLUDE")


if __name__ == "__main__":
    unittest.main()
