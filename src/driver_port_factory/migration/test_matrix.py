from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ..acquisition.material import parse_materials
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeDomain
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex, file_sha256
from ..source_analysis.contracts import SourceAnalysisArtifact
from ..target_study.contracts import TargetStudyArtifact
from .contracts import (
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
    TestDisposition,
    TestOrigin,
    TestPrimaryClass,
)

MATRIX_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.CONTRACTS,
    KnowledgeArtifact.QUERY_CONTRACT,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    SourceAnalysisArtifact.SOURCE_CLOSURE,
    SourceAnalysisArtifact.MATERIALS_MANIFEST,
)
CLASS_DISPOSITIONS = MappingProxyType(
    {
        TestPrimaryClass.DEVICE_FUNCTIONAL: frozenset({TestDisposition.RETAIN}),
        TestPrimaryClass.DEVICE_PROTOCOL_INTERNAL: frozenset(
            {TestDisposition.RETAIN, TestDisposition.ADAPT}
        ),
        TestPrimaryClass.PORTABLE_INTENT_PLATFORM_HARNESS: frozenset({TestDisposition.ADAPT}),
        TestPrimaryClass.SOURCE_PLATFORM_SEMANTICS: frozenset({TestDisposition.EXCLUDE}),
        TestPrimaryClass.OUT_OF_SCOPE_DEVICE_VARIANT: frozenset({TestDisposition.EXCLUDE}),
        TestPrimaryClass.TARGET_CAPABILITY_BLOCKED: frozenset({TestDisposition.PRESERVE_BLOCKED}),
        TestPrimaryClass.QEMU_MODEL_BLOCKED: frozenset({TestDisposition.PRESERVE_BLOCKED}),
    }
)
BLOCKER_DOMAINS = MappingProxyType(
    {
        TestPrimaryClass.TARGET_CAPABILITY_BLOCKED: KnowledgeDomain.TARGET,
        TestPrimaryClass.QEMU_MODEL_BLOCKED: KnowledgeDomain.QEMU,
    }
)


@dataclass(frozen=True, slots=True)
class TestMapping:
    identifier: str
    origin: TestOrigin
    source_test: str | None
    primary_class: TestPrimaryClass
    disposition: TestDisposition
    rationale: str
    evidence: tuple[dict[str, Any], ...]
    contract_ids: tuple[str, ...]
    original_command: str
    setup: str
    stimulus: str
    oracle: str
    boundary_cases: tuple[str, ...]
    negative_paths: tuple[str, ...]
    cleanup: str
    source_only_assertions_removed: tuple[str, ...]
    adapter: dict[str, Any]
    limitations: tuple[str, ...]
    expected_result: str
    execution_status: ContractExecutionStatus
    public_developer_evidence: bool

    @classmethod
    def from_dict(cls, value: Any) -> TestMapping:
        if not isinstance(value, dict):
            raise WorkflowError("test mapping must be an object")
        try:
            evidence = value["evidence"]
            contract_ids = value["contract_ids"]
            removed = value["source_only_assertions_removed"]
            limitations = value["limitations"]
            boundaries = value["boundary_cases"]
            negative_paths = value["negative_paths"]
            adapter = value["adapter"]
            collections = (
                evidence,
                contract_ids,
                removed,
                limitations,
                boundaries,
                negative_paths,
            )
            if not all(isinstance(item, list) for item in collections):
                raise TypeError
            if not isinstance(adapter, dict):
                raise TypeError
            return cls(
                str(value["test_id"]),
                TestOrigin(value["origin"]),
                value["source_test"],
                TestPrimaryClass(value["primary_class"]),
                TestDisposition(value["disposition"]),
                str(value["rationale"]),
                tuple(evidence),
                tuple(str(identifier) for identifier in contract_ids),
                str(value["original_command"]),
                str(value["setup"]),
                str(value["stimulus"]),
                str(value["oracle"]),
                tuple(str(item) for item in boundaries),
                tuple(str(item) for item in negative_paths),
                str(value["cleanup"]),
                tuple(str(item) for item in removed),
                adapter,
                tuple(str(item) for item in limitations),
                str(value["expected_result"]),
                ContractExecutionStatus(value["execution_status"]),
                value["public_developer_evidence"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("test mapping has an invalid typed boundary") from error

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.identifier,
            "origin": self.origin.value,
            "source_test": self.source_test,
            "primary_class": self.primary_class.value,
            "disposition": self.disposition.value,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
            "contract_ids": list(self.contract_ids),
            "original_command": self.original_command,
            "setup": self.setup,
            "stimulus": self.stimulus,
            "oracle": self.oracle,
            "boundary_cases": list(self.boundary_cases),
            "negative_paths": list(self.negative_paths),
            "cleanup": self.cleanup,
            "source_only_assertions_removed": list(self.source_only_assertions_removed),
            "adapter": self.adapter,
            "limitations": list(self.limitations),
            "expected_result": self.expected_result,
            "execution_status": self.execution_status.value,
            "public_developer_evidence": self.public_developer_evidence,
        }


@dataclass(frozen=True, slots=True)
class TestSelectionMatrix:
    tests: tuple[TestMapping, ...]

    @classmethod
    def read(cls, path: Path) -> TestSelectionMatrix:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex test-adaptation response is not UTF-8 JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("test-adaptation response must be a schema_version=1 object")
        tests = value.get("tests")
        if not isinstance(tests, list):
            raise WorkflowError("test-adaptation response requires a tests list")
        return cls(tuple(TestMapping.from_dict(item) for item in tests))

    def to_dict(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "inputs": inputs,
            "tests": [test.to_dict() for test in self.tests],
        }


class TestSelectionService:
    def finalize(self, project: Project, matrix: TestSelectionMatrix) -> None:
        if project.stage(MigrationStage.TEST_ADAPTATION).status is not StageStatus.RUNNING:
            raise WorkflowError("test_adaptation must be RUNNING")
        dependencies = project.workflow.spec(MigrationStage.TEST_ADAPTATION).dependencies
        inputs = {}
        for kind in MATRIX_INPUTS:
            matches = [
                ref
                for stage in dependencies
                for ref in project.current_artifact_refs(stage=stage)
                if ref.kind == kind.value
            ]
            if len(matches) != 1:
                raise WorkflowError(f"test adaptation requires one {kind.value} input")
            inputs[kind.value] = matches[0].to_dict()
        data = (
            json.dumps(
                matrix.to_dict(inputs),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n"
        ).encode()
        project.finalize_stage(
            MigrationStage.TEST_ADAPTATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.TEST_PORT_MATRIX,
                    data,
                    "generated:test-selection-matrix",
                ),
            ),
        )


class TestSelectionGate:
    def __init__(self, context: BundleValidationContext) -> None:
        self.context = context
        _, data = context.one_current(MigrationArtifact.TEST_PORT_MATRIX)
        self.document = json_object(data, MigrationArtifact.TEST_PORT_MATRIX.value)
        self.tests = tuple(TestMapping.from_dict(item) for item in self.document.get("tests", []))
        self.knowledge = self._knowledge()

    def validate(self) -> None:
        self._bind_inputs()
        contract_ids = self._contract_ids()
        discovered = self._discovered_tests()
        identifiers: set[str] = set()
        occurrences: Counter[str] = Counter()
        for test in self.tests:
            self._validate_test(test, identifiers, occurrences, contract_ids)
        if set(occurrences) != discovered:
            raise WorkflowError("test matrix must classify every discovered source test")

    def _bind_inputs(self) -> None:
        expected = {
            kind.value: self.context.one_dependency(kind)[0].to_dict() for kind in MATRIX_INPUTS
        }
        if self.document.get("inputs") != expected:
            raise WorkflowError("test matrix does not bind the frozen input artifacts")

    def _knowledge(self) -> KnowledgeIndex:
        ref, data = self.context.one_dependency(SourceAnalysisArtifact.MATERIALS_MANIFEST)
        corpus = CorpusManifest(parse_materials(data), data, ref.digest, ref.source)
        knowledge = KnowledgeIndex(self.context.project_root, corpus)
        knowledge.status()
        return knowledge

    def _contract_ids(self) -> set[str]:
        _, data = self.context.one_dependency(MigrationArtifact.CONTRACTS)
        document = json_object(data, MigrationArtifact.CONTRACTS.value)
        return {
            str(contract.get("id"))
            for contract in document.get("contracts", [])
            if isinstance(contract, dict)
        }

    def _discovered_tests(self) -> set[str]:
        _, data = self.context.one_dependency(SourceAnalysisArtifact.SOURCE_CLOSURE)
        closure = json_object(data, SourceAnalysisArtifact.SOURCE_CLOSURE.value)
        category = closure.get("closure_categories", {}).get("source_tests", {})
        paths = category.get("paths")
        if not isinstance(paths, list):
            raise WorkflowError("source closure has an invalid source-test inventory")
        return {str(path) for path in paths}

    def _validate_test(
        self,
        test: TestMapping,
        identifiers: set[str],
        occurrences: Counter[str],
        contract_ids: set[str],
    ) -> None:
        if not test.identifier.strip() or test.identifier in identifiers:
            raise WorkflowError("test mapping IDs must be stable and unique")
        identifiers.add(test.identifier)
        if test.disposition not in CLASS_DISPOSITIONS[test.primary_class]:
            raise WorkflowError(f"test {test.identifier} disposition contradicts its class")
        required_text = (
            test.rationale,
            test.setup,
            test.stimulus,
            test.oracle,
            test.cleanup,
            test.expected_result,
        )
        if not all(value.strip() for value in required_text):
            raise WorkflowError(f"test {test.identifier} omits preserved test intent")
        if test.origin is TestOrigin.SOURCE_TEST and not test.original_command.strip():
            raise WorkflowError(f"source test {test.identifier} omits its original command")
        if not test.evidence:
            raise WorkflowError(f"test {test.identifier} has no classification evidence")
        evidence_domains = {self._verify_reference(reference) for reference in test.evidence}
        if test.origin is TestOrigin.SOURCE_TEST and KnowledgeDomain.SOURCE not in evidence_domains:
            raise WorkflowError(f"test {test.identifier} has no source-original evidence")
        blocked_domain = BLOCKER_DOMAINS.get(test.primary_class)
        if blocked_domain is not None and blocked_domain not in evidence_domains:
            raise WorkflowError(f"blocked test {test.identifier} lacks blocker evidence")
        self._origin(test, occurrences)
        self._contracts(test, contract_ids)
        self._adapter(test)
        self._status(test)

    def _origin(self, test: TestMapping, occurrences: Counter[str]) -> None:
        if test.public_developer_evidence is not True:
            raise WorkflowError(f"test {test.identifier} must remain public developer evidence")
        if test.origin is TestOrigin.SOURCE_TEST:
            if not isinstance(test.source_test, str) or not test.source_test:
                raise WorkflowError(f"source test {test.identifier} has no frozen path")
            occurrences[test.source_test] += 1
        elif test.source_test is not None:
            raise WorkflowError("NEW_MIGRATION_TEST must not claim source-test provenance")

    @staticmethod
    def _contracts(test: TestMapping, contract_ids: set[str]) -> None:
        if any(identifier not in contract_ids for identifier in test.contract_ids):
            raise WorkflowError(f"test {test.identifier} references an unknown contract")
        if test.disposition is not TestDisposition.EXCLUDE and not test.contract_ids:
            raise WorkflowError(f"test {test.identifier} has no driver contract mapping")

    def _adapter(self, test: TestMapping) -> None:
        required = test.adapter.get("required")
        description = test.adapter.get("description")
        references = test.adapter.get("target_evidence")
        if (
            not isinstance(required, bool)
            or not isinstance(description, str)
            or not description.strip()
        ):
            raise WorkflowError(f"test {test.identifier} has an invalid adapter record")
        if not isinstance(references, list):
            raise WorkflowError(f"test {test.identifier} has invalid adapter evidence")
        if test.primary_class is TestPrimaryClass.PORTABLE_INTENT_PLATFORM_HARNESS and (
            required is not True or not references
        ):
            raise WorkflowError(f"portable test {test.identifier} requires a thin adapter")
        for reference in references:
            if self._verify_reference(reference) is not KnowledgeDomain.TARGET:
                raise WorkflowError(f"test {test.identifier} adapter lacks target evidence")

    @staticmethod
    def _status(test: TestMapping) -> None:
        blocked = test.disposition is TestDisposition.PRESERVE_BLOCKED
        excluded = test.disposition is TestDisposition.EXCLUDE
        expected = (
            ContractExecutionStatus.BLOCKED
            if blocked
            else ContractExecutionStatus.NOT_APPLICABLE
            if excluded
            else ContractExecutionStatus.NOT_RUN
        )
        if test.execution_status is not expected:
            raise WorkflowError(f"test {test.identifier} has an optimistic execution status")
        if blocked and not test.limitations:
            raise WorkflowError(f"blocked test {test.identifier} has no limitation")

    def _verify_reference(self, reference: Any) -> KnowledgeDomain:
        if not isinstance(reference, dict) or not reference.get("chunk_id"):
            raise WorkflowError("test evidence requires a knowledge chunk locator")
        try:
            domain = KnowledgeDomain(reference["domain"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("test evidence has an invalid domain") from error
        exact = self.knowledge.show(str(reference["chunk_id"]))["result"]
        if exact["domain"] != domain.value or reference.get("record_id") != exact["record_id"]:
            raise WorkflowError("test evidence does not belong to its declared domain")
        original = self.knowledge.controlled_path(str(exact["path"]))
        if file_sha256(original) != exact["sha256"]:
            raise WorkflowError(f"test evidence original changed: {exact['path']}")
        return domain


def validate_test_matrix_bundle(context: BundleValidationContext) -> None:
    TestSelectionGate(context).validate()
