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
    ComplianceRepairTarget,
    ComplianceStatus,
    ContractExecutionStatus,
    MigrationArtifact,
    MigrationStage,
)

COMPLIANCE_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.IMPLEMENTATION_BUNDLE,
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
class ComplianceFinding:
    repair_target: ComplianceRepairTarget
    summary: str
    implementation_paths: tuple[str, ...]
    target_evidence: tuple[dict[str, Any], ...]

    @classmethod
    def from_dict(cls, value: Any) -> ComplianceFinding:
        if not isinstance(value, dict):
            raise WorkflowError("compliance finding must be an object")
        evidence = value.get("target_evidence")
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise WorkflowError("compliance finding evidence must be an object list")
        try:
            finding = cls(
                ComplianceRepairTarget(value["repair_target"]),
                str(value["summary"]),
                _string_list(value["implementation_paths"], "implementation_paths"),
                tuple(evidence),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("compliance finding has an invalid typed boundary") from error
        if finding.repair_target is ComplianceRepairTarget.NONE or not finding.summary.strip():
            raise WorkflowError("compliance finding must name a repair target and summary")
        return finding

    def to_dict(self) -> dict[str, Any]:
        return {
            "repair_target": self.repair_target.value,
            "summary": self.summary,
            "implementation_paths": list(self.implementation_paths),
            "target_evidence": list(self.target_evidence),
        }


@dataclass(frozen=True, slots=True)
class ComplianceReport:
    status: ComplianceStatus
    evidence: tuple[dict[str, Any], ...]
    findings: tuple[ComplianceFinding, ...]

    @classmethod
    def read(cls, path: Path) -> ComplianceReport:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex compliance response is not UTF-8 JSON") from error
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> ComplianceReport:
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise WorkflowError("compliance report must be a schema_version=2 object")
        evidence = value.get("evidence")
        findings = value.get("findings")
        if not isinstance(evidence, list) or not all(isinstance(item, dict) for item in evidence):
            raise WorkflowError("compliance evidence must be an object list")
        if not isinstance(findings, list):
            raise WorkflowError("compliance findings must be a list")
        try:
            report = cls(
                ComplianceStatus(value["status"]),
                tuple(evidence),
                tuple(ComplianceFinding.from_dict(item) for item in findings),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("compliance report has an invalid typed boundary") from error
        if not report.evidence or (report.status is ComplianceStatus.PASS) != (not report.findings):
            raise WorkflowError("compliance status, evidence, and findings are inconsistent")
        return report

    def requires_repair(self, target: ComplianceRepairTarget) -> bool:
        return any(finding.repair_target is target for finding in self.findings)

    def repair_delta(self, target: ComplianceRepairTarget | None = None) -> dict[str, Any] | None:
        findings = [
            finding
            for finding in self.findings
            if target is None or finding.repair_target is target
        ]
        if not findings:
            return None
        evidence = {
            (str(item.get("chunk_id")), str(item.get("record_id"))): item
            for finding in findings
            for item in finding.target_evidence
        }
        return {
            "required_repairs": [finding.summary for finding in findings],
            "implementation_paths": sorted(
                {path for finding in findings for path in finding.implementation_paths}
            ),
            "target_evidence": list(evidence.values()),
        }

    def to_dict(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "inputs": inputs,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "findings": [finding.to_dict() for finding in self.findings],
            "execution": {
                "compile": ContractExecutionStatus.NOT_RUN.value,
                "runtime": ContractExecutionStatus.NOT_RUN.value,
            },
        }


class ComplianceService:
    def finalize(self, project: Project, report: ComplianceReport) -> None:
        if project.stage(MigrationStage.TARGET_COMPLIANCE).status is not StageStatus.RUNNING:
            raise WorkflowError("target_compliance must be RUNNING")
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in COMPLIANCE_INPUTS}
        data = (
            json.dumps(
                report.to_dict(inputs), ensure_ascii=False, sort_keys=True, indent=2
            ).encode()
            + b"\n"
        )
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
            for reference in project.current_artifact_refs(stage=stage)
            if reference.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"target compliance requires one {kind.value} input")
        return matches[0]


class ComplianceGate:
    def __init__(self, context: BundleValidationContext) -> None:
        _, data = context.one_current(MigrationArtifact.COMPLIANCE_REPORT)
        self.context = context
        self.document = json_object(data, MigrationArtifact.COMPLIANCE_REPORT.value)
        self.report = ComplianceReport.from_dict(self.document)

    def validate(self) -> None:
        expected_inputs = {
            kind.value: self.context.one_dependency(kind)[0].to_dict() for kind in COMPLIANCE_INPUTS
        }
        if self.document.get("inputs") != expected_inputs:
            raise WorkflowError("compliance report does not bind current inputs")
        if self.report.status is not ComplianceStatus.PASS:
            raise WorkflowError("target compliance has unresolved findings")
        if self.document.get("execution") != {
            "compile": ContractExecutionStatus.NOT_RUN.value,
            "runtime": ContractExecutionStatus.NOT_RUN.value,
        }:
            raise WorkflowError("target compliance cannot claim execution")
        verifier = self._evidence_verifier()
        for reference in self.report.evidence:
            verifier.verify(reference)

    def _evidence_verifier(self) -> TargetEvidenceVerifier:
        ref, data = self.context.one_dependency(SourceAnalysisArtifact.MATERIALS_MANIFEST)
        knowledge = KnowledgeIndex(
            self.context.project_root,
            CorpusManifest(parse_materials(data), data, ref.digest, ref.source),
        )
        knowledge.status()
        return TargetEvidenceVerifier(knowledge)


def validate_compliance_bundle(context: BundleValidationContext) -> None:
    ComplianceGate(context).validate()
