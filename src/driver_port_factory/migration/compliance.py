from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.material import parse_materials
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..knowledge.contracts import KnowledgeArtifact
from ..knowledge.corpus import CorpusManifest
from ..knowledge.index import KnowledgeIndex
from ..source_analysis.contracts import SourceAnalysisArtifact
from ..target_study.contracts import TargetStudyArtifact
from ..target_study.evidence import TargetEvidenceVerifier
from .contracts import (
    ComplianceArea,
    ComplianceRepairTarget,
    ComplianceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
)

COMPLIANCE_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.IMPLEMENTATION_BUNDLE,
    MigrationArtifact.TRANSLATION_COVERAGE,
    MigrationArtifact.TARGET_CHANGE_INVENTORY,
    KnowledgeArtifact.QUERY_CONTRACT,
    KnowledgeArtifact.GENERATED_SKILL,
    TargetStudyArtifact.STRUCTURED_PROFILE,
    TargetStudyArtifact.API_EVIDENCE,
    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
    TargetStudyArtifact.CHANGE_PLAN,
    SourceAnalysisArtifact.MATERIALS_MANIFEST,
)


def _string_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise WorkflowError(f"{label} must be a string list")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class ComplianceReview:
    identifier: str
    status: ComplianceStatus
    repair_target: ComplianceRepairTarget
    summary: str
    implementation_paths: tuple[str, ...]
    target_evidence: tuple[dict[str, Any], ...]
    unsafe_obligation_ids: tuple[str, ...]
    details: dict[str, Any]

    @classmethod
    def from_dict(cls, value: Any, key: str) -> ComplianceReview:
        if not isinstance(value, dict):
            raise WorkflowError("compliance review must be an object")
        try:
            evidence = value["target_evidence"]
            details = value["details"]
            if not isinstance(evidence, list) or not all(
                isinstance(item, dict) for item in evidence
            ):
                raise TypeError
            if not isinstance(details, dict):
                raise TypeError
            return cls(
                str(value[key]),
                ComplianceStatus(value["status"]),
                ComplianceRepairTarget(value["repair_target"]),
                str(value["summary"]),
                _string_list(value["implementation_paths"], "implementation_paths"),
                tuple(evidence),
                _string_list(value["unsafe_obligation_ids"], "unsafe_obligation_ids"),
                details,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("compliance review has an invalid typed boundary") from error

    def to_dict(self, key: str, identifier: str | None = None) -> dict[str, Any]:
        return {
            key: identifier or self.identifier,
            "status": self.status.value,
            "repair_target": self.repair_target.value,
            "summary": self.summary,
            "implementation_paths": list(self.implementation_paths),
            "target_evidence": list(self.target_evidence),
            "unsafe_obligation_ids": list(self.unsafe_obligation_ids),
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    status: ComplianceStatus
    areas: tuple[tuple[ComplianceArea, ComplianceReview], ...]
    apis: tuple[ComplianceReview, ...]
    target_changes: tuple[ComplianceReview, ...]
    compile_status: ContractExecutionStatus
    runtime_status: ContractExecutionStatus

    @classmethod
    def read(cls, path: Path) -> ComplianceReport:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex compliance response is not UTF-8 JSON") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> ComplianceReport:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("compliance report must be a schema_version=1 object")
        try:
            area_values = value["areas"]
            api_values = value["apis"]
            change_values = value["target_changes"]
            execution = value["execution"]
            if not all(
                isinstance(items, list) for items in (area_values, api_values, change_values)
            ):
                raise TypeError
            if not isinstance(execution, dict):
                raise TypeError
            areas = tuple(
                (ComplianceArea(item["area"]), ComplianceReview.from_dict(item, "area"))
                for item in area_values
            )
            return cls(
                ComplianceStatus(value["status"]),
                areas,
                tuple(ComplianceReview.from_dict(item, "api_id") for item in api_values),
                tuple(ComplianceReview.from_dict(item, "change_id") for item in change_values),
                ContractExecutionStatus(execution["compile"]),
                ContractExecutionStatus(execution["runtime"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("compliance report has an invalid typed boundary") from error

    def to_dict(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "inputs": inputs,
            "status": self.status.value,
            "areas": [review.to_dict("area", area.value) for area, review in self.areas],
            "apis": [review.to_dict("api_id") for review in self.apis],
            "target_changes": [review.to_dict("change_id") for review in self.target_changes],
            "execution": {
                "compile": self.compile_status.value,
                "runtime": self.runtime_status.value,
            },
        }


class ComplianceService:
    def finalize(self, project: Project, report: ComplianceReport) -> None:
        if project.stage(MigrationStage.TARGET_COMPLIANCE).status is not StageStatus.RUNNING:
            raise WorkflowError("target_compliance must be RUNNING")
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in COMPLIANCE_INPUTS}
        data = (
            json.dumps(report.to_dict(inputs), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        project.finalize_stage(
            MigrationStage.TARGET_COMPLIANCE,
            (GeneratedArtifact(MigrationArtifact.COMPLIANCE_REPORT, data, "generated:compliance"),),
        )

    @staticmethod
    def _input(project: Project, kind: object):
        dependencies = project.workflow.spec(MigrationStage.TARGET_COMPLIANCE).dependencies
        matches = [
            reference
            for stage in dependencies
            for reference in project.artifact_refs(stage=stage)
            if reference.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"target compliance requires one {kind.value} input")
        return matches[0]


class ComplianceGate:
    def __init__(self, context: BundleValidationContext) -> None:
        self.context = context
        _, data = context.one_current(MigrationArtifact.COMPLIANCE_REPORT)
        self.document = json_object(data, MigrationArtifact.COMPLIANCE_REPORT.value)
        self.report = ComplianceReport.from_dict(self.document)

    def validate(self) -> None:
        expected_inputs = {
            kind.value: self.context.one_dependency(kind)[0].to_dict() for kind in COMPLIANCE_INPUTS
        }
        if self.document.get("inputs") != expected_inputs:
            raise WorkflowError("compliance report does not bind frozen implementation evidence")
        if self.report.status is not ComplianceStatus.PASS:
            raise WorkflowError("target compliance has unresolved findings")
        if self.report.compile_status is not ContractExecutionStatus.NOT_RUN or (
            self.report.runtime_status is not ContractExecutionStatus.NOT_RUN
        ):
            raise WorkflowError("target compliance cannot claim compile or runtime execution")

        implementation = self._document(MigrationArtifact.IMPLEMENTATION_BUNDLE)
        inventory = self._document(MigrationArtifact.TARGET_CHANGE_INVENTORY)
        paths = {str(item["path"]) for item in implementation.get("files", [])}
        evidence = self._evidence_verifier()
        reviews = [review for _area, review in self.report.areas]
        self._complete_reviews(reviews, paths, evidence)
        if {area for area, _review in self.report.areas} != set(ComplianceArea):
            raise WorkflowError("compliance report does not cover every target review area")
        if set().union(*(set(review.implementation_paths) for review in reviews)) != paths:
            raise WorkflowError("compliance report does not cover every implementation file")

        symbols = {str(item["api_id"]): item for item in inventory.get("target_symbols", [])}
        self._complete_reviews(self.report.apis, paths, evidence)
        if {review.identifier for review in self.report.apis} != set(symbols):
            raise WorkflowError("compliance report does not cover every target API")
        self._api_originals(symbols)

        changes = {str(item["change_id"]): item for item in inventory.get("target_changes", [])}
        self._complete_reviews(self.report.target_changes, paths, evidence)
        if {review.identifier for review in self.report.target_changes} != set(changes):
            raise WorkflowError("compliance report does not cover every pre-existing target change")
        for review in self.report.target_changes:
            required = ("path", "necessity", "safety", "rollback")
            if review.details.get("path") != changes[review.identifier].get("path") or not all(
                str(review.details.get(field, "")).strip() for field in required[1:]
            ):
                raise WorkflowError("target change compliance review is incomplete")

        obligations = {
            str(item["obligation_id"]) for item in inventory.get("unsafe_obligations", [])
        }
        covered = {identifier for review in reviews for identifier in review.unsafe_obligation_ids}
        if covered != obligations:
            raise WorkflowError("compliance report does not cover every unsafe obligation")

    def _complete_reviews(
        self,
        reviews: tuple[ComplianceReview, ...] | list[ComplianceReview],
        paths: set[str],
        evidence: TargetEvidenceVerifier,
    ) -> None:
        identifiers = [review.identifier for review in reviews]
        if len(identifiers) != len(set(identifiers)):
            raise WorkflowError("compliance review identifiers must be unique")
        for review in reviews:
            if (
                review.status is not ComplianceStatus.PASS
                or review.repair_target is not ComplianceRepairTarget.NONE
                or not review.summary.strip()
                or not review.target_evidence
                or not set(review.implementation_paths) <= paths
            ):
                raise WorkflowError("compliance review is unresolved or incomplete")
            for reference in review.target_evidence:
                evidence.verify(reference)

    def _api_originals(self, symbols: dict[str, dict[str, Any]]) -> None:
        table = self._document(TargetStudyArtifact.API_EVIDENCE)
        entries = {str(item["api_id"]): item for item in table.get("entries", [])}
        for review in self.report.apis:
            citations = {
                (str(item.get("chunk_id")), str(item.get("record_id")))
                for item in review.target_evidence
            }
            entry = entries[review.identifier]
            originals = {
                (str(entry[field].get("chunk_id")), str(entry[field].get("record_id")))
                for field in ("definition_evidence", "call_site_evidence")
            }
            if review.identifier not in symbols or not originals <= citations:
                raise WorkflowError("target API review omits its definition or call-site original")

    def _evidence_verifier(self) -> TargetEvidenceVerifier:
        ref, data = self.context.one_dependency(SourceAnalysisArtifact.MATERIALS_MANIFEST)
        knowledge = KnowledgeIndex(
            self.context.project_root,
            CorpusManifest(parse_materials(data), data, ref.digest, ref.source),
        )
        knowledge.status()
        return TargetEvidenceVerifier(knowledge)

    def _document(self, kind: object) -> dict[str, Any]:
        return json_object(self.context.one_dependency(kind)[1], kind.value)


def validate_compliance_bundle(context: BundleValidationContext) -> None:
    ComplianceGate(context).validate()
