from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.material import parse_materials
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeDomain
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex, file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact
from ..target_study.contracts import ApiConfidence, TargetStudyArtifact
from .contracts import (
    ContractEvidenceStatus,
    ContractExecutionStatus,
    ContractVerificationKind,
    EvidenceSuccessor,
    MigrationArtifact,
    MigrationStage,
)

CONTRACT_INPUTS = (
    MigrationArtifact.HANDOFF,
    KnowledgeArtifact.QUERY_CONTRACT,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
    TargetStudyArtifact.CHANGE_PLAN,
    SourceAnalysisArtifact.SOURCE_CLOSURE,
    SourceAnalysisArtifact.MATERIALS_MANIFEST,
    SourceAnalysisArtifact.STRUCTURED_C_FACTS,
)
EVIDENCE_DOMAINS = (
    KnowledgeDomain.HARDWARE,
    KnowledgeDomain.SOURCE,
    KnowledgeDomain.TARGET,
    KnowledgeDomain.QEMU,
)


@dataclass(frozen=True, slots=True)
class MigrationContract:
    identifier: str
    requirement: str
    evidence: dict[str, Any]
    source_function_refs: tuple[dict[str, Any], ...]
    rust_design_intent: str
    verification: dict[str, Any]
    evidence_status: ContractEvidenceStatus
    execution_status: ContractExecutionStatus

    @classmethod
    def from_dict(cls, value: Any) -> MigrationContract:
        if not isinstance(value, dict):
            raise WorkflowError("migration contract must be an object")
        try:
            evidence = value["evidence"]
            source_refs = value["source_function_refs"]
            verification = value["verification"]
            if not isinstance(evidence, dict) or not isinstance(source_refs, list):
                raise TypeError
            if not isinstance(verification, dict):
                raise TypeError
            return cls(
                str(value["id"]),
                str(value["requirement"]),
                evidence,
                tuple(source_refs),
                str(value["rust_design_intent"]),
                verification,
                ContractEvidenceStatus(value["evidence_status"]),
                ContractExecutionStatus(value["execution_status"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("migration contract has an invalid typed boundary") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.identifier,
            "requirement": self.requirement,
            "evidence": self.evidence,
            "source_function_refs": list(self.source_function_refs),
            "rust_design_intent": self.rust_design_intent,
            "verification": self.verification,
            "evidence_status": self.evidence_status.value,
            "execution_status": self.execution_status.value,
        }


@dataclass(frozen=True, slots=True)
class MigrationContractSet:
    contracts: tuple[MigrationContract, ...]
    gaps: tuple[dict[str, Any], ...]

    @classmethod
    def read(cls, path: Path) -> MigrationContractSet:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex migration contracts response is not UTF-8 JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("migration contracts response must be a schema_version=1 object")
        contracts = value.get("contracts")
        gaps = value.get("gaps")
        if not isinstance(contracts, list) or not contracts or not isinstance(gaps, list):
            raise WorkflowError("migration contracts response requires contracts and gaps")
        for gap in gaps:
            if not isinstance(gap, dict):
                raise WorkflowError("migration contract gap must be an object")
            try:
                EvidenceSuccessor(gap["successor"])
            except (KeyError, TypeError, ValueError) as error:
                raise WorkflowError("migration contract gap has no valid successor") from error
        return cls(tuple(MigrationContract.from_dict(item) for item in contracts), tuple(gaps))

    def to_dict(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "inputs": inputs,
            "contracts": [contract.to_dict() for contract in self.contracts],
            "gaps": list(self.gaps),
        }


class MigrationContractService:
    def finalize(self, project: Project, contract_set: MigrationContractSet) -> None:
        if project.stage(MigrationStage.CONTRACTS).status is not StageStatus.RUNNING:
            raise WorkflowError("migration_contracts must be RUNNING")
        inputs = {kind.value: self._dependency(project, kind) for kind in CONTRACT_INPUTS}
        data = (
            json.dumps(
                contract_set.to_dict(inputs),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        project.finalize_stage(
            MigrationStage.CONTRACTS,
            (
                GeneratedArtifact(
                    MigrationArtifact.CONTRACTS,
                    data,
                    "generated:migration-contracts",
                ),
            ),
        )

    @staticmethod
    def _dependency(project: Project, kind: Any) -> dict[str, Any]:
        dependencies = project.workflow.spec(MigrationStage.CONTRACTS).dependencies
        matches = [
            ref
            for stage in dependencies
            for ref in project.artifact_refs(stage=stage)
            if ref.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"migration contracts require one {kind.value} input")
        return matches[0].to_dict()


class MigrationContractGate:
    def __init__(self, context: BundleValidationContext) -> None:
        self.context = context
        _, data = context.one_current(MigrationArtifact.CONTRACTS)
        self.document = json_object(data, MigrationArtifact.CONTRACTS.value)
        self.contracts = tuple(
            MigrationContract.from_dict(value) for value in self.document.get("contracts", [])
        )

    def validate(self) -> None:
        self._bind_inputs()
        if not self.contracts:
            raise WorkflowError("migration contract set is empty")
        gaps = self.document.get("gaps")
        if gaps:
            successors = sorted({str(gap.get("successor")) for gap in gaps})
            raise WorkflowError(
                "migration contract evidence gaps require successor: " + ", ".join(successors)
            )
        knowledge = self._knowledge()
        api_entries = self._api_entries()
        identifiers: set[str] = set()
        covered_functions: set[tuple[str, str]] = set()
        for contract in self.contracts:
            self._validate_contract(
                contract,
                identifiers,
                covered_functions,
                knowledge,
                api_entries,
            )
        required_functions = self._required_functions()
        if covered_functions != required_functions:
            missing = sorted(required_functions - covered_functions)
            unknown = sorted(covered_functions - required_functions)
            raise WorkflowError(
                f"migration contracts do not exactly cover source functions; "
                f"missing={missing}, unknown={unknown}"
            )

    def _bind_inputs(self) -> None:
        expected = {
            kind.value: self.context.one_dependency(kind)[0].to_dict() for kind in CONTRACT_INPUTS
        }
        if self.document.get("inputs") != expected:
            raise WorkflowError("migration contracts do not bind the frozen input artifacts")

    def _knowledge(self) -> KnowledgeIndex:
        ref, data = self.context.one_dependency(SourceAnalysisArtifact.MATERIALS_MANIFEST)
        corpus = CorpusManifest(parse_materials(data), data, ref.digest, ref.source)
        knowledge = KnowledgeIndex(self.context.project_root, corpus)
        knowledge.status()
        return knowledge

    def _api_entries(self) -> dict[str, dict[str, Any]]:
        _, data = self.context.one_dependency(TargetStudyArtifact.API_EVIDENCE)
        table = json_object(data, TargetStudyArtifact.API_EVIDENCE.value)
        entries = table.get("entries")
        if not isinstance(entries, list):
            raise WorkflowError("target API evidence has no entries")
        return {str(entry.get("api_id")): entry for entry in entries if isinstance(entry, dict)}

    def _validate_contract(
        self,
        contract: MigrationContract,
        identifiers: set[str],
        covered_functions: set[tuple[str, str]],
        knowledge: KnowledgeIndex,
        api_entries: dict[str, dict[str, Any]],
    ) -> None:
        if not contract.identifier.strip() or contract.identifier in identifiers:
            raise WorkflowError("migration contract IDs must be stable and unique")
        identifiers.add(contract.identifier)
        if not contract.requirement.strip() or not contract.rust_design_intent.strip():
            raise WorkflowError(f"migration contract {contract.identifier} is incomplete")
        self._validate_evidence(contract, knowledge, api_entries)
        if contract.evidence_status is ContractEvidenceStatus.UNKNOWN:
            raise WorkflowError(
                f"migration contract {contract.identifier} has UNKNOWN evidence without successor"
            )
        if contract.execution_status is not ContractExecutionStatus.NOT_RUN:
            raise WorkflowError(
                f"migration contract {contract.identifier} execution must remain NOT_RUN"
            )
        self._verification(contract)
        for reference in contract.source_function_refs:
            if not isinstance(reference, dict):
                raise WorkflowError(
                    f"migration contract {contract.identifier} has invalid source ref"
                )
            covered_functions.add((str(reference.get("unit_id")), str(reference.get("node_id"))))

    def _validate_evidence(
        self,
        contract: MigrationContract,
        knowledge: KnowledgeIndex,
        api_entries: dict[str, dict[str, Any]],
    ) -> None:
        if set(contract.evidence) != {domain.value for domain in EVIDENCE_DOMAINS}:
            raise WorkflowError(f"migration contract {contract.identifier} lacks an evidence lane")
        for domain in EVIDENCE_DOMAINS:
            references = contract.evidence[domain.value]
            if not isinstance(references, list) or not references:
                raise WorkflowError(
                    f"migration contract {contract.identifier} has no {domain.value} evidence"
                )
            if domain is KnowledgeDomain.TARGET:
                for reference in references:
                    self._validate_target(reference, knowledge, api_entries)
            else:
                for reference in references:
                    self._verify_reference(reference, domain, knowledge)

    def _validate_target(
        self,
        reference: Any,
        knowledge: KnowledgeIndex,
        api_entries: dict[str, dict[str, Any]],
    ) -> None:
        if not isinstance(reference, dict):
            raise WorkflowError("target contract evidence must be an object")
        entry = api_entries.get(str(reference.get("api_id")))
        if entry is None or ApiConfidence(entry.get("confidence")) is ApiConfidence.UNKNOWN:
            raise WorkflowError("target contract evidence does not bind a resolved API entry")
        for locator, field in (
            ("definition", "definition_evidence"),
            ("call_site", "call_site_evidence"),
        ):
            verified = self._verify_reference(
                reference.get(locator),
                KnowledgeDomain.TARGET,
                knowledge,
            )
            expected = entry.get(field)
            if not isinstance(expected, dict) or any(
                verified[name] != expected.get(name) for name in ("chunk_id", "record_id")
            ):
                raise WorkflowError(
                    "target contract evidence differs from the original API locator"
                )

    def _verify_reference(
        self,
        reference: Any,
        domain: KnowledgeDomain,
        knowledge: KnowledgeIndex,
    ) -> dict[str, Any]:
        if not isinstance(reference, dict) or not reference.get("chunk_id"):
            raise WorkflowError(f"{domain.value} contract evidence requires a chunk locator")
        exact = knowledge.show(str(reference["chunk_id"]))["result"]
        if exact["domain"] != domain.value or reference.get("record_id") != exact["record_id"]:
            raise WorkflowError(f"contract evidence is not owned by the {domain.value} lane")
        original = knowledge.controlled_path(str(exact["path"]))
        if file_sha256(original) != exact["sha256"]:
            raise WorkflowError(f"contract evidence original changed: {exact['path']}")
        return exact

    @staticmethod
    def _verification(contract: MigrationContract) -> None:
        try:
            ContractVerificationKind(contract.verification["kind"])
            argv = contract.verification["argv"]
            oracle = contract.verification["oracle"]
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError(
                f"migration contract {contract.identifier} has invalid verification"
            ) from error
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(argument, str) and argument for argument in argv)
            or not isinstance(oracle, str)
            or not oracle.strip()
        ):
            raise WorkflowError(
                f"migration contract {contract.identifier} verification is not executable"
            )

    def _required_functions(self) -> set[tuple[str, str]]:
        _, data = self.context.one_dependency(SourceAnalysisArtifact.STRUCTURED_C_FACTS)
        facts = json_object(data, SourceAnalysisArtifact.STRUCTURED_C_FACTS.value)
        required: set[tuple[str, str]] = set()
        for unit in facts.get("units", []):
            unit_id = str(unit["unit_id"])
            semantic = unit["semantic_index"]
            path = (self.context.project_root / semantic["path"]).resolve()
            if (
                self.context.project_root not in path.parents
                or file_sha256(path) != semantic["sha256"]
            ):
                raise WorkflowError(f"structured semantic index changed for {unit_id}")
            index = json.loads(path.read_text(encoding="utf-8"))
            required.update(
                (unit_id, str(node_id)) for node_id in index["indexes"]["function_definitions"]
            )
        return required


def validate_contract_bundle(context: BundleValidationContext) -> None:
    MigrationContractGate(context).validate()
