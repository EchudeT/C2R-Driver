from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.cli import main
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus
from driver_port_factory.migration.contracts import (
    ComplianceArea,
    MigrationArtifact,
    MigrationStage,
)
from driver_port_factory.target_study.contracts import TargetStudyArtifact, TargetStudyStage
from tests.test_driver_implementation import (
    DRIVER_PATH,
    TEST_PATH,
    implementation_project,
    implementation_response,
    run_implementation,
    write_implementation,
)


def compliance_project(root: Path):
    project = implementation_project(root)
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port" / "references"
    (references / "target-platform-study.md").write_text(
        "# Target platform study fixture\n", encoding="utf-8"
    )
    _worktree, files = write_implementation(project)
    if run_implementation(project, implementation_response(project, files)) != 0:
        raise AssertionError("driver implementation fixture did not finalize")
    return project


def compliance_response(project) -> dict:
    api = project.load_json_artifact(
        TargetStudyStage.STUDY,
        TargetStudyArtifact.API_EVIDENCE,
    )["entries"][0]
    evidence = [api["definition_evidence"]]

    def review(identifier: str, *, paths: list[str], citations: list[dict]) -> dict:
        return {
            "status": "PASS",
            "repair_target": "NONE",
            "summary": f"Pinned target originals establish compliance for {identifier}.",
            "implementation_paths": paths,
            "target_evidence": citations,
            "unsafe_obligation_ids": [],
            "details": {},
        }

    return {
        "schema_version": 1,
        "status": "PASS",
        "areas": [
            {
                "area": area.value,
                **review(area.value, paths=[DRIVER_PATH, TEST_PATH], citations=evidence),
            }
            for area in ComplianceArea
        ],
        "apis": [
            {
                "api_id": api["api_id"],
                **review(
                    api["api_id"],
                    paths=[DRIVER_PATH],
                    citations=[api["definition_evidence"], api["call_site_evidence"]],
                ),
            }
        ],
        "target_changes": [],
        "execution": {"compile": "NOT_RUN", "runtime": "NOT_RUN"},
    }


def run_compliance(project, document: dict) -> int:
    result = CodexResult("compliance-job", json.dumps(document), "compliance-thread")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result):
        return main(["target-compliance", "run", str(project.root)])


class TargetComplianceTests(unittest.TestCase):
    def test_target_original_citations_finalize_clean_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = compliance_project(Path(temporary))

            self.assertEqual(run_compliance(project, compliance_response(project)), 0)
            self.assertEqual(
                project.stage(MigrationStage.TARGET_COMPLIANCE).status,
                StageStatus.PASS,
            )
            report = project.load_json_artifact(
                MigrationStage.TARGET_COMPLIANCE,
                MigrationArtifact.COMPLIANCE_REPORT,
            )
            self.assertEqual(report["execution"], {"compile": "NOT_RUN", "runtime": "NOT_RUN"})

    def test_uncited_review_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = compliance_project(Path(temporary))
            response = compliance_response(project)
            response["areas"][0]["target_evidence"] = []

            self.assertEqual(run_compliance(project, response), 2)
            self.assertEqual(
                project.stage(MigrationStage.TARGET_COMPLIANCE).status,
                StageStatus.RUNNING,
            )


if __name__ == "__main__":
    unittest.main()
