from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from driver_port_factory.acquisition.closure import EvidenceClosurePlan
from driver_port_factory.acquisition.closure_artifacts import (
    EvidenceCoverageInventory,
    EvidenceGapRegister,
    EvidenceMaterialsManifest,
    EvidenceRetrievalLedger,
)
from driver_port_factory.acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from driver_port_factory.acquisition.repository import RepositoryAcquirer
from driver_port_factory.codex.prompts import default_prompt_pack_path
from tests.acquisition_support import close_evidence
from tests.test_acquisition import ready_project


class EvidenceArtifactSchemaTests(unittest.TestCase):
    def test_final_artifacts_conform_to_five_strict_metadata_schemas(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project, _, _, _ = ready_project(Path(temporary))
            RepositoryAcquirer().acquire(project)
            close_evidence(project)
            validators = schema_validators()
            parsers = {
                AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN: EvidenceClosurePlan.from_dict,
                AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY: (
                    EvidenceCoverageInventory.from_dict
                ),
                AcquisitionArtifact.EVIDENCE_GAP_REGISTER: EvidenceGapRegister.from_dict,
                AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER: EvidenceRetrievalLedger.from_dict,
            }
            for artifact, parser in parsers.items():
                with self.subTest(artifact=artifact.value):
                    document = project.load_json_artifact(
                        AcquisitionStage.EVIDENCE_CLOSURE,
                        artifact,
                    )
                    validators[artifact].validate(document)
                    parser(document)
            materials = project.artifacts.read(
                project.artifact(
                    AcquisitionStage.EVIDENCE_CLOSURE,
                    AcquisitionArtifact.MATERIALS_MANIFEST,
                )
            )
            material_documents = tuple(
                json.loads(line) for line in materials.decode("utf-8").splitlines() if line
            )
            self.assertTrue(material_documents)
            for document in material_documents:
                validators[AcquisitionArtifact.MATERIALS_MANIFEST].validate(document)
            EvidenceMaterialsManifest.from_bytes(materials)


def schema_validators() -> dict[AcquisitionArtifact, Draft202012Validator]:
    prompt_schema = default_prompt_pack_path() / "evidence-closure-proposal.schema.json"
    schema_root = default_prompt_pack_path().parent.parent / "schemas" / "acquisition"
    paths = (*schema_root.glob("*.json"), prompt_schema)
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    registry: Registry = Registry()
    for document in documents:
        Draft202012Validator.check_schema(document)
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    contracts = {
        AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN: "evidence-closure-plan.schema.json",
        AcquisitionArtifact.MATERIALS_MANIFEST: "evidence-material-record.schema.json",
        AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY: (
            "evidence-coverage-inventory.schema.json"
        ),
        AcquisitionArtifact.EVIDENCE_GAP_REGISTER: "evidence-gap-register.schema.json",
        AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER: ("evidence-retrieval-ledger.schema.json"),
    }
    return {
        artifact: Draft202012Validator(
            next(
                document
                for document in documents
                if document.get("$id", "").endswith(filename.removesuffix(".schema.json"))
            ),
            registry=registry,
        )
        for artifact, filename in contracts.items()
    }


if __name__ == "__main__":
    unittest.main()
