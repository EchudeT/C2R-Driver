from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.verification import AcquisitionVerifier
from ..core.models import ActorRole, FileArtifact, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..knowledge.index import KnowledgeIndex, file_sha256
from .change_plan import TargetChangePlanValidator
from .contracts import (
    ApiConfidence,
    InvestigationStatus,
    TargetProfileStatus,
    TargetStudyArtifact,
    TargetStudyOutcome,
    TargetStudyStage,
    TraceStage,
    TraceStatus,
)
from .evidence import TargetEvidenceVerifier, require_fields
from .profile import PROFILE_HEADINGS, TargetProfileValidator

TRACE_ORDER = {stage: position for position, stage in enumerate(TraceStage)}
TRACE_STEPS = tuple(stage.value for stage in TraceStage)


@dataclass(frozen=True, slots=True)
class TargetStudyResult:
    status: TargetStudyOutcome
    stage_status: StageStatus
    report_path: str
    errors: tuple[str, ...]


class TargetStudyService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def validate(
        self,
        project: Project,
        *,
        profile_json: Path,
        api_table: Path,
        analogous_trace: Path,
        change_plan: Path,
    ) -> TargetStudyResult:
        project.ensure_role(*self.ROLES)
        stage = project.stage(TargetStudyStage.STUDY)
        if stage.status is StageStatus.READY:
            project.start(TargetStudyStage.STUDY)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"target_platform_study must be READY or RUNNING, got {stage.status.value}"
            )
        paths = {
            "profile_json": self._controlled(project, profile_json),
            "api_table": self._controlled(project, api_table),
            "analogous_trace": self._controlled(project, analogous_trace),
            "change_plan": self._controlled(project, change_plan),
        }
        errors: list[str] = []
        details: dict[str, Any] = {}
        knowledge = KnowledgeIndex.for_project(project)
        evidence = TargetEvidenceVerifier(knowledge)
        knowledge_status = knowledge.status()
        acquisition_verification = AcquisitionVerifier().verify(project)
        if not acquisition_verification["valid"]:
            raise WorkflowError("one or more frozen baselines failed verification")
        try:
            profile = self._load_json(paths["profile_json"])
            api = self._load_json(paths["api_table"])
            trace = self._load_json(paths["analogous_trace"])
            changes = self._load_json(paths["change_plan"])
            details["knowledge_status"] = knowledge_status
            details["acquisition_verification"] = acquisition_verification
            details["profile"] = TargetProfileValidator(project, evidence).validate(profile)
            details["changes"] = TargetChangePlanValidator(evidence).validate(changes)
            details["api_table"] = self._validate_api_table(
                evidence, api, set(details["changes"]["resolution_ids"])
            )
            details["analogous_trace"] = self._validate_trace(evidence, trace)
            self._ready_gate(details["changes"], details["analogous_trace"])
            paths["profile_markdown"] = self._write_profile_markdown(paths["profile_json"], profile)
        except (WorkflowError, UnicodeDecodeError) as error:
            errors.append(str(error))
        report = {
            "schema_version": 1,
            "status": (TargetStudyOutcome.PASS if not errors else TargetStudyOutcome.FAIL),
            "inputs": {
                name: {"path": str(path), "sha256": file_sha256(path)}
                for name, path in paths.items()
            },
            "details": details,
            "errors": errors,
            "validated_at": utc_now(),
        }
        report_path = self._write_report(project, report)
        if errors:
            project.record_artifact(
                TargetStudyStage.STUDY,
                FileArtifact(TargetStudyArtifact.VALIDATION_ATTEMPT, report_path),
            )
            return TargetStudyResult(
                TargetStudyOutcome.FAIL,
                StageStatus.RUNNING,
                str(report_path),
                tuple(errors),
            )
        project.finalize_stage(
            TargetStudyStage.STUDY,
            (
                FileArtifact(TargetStudyArtifact.PROFILE, paths["profile_markdown"]),
                FileArtifact(TargetStudyArtifact.STRUCTURED_PROFILE, paths["profile_json"]),
                FileArtifact(TargetStudyArtifact.API_EVIDENCE, paths["api_table"]),
                FileArtifact(
                    TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
                    paths["analogous_trace"],
                ),
                FileArtifact(TargetStudyArtifact.CHANGE_PLAN, paths["change_plan"]),
                FileArtifact(TargetStudyArtifact.REPORT, report_path),
            ),
        )
        return TargetStudyResult(TargetStudyOutcome.PASS, StageStatus.PASS, str(report_path), ())

    def _validate_api_table(
        self,
        evidence: TargetEvidenceVerifier,
        table: dict[str, Any],
        resolution_ids: set[str],
    ) -> dict[str, Any]:
        if table.get("schema_version") != 1 or not isinstance(table.get("entries"), list):
            raise WorkflowError("target API table must be schema_version=1 with entries")
        if not table["entries"]:
            raise WorkflowError("target API table must contain at least one target-facing API")
        identifiers: set[str] = set()
        verified_entries = []
        for entry in table["entries"]:
            verified_entries.append(
                self._validate_api_entry(evidence, entry, identifiers, resolution_ids)
            )
        return {"entry_count": len(verified_entries), "entries": verified_entries}

    @staticmethod
    def _validate_api_entry(
        evidence: TargetEvidenceVerifier,
        value: Any,
        identifiers: set[str],
        resolution_ids: set[str],
    ) -> dict[str, Any]:
        required = {
            "api_id",
            "api_or_type",
            "purpose_in_driver",
            "documented_contract",
            "execution_context",
            "ownership_lifetime_cleanup",
            "error_behavior",
            "safety_or_unsafe_obligations",
            "confidence",
        }
        entry = require_fields(value, required, "target API entry")
        identifier = str(entry["api_id"])
        if identifier in identifiers:
            raise WorkflowError(f"duplicate target API ID: {identifier}")
        identifiers.add(identifier)
        for field in required - {"confidence"}:
            if not str(entry[field]).strip():
                raise WorkflowError(f"target API {identifier} has empty {field}")
        try:
            confidence = ApiConfidence(entry["confidence"])
        except (TypeError, ValueError) as error:
            raise WorkflowError(f"target API {identifier} has invalid confidence") from error
        verified = {}
        if confidence in {ApiConfidence.VERIFIED, ApiConfidence.INFERRED}:
            for field in (
                "definition_evidence",
                "call_site_evidence",
                "analogous_driver_evidence",
            ):
                reference = entry.get(field)
                if not isinstance(reference, dict):
                    raise WorkflowError(
                        f"target API {identifier} requires {field} at confidence {confidence}"
                    )
                verified[field] = evidence.verify(reference)
        else:
            resolution_id = entry.get("investigation_or_target_change_id")
            if not resolution_id or resolution_id not in resolution_ids:
                raise WorkflowError(
                    f"UNKNOWN target API {identifier} needs a concrete investigation/change"
                )
        return {"api_id": identifier, "confidence": confidence, "evidence": verified}

    @staticmethod
    def _validate_trace(evidence: TargetEvidenceVerifier, trace: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version",
            "selected_implementation",
            "selection_rationale",
            "framework_owners",
            "differences_not_to_copy",
            "steps",
        }
        require_fields(trace, required, "analogous driver trace")
        if trace["schema_version"] != 1:
            raise WorkflowError("analogous trace schema_version must be 1")
        for field in (
            "selected_implementation",
            "selection_rationale",
            "framework_owners",
            "differences_not_to_copy",
        ):
            if not trace[field]:
                raise WorkflowError(f"analogous trace requires {field}")
        steps = trace["steps"]
        if not isinstance(steps, list):
            raise WorkflowError("analogous trace steps must be a list")
        try:
            observed = tuple(TraceStage(step.get("stage")) for step in steps)
        except (TypeError, ValueError) as error:
            raise WorkflowError("analogous trace contains an invalid stage") from error
        if not observed or len(observed) != len(set(observed)):
            raise WorkflowError("analogous trace must contain distinct applicable steps")
        if tuple(sorted(observed, key=TRACE_ORDER.__getitem__)) != observed:
            raise WorkflowError("analogous trace steps must follow the Skill path order")
        return {
            "selected_implementation": trace["selected_implementation"],
            "steps": [TargetStudyService._validate_trace_step(evidence, step) for step in steps],
        }

    @staticmethod
    def _validate_trace_step(
        evidence: TargetEvidenceVerifier, step: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            stage = TraceStage(step.get("stage"))
            status = TraceStatus(step.get("status"))
        except (TypeError, ValueError) as error:
            raise WorkflowError(
                f"analogous trace step {step['stage']} has invalid status"
            ) from error
        if not str(step.get("summary", "")).strip():
            raise WorkflowError(f"analogous trace step {step['stage']} needs a summary")
        references = step.get("evidence_refs")
        if not isinstance(references, list):
            raise WorkflowError(f"analogous trace step {step['stage']} has invalid evidence")
        if status is TraceStatus.VERIFIED and not references:
            raise WorkflowError(f"analogous trace step {step['stage']} needs target evidence")
        return {
            "stage": stage,
            "status": status,
            "evidence": [evidence.verify(reference) for reference in references],
        }

    @staticmethod
    def _ready_gate(changes: dict[str, Any], trace: dict[str, Any]) -> None:
        unfinished = [
            identifier
            for identifier, status in changes["investigations"].items()
            if status is not InvestigationStatus.PASS
        ]
        if unfinished:
            raise WorkflowError("target platform study has unresolved investigations")

    @staticmethod
    def _write_profile_markdown(path: Path, profile: dict[str, Any]) -> Path:
        sections = {
            "## Repository and documentation map": "repository_documentation_map",
            "## Lifecycle and execution contexts": "lifecycle_execution_contexts",
            "## Hardware access and concurrency": "hardware_access_concurrency",
            "## Error, recovery and observability conventions": ("error_recovery_observability"),
            "## Coding and safety requirements": "coding_safety_requirements",
            "## Artifact and QEMU path": "artifact_qemu_path",
        }
        linked_sections = {
            "## Closest analogous implementation": "See `analogous_trace.json`.",
            "## Target API evidence": "See `api_table.json`.",
            "## Target changes and unresolved gaps": "See `change_plan.json`.",
        }
        lines = [
            PROFILE_HEADINGS[0],
            "",
            PROFILE_HEADINGS[1],
            "",
            f"- Target platform: {profile['target_platform']}",
            f"- Source root: {profile['source_root']}",
            f"- Revision: {profile['revision']}",
            f"- Artifact mode: {profile['artifact_mode']}",
            f"- Profile status: `{TargetProfileStatus.READY.value}`",
        ]
        for heading in PROFILE_HEADINGS[2:]:
            summary = (
                linked_sections[heading]
                if heading in linked_sections
                else str(profile[sections[heading]]["summary"])
            )
            lines.extend(("", heading, "", summary))
        output = path.with_name("profile.md")
        output.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output

    @staticmethod
    def _write_report(project: Project, report: dict[str, Any]) -> Path:
        report_root = project.control / "target-study"
        report_root.mkdir(parents=True, exist_ok=True)
        report_id = hashlib.sha256(
            json.dumps(report["inputs"], sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        report_path = report_root / f"validation-{report_id}.json"
        if report_path.exists():
            existing = TargetStudyService._load_json(report_path)
            stable = {key: value for key, value in report.items() if key != "validated_at"}
            if {key: value for key, value in existing.items() if key != "validated_at"} == stable:
                return report_path
            raise WorkflowError("target-study validation result changed for immutable inputs")
        report_path.write_bytes(TargetStudyService._json_bytes(report))
        return report_path

    @staticmethod
    def _controlled(project: Project, path: Path) -> Path:
        resolved = path.resolve()
        if resolved != project.root and project.root not in resolved.parents:
            raise WorkflowError("target-study submissions must remain inside the project")
        if not resolved.is_file():
            raise WorkflowError(f"target-study submission is not a file: {resolved}")
        return resolved

    @staticmethod
    def _load_json(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError(f"invalid target-study JSON: {path}") from error
        if not isinstance(value, dict):
            raise WorkflowError(f"target-study JSON must be an object: {path}")
        return value

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
