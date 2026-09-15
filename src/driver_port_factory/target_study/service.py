from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..acquisition.service import AcquisitionService
from ..core.models import ActorRole, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from ..knowledge.index import KnowledgeIndex, file_sha256

PROFILE_SECTIONS = {
    "repository_documentation_map",
    "lifecycle_execution_contexts",
    "hardware_access_concurrency",
    "error_recovery_observability",
    "coding_safety_requirements",
    "artifact_qemu_path",
}
TRACE_STEPS = (
    "selection-configuration",
    "registration-match",
    "resource-acquisition",
    "device-initialization",
    "request-submission-completion",
    "interrupt-deferred-processing",
    "error-propagation-recovery",
    "stop-detach-cleanup",
    "artifact-inclusion-qemu-launch",
)
PROFILE_HEADINGS = (
    "# Target-Platform Profile",
    "## Identity",
    "## Repository and documentation map",
    "## Closest analogous implementation",
    "## Lifecycle and execution contexts",
    "## Target API evidence",
    "## Hardware access and concurrency",
    "## Error, recovery and observability conventions",
    "## Coding and safety requirements",
    "## Artifact and QEMU path",
    "## Target changes and unresolved gaps",
)


@dataclass(frozen=True, slots=True)
class TargetStudyResult:
    status: str
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
        profile_markdown: Path,
        api_table: Path,
        analogous_trace: Path,
        change_plan: Path,
    ) -> TargetStudyResult:
        project.ensure_role(*self.ROLES)
        stage = project.store.stage("target_platform_study")
        if stage.status is StageStatus.READY:
            project.start("target_platform_study")
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"target_platform_study must be READY or RUNNING, got {stage.status.value}"
            )
        paths = {
            "profile_json": self._controlled(project, profile_json),
            "profile_markdown": self._controlled(project, profile_markdown),
            "api_table": self._controlled(project, api_table),
            "analogous_trace": self._controlled(project, analogous_trace),
            "change_plan": self._controlled(project, change_plan),
        }
        errors: list[str] = []
        details: dict[str, Any] = {}
        try:
            profile = self._load_json(paths["profile_json"])
            api = self._load_json(paths["api_table"])
            trace = self._load_json(paths["analogous_trace"])
            changes = self._load_json(paths["change_plan"])
            knowledge = KnowledgeIndex(project.root)
            details["knowledge_status"] = knowledge.status()
            details["acquisition_verification"] = AcquisitionService().verify(project)
            if not details["acquisition_verification"]["valid"]:
                raise WorkflowError("one or more frozen baselines failed verification")
            details["profile"] = self._validate_profile(project, knowledge, profile)
            details["changes"] = self._validate_changes(knowledge, changes)
            details["api_table"] = self._validate_api_table(
                knowledge, api, set(details["changes"]["resolution_ids"])
            )
            details["analogous_trace"] = self._validate_trace(knowledge, trace)
            self._validate_profile_markdown(paths["profile_markdown"], profile)
        except (WorkflowError, OSError, UnicodeDecodeError) as error:
            errors.append(str(error))
        report = {
            "schema_version": 1,
            "status": "PASS" if not errors else "FAIL",
            "inputs": {
                name: {"path": str(path), "sha256": file_sha256(path)}
                for name, path in paths.items()
            },
            "details": details,
            "errors": errors,
            "validated_at": utc_now(),
        }
        report_root = project.control / "target-study"
        report_root.mkdir(parents=True, exist_ok=True)
        report_id = hashlib.sha256(
            json.dumps(report["inputs"], sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        report_path = report_root / f"validation-{report_id}.json"
        if report_path.exists():
            raise WorkflowError(
                "this target-study submission was already validated; submit a changed artifact"
            )
        report_path.write_bytes(self._json_bytes(report))
        if errors:
            project.add_artifact(
                "target_platform_study", "target_study_validation_attempt", report_path
            )
            return TargetStudyResult("FAIL", StageStatus.RUNNING, str(report_path), tuple(errors))
        project.add_artifact("target_platform_study", "target_profile", paths["profile_markdown"])
        project.add_artifact(
            "target_platform_study", "target_profile_structured", paths["profile_json"]
        )
        project.add_artifact("target_platform_study", "target_api_evidence", paths["api_table"])
        project.add_artifact(
            "target_platform_study", "analogous_driver_trace", paths["analogous_trace"]
        )
        project.add_artifact("target_platform_study", "target_change_plan", paths["change_plan"])
        project.add_artifact("target_platform_study", "target_study_report", report_path)
        project.complete("target_platform_study", StageStatus.PASS)
        return TargetStudyResult("PASS", StageStatus.PASS, str(report_path), ())

    def _validate_profile(
        self, project: Project, knowledge: KnowledgeIndex, profile: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "target_platform",
            "source_root",
            "revision",
            "artifact_mode",
            "profile_status",
            "unmodified_target_baseline",
            *PROFILE_SECTIONS,
        }
        self._require_fields(profile, required, "target profile")
        if profile["schema_version"] != 1 or profile["profile_status"] != "READY":
            raise WorkflowError("target profile must be schema_version=1 and READY")
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        target = self._checkout(checkouts, RepositoryRole.TARGET)
        expected_root = str((project.root / target.checkout_path).resolve())
        if profile["target_platform"] != project.config.target_platform:
            raise WorkflowError("target profile platform differs from the project target")
        if str(Path(profile["source_root"]).resolve()) != expected_root:
            raise WorkflowError("target profile source_root is not the frozen target baseline")
        if profile["revision"] != target.resolved_commit:
            raise WorkflowError("target profile revision differs from the frozen target revision")
        mode = project.load_json_artifact("environment_recovery", "artifact_mode_record")[
            "artifact_mode"
        ]
        if profile["artifact_mode"] != mode:
            raise WorkflowError("target profile artifact_mode differs from environment recovery")
        baseline = profile["unmodified_target_baseline"]
        self._require_fields(
            baseline,
            {"revision", "tree_id", "clean", "baseline_status", "baseline_evidence"},
            "unmodified target baseline",
        )
        if baseline["revision"] != target.resolved_commit or baseline["tree_id"] != target.tree_id:
            raise WorkflowError("unmodified target baseline identity does not match acquisition")
        if baseline["clean"] is not True:
            raise WorkflowError("unmodified target baseline must be recorded clean")
        if baseline["baseline_status"] not in {"PASS", "NOT_RUN", "BLOCKED"}:
            raise WorkflowError("invalid unmodified target baseline status")
        if not str(baseline["baseline_evidence"]).strip():
            raise WorkflowError("unmodified target baseline requires evidence or a blocker reason")
        section_results = {}
        for section in sorted(PROFILE_SECTIONS):
            value = profile[section]
            if not isinstance(value, dict) or not str(value.get("summary", "")).strip():
                raise WorkflowError(f"target profile section {section} requires a summary")
            references = value.get("evidence_refs")
            if not isinstance(references, list) or not references:
                raise WorkflowError(
                    f"target profile section {section} requires target evidence_refs"
                )
            section_results[section] = [
                self._verify_evidence(knowledge, reference) for reference in references
            ]
        return {
            "target_platform": profile["target_platform"],
            "revision": profile["revision"],
            "artifact_mode": profile["artifact_mode"],
            "sections": section_results,
        }

    def _validate_api_table(
        self,
        knowledge: KnowledgeIndex,
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
            self._require_fields(entry, required, "target API entry")
            identifier = str(entry["api_id"])
            if identifier in identifiers:
                raise WorkflowError(f"duplicate target API ID: {identifier}")
            identifiers.add(identifier)
            for field in required - {"confidence"}:
                if not str(entry[field]).strip():
                    raise WorkflowError(f"target API {identifier} has empty {field}")
            confidence = entry["confidence"]
            if confidence not in {"VERIFIED", "INFERRED", "UNKNOWN"}:
                raise WorkflowError(f"target API {identifier} has invalid confidence")
            evidence = {}
            if confidence in {"VERIFIED", "INFERRED"}:
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
                    evidence[field] = self._verify_evidence(knowledge, reference)
            else:
                resolution_id = entry.get("investigation_or_target_change_id")
                if not resolution_id or resolution_id not in resolution_ids:
                    raise WorkflowError(
                        f"UNKNOWN target API {identifier} needs a concrete investigation/change"
                    )
            verified_entries.append(
                {"api_id": identifier, "confidence": confidence, "evidence": evidence}
            )
        return {"entry_count": len(verified_entries), "entries": verified_entries}

    def _validate_trace(self, knowledge: KnowledgeIndex, trace: dict[str, Any]) -> dict[str, Any]:
        required = {
            "schema_version",
            "selected_implementation",
            "selection_rationale",
            "framework_owners",
            "differences_not_to_copy",
            "steps",
        }
        self._require_fields(trace, required, "analogous driver trace")
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
        observed = tuple(str(step.get("stage")) for step in steps)
        if observed != TRACE_STEPS:
            raise WorkflowError(
                "analogous trace must contain the complete ordered registration-to-QEMU path"
            )
        results = []
        for step in steps:
            status = step.get("status")
            if status not in {"VERIFIED", "NOT_APPLICABLE"}:
                raise WorkflowError(f"analogous trace step {step['stage']} has invalid status")
            if not str(step.get("summary", "")).strip():
                raise WorkflowError(f"analogous trace step {step['stage']} needs a summary")
            references = step.get("evidence_refs")
            if not isinstance(references, list) or not references:
                raise WorkflowError(f"analogous trace step {step['stage']} needs target evidence")
            results.append(
                {
                    "stage": step["stage"],
                    "status": status,
                    "evidence": [
                        self._verify_evidence(knowledge, reference) for reference in references
                    ],
                }
            )
        return {"selected_implementation": trace["selected_implementation"], "steps": results}

    def _validate_changes(
        self, knowledge: KnowledgeIndex, changes: dict[str, Any]
    ) -> dict[str, Any]:
        required = {
            "schema_version",
            "integration_path",
            "required_change_level",
            "driver_owned_paths",
            "proposed_preexisting_changes",
            "investigations",
        }
        self._require_fields(changes, required, "target change plan")
        if changes["schema_version"] != 1:
            raise WorkflowError("target change plan schema_version must be 1")
        if changes["required_change_level"] not in {
            "driver-owned",
            "integration-wiring",
            "target-api-framework",
        }:
            raise WorkflowError("target change plan has an invalid change level")
        if not str(changes["integration_path"]).strip():
            raise WorkflowError("target change plan requires an integration path")
        if not isinstance(changes["driver_owned_paths"], list) or not changes["driver_owned_paths"]:
            raise WorkflowError("target change plan requires driver-owned paths")
        resolution_ids: set[str] = set()
        investigations = changes["investigations"]
        if not isinstance(investigations, list):
            raise WorkflowError("target change investigations must be a list")
        for investigation in investigations:
            self._require_fields(
                investigation,
                {"investigation_id", "question", "planned_action", "status"},
                "target investigation",
            )
            if investigation["status"] not in {"PLANNED", "BLOCKED", "PASS"}:
                raise WorkflowError("target investigation has an invalid status")
            resolution_ids.add(str(investigation["investigation_id"]))
        proposed = changes["proposed_preexisting_changes"]
        if not isinstance(proposed, list):
            raise WorkflowError("proposed_preexisting_changes must be a list")
        required_change_fields = {
            "change_id",
            "driver_contract_ids",
            "problem_and_observed_blocker",
            "target_evidence",
            "alternatives_considered_and_why_insufficient",
            "selected_change_level",
            "exact_files_and_symbols",
            "smallest_expected_behavioral_effect",
            "public_api_abi_or_safety_impact",
            "validation_plan",
            "rollback_method",
            "status",
            "broad_impact",
            "approval_status",
        }
        for change in proposed:
            self._require_fields(change, required_change_fields, "pre-existing target change")
            if change["selected_change_level"] not in {
                "integration-wiring",
                "target-api-framework",
            }:
                raise WorkflowError("pre-existing target change has an invalid level")
            if change["broad_impact"] and change["approval_status"] != "APPROVED":
                raise WorkflowError(
                    f"broad target change {change['change_id']} requires explicit approval"
                )
            self._verify_evidence(knowledge, change["target_evidence"])
            resolution_ids.add(str(change["change_id"]))
        if changes["required_change_level"] == "driver-owned" and proposed:
            raise WorkflowError(
                "driver-owned integration cannot declare pre-existing target changes"
            )
        return {
            "required_change_level": changes["required_change_level"],
            "proposed_change_count": len(proposed),
            "resolution_ids": sorted(resolution_ids),
        }

    @staticmethod
    def _validate_profile_markdown(path: Path, profile: dict[str, Any]) -> None:
        text = path.read_text(encoding="utf-8")
        missing = [heading for heading in PROFILE_HEADINGS if heading not in text]
        if missing:
            raise WorkflowError(
                "target profile Markdown is missing template headings: " + ", ".join(missing)
            )
        required_values = (
            str(profile["target_platform"]),
            str(profile["source_root"]),
            str(profile["revision"]),
            str(profile["artifact_mode"]),
        )
        if any(value not in text for value in required_values):
            raise WorkflowError(
                "target profile Markdown does not contain the frozen identity values"
            )
        if "Profile status: `READY`" not in text:
            raise WorkflowError("target profile Markdown must set Profile status to READY")

    @staticmethod
    def _verify_evidence(knowledge: KnowledgeIndex, reference: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(reference, dict) or not reference.get("chunk_id"):
            raise WorkflowError("target evidence reference requires chunk_id")
        exact = knowledge.show(str(reference["chunk_id"]))["result"]
        if exact["domain"] != "target":
            raise WorkflowError(
                f"target-study evidence {exact['chunk_id']} is not in the target domain"
            )
        if reference.get("record_id") and reference["record_id"] != exact["record_id"]:
            raise WorkflowError(f"target evidence record mismatch for {exact['chunk_id']}")
        original = knowledge.controlled_path(str(exact["path"]))
        if file_sha256(original) != exact["sha256"]:
            raise WorkflowError(f"target evidence hash changed for {exact['path']}")
        lines = original.read_text(encoding="utf-8").splitlines()
        if not (1 <= int(exact["line_start"]) <= int(exact["line_end"]) <= len(lines)):
            raise WorkflowError(f"target evidence locator is invalid for {exact['chunk_id']}")
        return {
            "chunk_id": exact["chunk_id"],
            "record_id": exact["record_id"],
            "path": exact["path"],
            "line_start": exact["line_start"],
            "line_end": exact["line_end"],
            "revision": exact["revision"],
            "sha256": exact["sha256"],
        }

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
    def _require_fields(value: Any, fields: set[str], label: str) -> None:
        if not isinstance(value, dict):
            raise WorkflowError(f"{label} must be an object")
        missing = sorted(fields - value.keys())
        if missing:
            raise WorkflowError(f"{label} missing fields: {', '.join(missing)}")

    @staticmethod
    def _checkout(records: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in records if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"expected one {role.value} checkout")
        return matches[0]

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )
