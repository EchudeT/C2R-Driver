from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import patch

from driver_port_factory.acquisition.repository import load_repository_acquisition
from driver_port_factory.cli import main
from driver_port_factory.codex.gateway import CodexResult
from driver_port_factory.core.models import StageStatus
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.migration.implementation import FACT_DOMAINS, INDEX_FACTS
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.target_study.contracts import TargetStudyArtifact, TargetStudyStage
from tests.test_test_adaptation import (
    adaptation_project,
    run_adaptation,
    source_mapping,
)
from tests.test_test_adaptation import (
    response as adaptation_response,
)

DRIVER_PATH = "kernel/src/driver/example/mod.rs"
TEST_PATH = "kernel/src/driver/example/tests.rs"


def implementation_project(root: Path):
    project = adaptation_project(root)
    references = Path(project.config.skill_root) / "knowledge-guided-driver-port" / "references"
    (references / "target-changes.md").write_text("# Target changes fixture\n", encoding="utf-8")
    if run_adaptation(project, adaptation_response(source_mapping(project))) != 0:
        raise AssertionError("test-adaptation fixture did not finalize")
    return project


def write_implementation(project, *, modify_target: bool = False) -> tuple[Path, list[dict]]:
    acquisition = load_repository_acquisition(project)
    worktree = project.root / acquisition.target_worktree.path
    sources = {
        DRIVER_PATH: (
            "pub struct ExampleDriver;\n"
            "impl ExampleDriver {\n"
            "    pub fn initialize() -> Self { Self }\n"
            "}\n"
        ),
        TEST_PATH: (
            "#[test]\n"
            "fn initializes() {\n"
            "    let _driver = super::ExampleDriver::initialize();\n"
            "}\n"
        ),
    }
    roles = {DRIVER_PATH: "DRIVER", TEST_PATH: "PUBLIC_TEST"}
    if modify_target:
        sources["Cargo.toml"] = '[workspace]\nmembers = ["kernel/src/driver/example"]\n'
        roles["Cargo.toml"] = "INTEGRATION"
    files = []
    for relative, content in sources.items():
        path = worktree / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        files.append(
            {
                "path": relative,
                "role": roles[relative],
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
            }
        )
    return worktree, files


def implementation_response(project, files: list[dict], *, modify_target: bool = False) -> dict:
    facts = project.load_json_artifact(
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
    )
    grouped: dict[object, set[str]] = defaultdict(set)
    for unit in facts["units"]:
        semantic = json.loads(
            (project.root / unit["semantic_index"]["path"]).read_text(encoding="utf-8")
        )
        for kind, index_name in INDEX_FACTS.items():
            if semantic["indexes"][index_name]:
                grouped[FACT_DOMAINS[kind]].add(str(unit["unit_id"]))
    coverage = [
        {
            "coverage_id": f"source-{index}",
            "domain": domain.value,
            "unit_ids": sorted(unit_ids),
            "target": {"path": DRIVER_PATH, "line_start": 1, "line_end": 4},
            "contract_ids": ["example-driver-behavior"],
            "test_ids": [],
            "lowering": "Reconstruct the evidenced source behavior in the target driver state.",
            "assumptions": [],
            "safety_obligation_ids": [],
            "diagnostics": [],
            "status": "TRANSLATED",
        }
        for index, (domain, unit_ids) in enumerate(grouped.items(), start=1)
    ]
    coverage.append(
        {
            "coverage_id": "public-test-example",
            "domain": "TEST_ASSERTION",
            "unit_ids": [],
            "target": {"path": TEST_PATH, "line_start": 1, "line_end": 4},
            "contract_ids": ["example-driver-behavior"],
            "test_ids": ["example-source-test"],
            "lowering": "Adapt the portable source test intent to the target driver API.",
            "assumptions": [],
            "safety_obligation_ids": [],
            "diagnostics": [],
            "status": "TRANSLATED",
        }
    )
    api = project.load_json_artifact(
        TargetStudyStage.STUDY,
        TargetStudyArtifact.API_EVIDENCE,
    )["entries"][0]
    return {
        "schema_version": 1,
        "files": files,
        "coverage": coverage,
        "target_changes": (
            [
                {
                    "path": "Cargo.toml",
                    "change_id": "unplanned-integration",
                    "status": "TARGET_CHANGE_PLANNED",
                }
            ]
            if modify_target
            else []
        ),
        "target_symbols": [
            {
                "symbol": api["api_or_type"],
                "api_id": api["api_id"],
                "definition_evidence": api["definition_evidence"],
                "call_site_evidence": api["call_site_evidence"],
            }
        ],
        "unsafe_obligations": [],
    }


def run_implementation(project, document: dict) -> int:
    result = CodexResult("implementation-job", json.dumps(document), "implementation-thread")
    with patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result):
        return main(["driver-implementation", "run", str(project.root)])


class DriverImplementationTests(unittest.TestCase):
    def test_complete_driver_owned_bundle_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = implementation_project(Path(temporary))
            _worktree, files = write_implementation(project)

            self.assertEqual(
                run_implementation(project, implementation_response(project, files)), 0
            )
            self.assertEqual(
                project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status,
                StageStatus.PASS,
            )
            bundle = project.load_json_artifact(
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
            )
            self.assertEqual({item["path"] for item in bundle["files"]}, {DRIVER_PATH, TEST_PATH})
            coverage = project.load_json_artifact(
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationArtifact.TRANSLATION_COVERAGE,
            )["coverage"]
            self.assertTrue(
                all(item["source_facts"] and item["source_spans"] for item in coverage[:-1])
            )

    def test_missing_source_fact_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = implementation_project(Path(temporary))
            _worktree, files = write_implementation(project)
            document = implementation_response(project, files)
            document["coverage"].pop(0)

            self.assertEqual(run_implementation(project, document), 2)
            self.assertEqual(
                project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status,
                StageStatus.RUNNING,
            )

    def test_unplanned_preexisting_target_change_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = implementation_project(Path(temporary))
            _worktree, files = write_implementation(project, modify_target=True)
            document = implementation_response(project, files, modify_target=True)

            self.assertEqual(run_implementation(project, document), 2)
            self.assertEqual(
                project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status,
                StageStatus.RUNNING,
            )


if __name__ == "__main__":
    unittest.main()
