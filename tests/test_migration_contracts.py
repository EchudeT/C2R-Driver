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
from driver_port_factory.migration.contract_set import MigrationContractGate
from driver_port_factory.migration.contracts import MigrationArtifact, MigrationStage
from driver_port_factory.source_analysis.closure import SourceClosureService
from driver_port_factory.source_analysis.contracts import (
    SourceAnalysisArtifact,
    SourceAnalysisStage,
)
from driver_port_factory.source_analysis.structured import StructuredCAnalysisService
from driver_port_factory.target_study.contracts import TargetStudyArtifact, TargetStudyStage
from tests.test_source_closure import ready_project, source_closure, write_json


def contract_project(root: Path):
    project, checkouts = ready_project(root)
    skill = Path(project.config.skill_root) / "knowledge-guided-driver-port"
    for relative in ("SKILL.md", "references/translation.md", "references/knowledge-contract.md"):
        document = skill / relative
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("# Contract fixture\n", encoding="utf-8")
    SourceClosureService().validate(
        project,
        closure_path=write_json(
            project.root / "source-closure.json",
            source_closure(project, checkouts),
        ),
    )
    result = StructuredCAnalysisService().analyze(project)
    if result.status.value != "READY":
        raise AssertionError(result.errors)
    return project


def evidence_ref(index: KnowledgeIndex, query: str, domain: KnowledgeDomain) -> dict[str, str]:
    hit = index.search(query, domain=domain)["results"][0]
    return {"chunk_id": hit["chunk_id"], "record_id": hit["record_id"]}


def contract_response(project) -> dict:
    index = KnowledgeIndex.for_project(project)
    api = project.load_json_artifact(
        TargetStudyStage.STUDY,
        TargetStudyArtifact.API_EVIDENCE,
    )["entries"][0]
    facts = project.load_json_artifact(
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
    )
    functions = []
    for unit in facts["units"]:
        semantic = json.loads(
            (project.root / unit["semantic_index"]["path"]).read_text(encoding="utf-8")
        )
        functions.extend(
            {"unit_id": unit["unit_id"], "node_id": node_id}
            for node_id in semantic["indexes"]["function_definitions"]
        )
    return {
        "schema_version": 1,
        "contracts": [
            {
                "id": "example-driver-behavior",
                "requirement": "Initialize and expose the example device behavior.",
                "evidence": {
                    "hardware": [
                        evidence_ref(index, "example driver source entry", KnowledgeDomain.SOURCE)
                    ],
                    "source": [
                        evidence_ref(index, "example driver source entry", KnowledgeDomain.SOURCE)
                    ],
                    "target": [
                        {
                            "api_id": api["api_id"],
                            "definition": api["definition_evidence"],
                            "call_site": api["call_site_evidence"],
                        }
                    ],
                    "qemu": [evidence_ref(index, "QEMU device model", KnowledgeDomain.QEMU)],
                },
                "source_function_refs": functions,
                "rust_design_intent": "Own the initialized device behind the target driver API.",
                "verification": {
                    "kind": "QEMU",
                    "argv": ["qemu-system-test", "--device", "example"],
                    "oracle": "The driver initializes and the device operation completes.",
                },
                "evidence_status": "VERIFIED",
                "execution_status": "NOT_RUN",
            }
        ],
        "gaps": [],
    }


def run_with_response(project, response: dict) -> int:
    result = CodexResult("contract-job", json.dumps(response), "contract-thread")
    original = MigrationContractGate._verify_reference

    def fixture_hardware(gate, reference, domain, knowledge):
        if domain is KnowledgeDomain.HARDWARE:
            return {"chunk_id": reference["chunk_id"], "record_id": reference["record_id"]}
        return original(gate, reference, domain, knowledge)

    with (
        patch("driver_port_factory.codex.cli.CodexExecGateway.run", return_value=result),
        patch.object(MigrationContractGate, "_verify_reference", new=fixture_hardware),
    ):
        return main(["migration-contracts", "run", str(project.root)])


class MigrationContractTests(unittest.TestCase):
    def test_four_domain_contract_finalizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = contract_project(Path(temporary))
            self.assertEqual(run_with_response(project, contract_response(project)), 0)
            self.assertEqual(project.stage(MigrationStage.CONTRACTS).status, StageStatus.PASS)
            frozen = project.load_json_artifact(
                MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS
            )
            self.assertEqual(frozen["contracts"][0]["execution_status"], "NOT_RUN")

    def test_missing_target_original_and_forged_execution_cannot_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = contract_project(Path(temporary))
            response = contract_response(project)
            del response["contracts"][0]["evidence"]["target"][0]["call_site"]
            self.assertEqual(run_with_response(project, response), 2)
            self.assertEqual(project.stage(MigrationStage.CONTRACTS).status, StageStatus.RUNNING)

            response = copy.deepcopy(contract_response(project))
            response["contracts"][0]["execution_status"] = "PASS"
            self.assertEqual(run_with_response(project, response), 2)
            self.assertEqual(project.stage(MigrationStage.CONTRACTS).status, StageStatus.RUNNING)


if __name__ == "__main__":
    unittest.main()
