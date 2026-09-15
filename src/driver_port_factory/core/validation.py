from __future__ import annotations

import json
from typing import Any

from .models import WorkflowError


def _json_value(data: bytes, kind: str) -> Any:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"{kind} must be a UTF-8 JSON document") from error


def _json_object(data: bytes, kind: str) -> dict[str, Any]:
    value = _json_value(data, kind)
    if not isinstance(value, dict):
        raise WorkflowError(f"{kind} must be a JSON object")
    return value


def validate_artifact(kind: str, data: bytes) -> None:
    """Validate high-value control artifacts before they enter a stage gate."""

    if kind == "request_record":
        value = _json_object(data, kind)
        required = {
            "source_platform",
            "target_platform",
            "user_supplied_driver_name",
            "raw_request",
            "intake_status",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"request_record missing fields: {', '.join(missing)}")
    elif kind == "driver_candidates":
        value = _json_object(data, kind)
        if not isinstance(value.get("candidates"), list):
            raise WorkflowError("driver_candidates.candidates must be a list")
        if value.get("metadata_scope") != "LIGHTWEIGHT_ONLY":
            raise WorkflowError("driver candidate resolution must remain lightweight before clone")
    elif kind == "scope_confirmation":
        value = _json_object(data, kind)
        if value.get("identity_status") != "CONFIRMED":
            raise WorkflowError("scope_confirmation must have identity_status=CONFIRMED")
        if not isinstance(value.get("selected_candidate"), dict):
            raise WorkflowError("scope_confirmation requires a selected_candidate")
    elif kind == "migration_envelope":
        value = _json_object(data, kind)
        required = {
            "source_platform",
            "target_platform",
            "canonical_source_driver_name",
            "source_driver_entry_or_repository_hint",
            "device_family",
            "bus_or_transport",
            "intended_subset",
            "excluded_variants",
            "identity_status",
        }
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"migration_envelope missing fields: {', '.join(missing)}")
        if value["identity_status"] != "FROZEN":
            raise WorkflowError("migration_envelope must have identity_status=FROZEN")
        if not isinstance(value["intended_subset"], list) or not value["intended_subset"]:
            raise WorkflowError("migration_envelope.intended_subset must be non-empty")
    elif kind == "identity_record":
        value = _json_object(data, kind)
        required = {"canonical_name", "bus", "device_scope", "source_paths", "confirmed"}
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"identity_record missing fields: {', '.join(missing)}")
        if value["confirmed"] is not True:
            raise WorkflowError("identity_record must explicitly set confirmed=true")
        if not isinstance(value["device_scope"], list) or not value["device_scope"]:
            raise WorkflowError("identity_record.device_scope must be a non-empty list")
        if not isinstance(value["source_paths"], list) or not value["source_paths"]:
            raise WorkflowError("identity_record.source_paths must be a non-empty list")
    elif kind == "revision_manifest":
        value = _json_object(data, kind)
        for component in ("source", "target", "qemu"):
            entry = value.get(component)
            if not isinstance(entry, dict) or not entry.get("revision"):
                raise WorkflowError(
                    f"revision_manifest.{component}.revision must be a non-empty pinned value"
                )
    elif kind == "acquisition_plan":
        value = _json_object(data, kind)
        repositories = value.get("repositories")
        if not isinstance(repositories, list):
            raise WorkflowError("acquisition_plan.repositories must be a list")
        roles = {entry.get("role") for entry in repositories if isinstance(entry, dict)}
        if roles != {"source", "target", "qemu"}:
            raise WorkflowError("acquisition_plan requires source, target, and qemu repositories")
        for entry in repositories:
            revision = entry.get("resolved_commit", "")
            if not isinstance(revision, str) or len(revision) not in {40, 64}:
                raise WorkflowError("each acquisition repository needs a full Git commit")
    elif kind == "acquisition_manifest":
        value = _json_object(data, kind)
        if not isinstance(value.get("checkouts"), list) or len(value["checkouts"]) != 3:
            raise WorkflowError("acquisition_manifest requires three controlled checkouts")
        if not isinstance(value.get("source_identity_verification"), dict):
            raise WorkflowError("acquisition_manifest requires source identity verification")
    elif kind in {
        "materials_manifest",
        "knowledge_materials_manifest",
        "source_closure_materials_manifest",
    }:
        try:
            lines = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"{kind} must be JSON Lines") from error
        if not lines or any("sha256" not in line or "revision" not in line for line in lines):
            raise WorkflowError(f"{kind} entries require sha256 and revision")
    elif kind == "source_identity_verification":
        value = _json_object(data, kind)
        if not isinstance(value.get("consistent"), bool):
            raise WorkflowError("source_identity_verification.consistent must be boolean")
    elif kind == "environment_inventory":
        value = _json_object(data, kind)
        if not isinstance(value.get("host"), dict) or not isinstance(value.get("tools"), list):
            raise WorkflowError("environment_inventory requires host and tools records")
        if not isinstance(value.get("frozen_repositories"), dict):
            raise WorkflowError("environment_inventory requires frozen repository identities")
    elif kind == "artifact_mode_candidates":
        value = _json_object(data, kind)
        if not isinstance(value.get("candidates"), list) or not value.get("selection_rule"):
            raise WorkflowError("artifact_mode_candidates requires candidates and a selection rule")
    elif kind == "environment_experiment_plan":
        value = _json_object(data, kind)
        if value.get("milestone") != "EXPERIMENT_READY":
            raise WorkflowError("environment plan must target EXPERIMENT_READY")
        if not isinstance(value.get("command"), list) or not value["command"]:
            raise WorkflowError("environment plan requires a non-empty command")
        if not isinstance(value.get("expected_markers"), list) or not value["expected_markers"]:
            raise WorkflowError("environment plan requires expected markers")
    elif kind in {"environment_recovery_attempt", "experiment_ready_run"}:
        value = _json_object(data, kind)
        if not isinstance(value.get("route"), dict) or not isinstance(value.get("run"), dict):
            raise WorkflowError(f"{kind} requires route and run evidence")
        if kind == "experiment_ready_run" and value.get("readiness") != "PASS":
            raise WorkflowError("experiment_ready_run must have readiness=PASS")
    elif kind == "artifact_mode_record":
        value = _json_object(data, kind)
        required = {"artifact_mode", "route_kind", "selected_route_id"}
        missing = sorted(required - value.keys())
        if missing:
            raise WorkflowError(f"artifact_mode_record missing fields: {', '.join(missing)}")
    elif kind == "experiment_route":
        value = _json_object(data, kind)
        if value.get("milestone") != "EXPERIMENT_READY":
            raise WorkflowError("experiment_route must record EXPERIMENT_READY")
        if value.get("migrated_driver_runtime_ready") is not False:
            raise WorkflowError(
                "environment recovery cannot claim migrated-driver runtime readiness"
            )
    elif kind == "kb_status":
        value = _json_object(data, kind)
        if value.get("status") != "READY" or not isinstance(value.get("manifest_fingerprint"), str):
            raise WorkflowError("kb_status must be READY with a manifest fingerprint")
    elif kind == "kb_query_contract":
        value = _json_object(data, kind)
        commands = value.get("commands")
        if not isinstance(commands, dict) or set(commands) != {
            "status",
            "rebuild",
            "search",
            "show",
        }:
            raise WorkflowError("kb_query_contract requires all four query commands")
        if not value.get("template_sha256") or not value.get("manifest_sha256"):
            raise WorkflowError("kb_query_contract requires template and manifest hashes")
    elif kind == "generated_kb_skill":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkflowError("generated_kb_skill must be UTF-8") from error
        if "{{" in text or "}}" in text:
            raise WorkflowError("generated_kb_skill contains unresolved placeholders")
        for operation in ("status", "rebuild", "search", "show"):
            if operation not in text:
                raise WorkflowError(
                    f"generated_kb_skill does not describe the {operation} operation"
                )
    elif kind == "kb_readiness_report":
        value = _json_object(data, kind)
        if value.get("status") != "PASS" or value.get("failed_probe_ids"):
            raise WorkflowError("kb_readiness_report must have no failed probes")
    elif kind == "target_probe_results":
        value = _json_object(data, kind)
        probes = value.get("probes")
        if value.get("status") != "PASS" or not isinstance(probes, list) or not probes:
            raise WorkflowError("target_probe_results must contain passing probes")
        if any(probe.get("status") != "PASS" for probe in probes):
            raise WorkflowError("every target knowledge probe must pass")
    elif kind == "target_profile":
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkflowError("target_profile must be UTF-8 Markdown") from error
        if "# Target-Platform Profile" not in text or "Profile status: `READY`" not in text:
            raise WorkflowError("target_profile must be a completed READY profile")
    elif kind == "target_profile_structured":
        value = _json_object(data, kind)
        if value.get("schema_version") != 1 or value.get("profile_status") != "READY":
            raise WorkflowError("target_profile_structured must be schema_version=1 and READY")
    elif kind == "target_api_evidence":
        value = _json_object(data, kind)
        if value.get("schema_version") != 1 or not value.get("entries"):
            raise WorkflowError("target_api_evidence requires at least one API entry")
    elif kind == "analogous_driver_trace":
        value = _json_object(data, kind)
        if value.get("schema_version") != 1 or not value.get("steps"):
            raise WorkflowError("analogous_driver_trace requires ordered trace steps")
    elif kind == "target_change_plan":
        value = _json_object(data, kind)
        if value.get("schema_version") != 1 or not value.get("integration_path"):
            raise WorkflowError("target_change_plan requires an integration path")
    elif kind == "target_study_report":
        value = _json_object(data, kind)
        if value.get("status") != "PASS" or value.get("errors"):
            raise WorkflowError("target_study_report must pass without validation errors")
    elif kind == "source_closure":
        value = _json_object(data, kind)
        if value.get("schema_version") != 1 or value.get("closure_status") != "CLOSED":
            raise WorkflowError("source_closure must be schema_version=1 and CLOSED")
        if not isinstance(value.get("compiler"), dict):
            raise WorkflowError("source_closure requires a compiler identity")
        if not isinstance(value.get("translation_units"), list) or not value["translation_units"]:
            raise WorkflowError("source_closure requires translation units")
        if value.get("unresolved_dependencies") != []:
            raise WorkflowError("source_closure cannot contain unresolved dependencies")
    elif kind in {"source_closure_report", "source_closure_validation_attempt"}:
        value = _json_object(data, kind)
        errors = value.get("errors")
        if kind == "source_closure_report" and (value.get("status") != "PASS" or errors != []):
            raise WorkflowError("source_closure_report must pass without errors")
        if kind == "source_closure_validation_attempt" and (
            value.get("status") != "FAIL" or not isinstance(errors, list) or not errors
        ):
            raise WorkflowError("source_closure_validation_attempt must preserve a failed attempt")
    elif kind == "compile_manifest":
        value = _json_object(data, kind)
        compiler = value.get("compiler")
        units = value.get("translation_units")
        if value.get("schema_version") != 1 or not value.get("source_revision"):
            raise WorkflowError("compile_manifest requires its source revision")
        if not isinstance(compiler, dict) or len(str(compiler.get("sha256", ""))) != 64:
            raise WorkflowError("compile_manifest requires a hashed compiler")
        if not isinstance(units, list) or not units:
            raise WorkflowError("compile_manifest requires translation units")
    elif kind == "compilation_database":
        value = _json_value(data, kind)
        if not isinstance(value, list) or not value:
            raise WorkflowError("compilation_database must be a non-empty JSON array")
        for entry in value:
            if not isinstance(entry, dict) or not all(
                entry.get(field) for field in ("directory", "file", "arguments")
            ):
                raise WorkflowError("compilation_database entries require directory, file, argv")
            if not isinstance(entry["arguments"], list):
                raise WorkflowError("compilation_database arguments must be an argv list")
    elif kind == "knowledge_revision":
        value = _json_object(data, kind)
        index_status = value.get("index_status")
        if value.get("schema_version") != 1 or len(str(value.get("manifest_sha256", ""))) != 64:
            raise WorkflowError("knowledge_revision requires a hashed schema_version=1 manifest")
        if not isinstance(value.get("added_materials"), list):
            raise WorkflowError("knowledge_revision.added_materials must be a list")
        if not isinstance(index_status, dict) or index_status.get("status") != "READY":
            raise WorkflowError("knowledge_revision requires a rebuilt READY index")
    elif kind == "candidate_digest_anchor":
        value = _json_object(data, kind)
        digest = value.get("candidate_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise WorkflowError("candidate_digest_anchor.candidate_sha256 must be SHA256")
