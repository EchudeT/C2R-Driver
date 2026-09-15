from __future__ import annotations

import json
from typing import Any

from .models import WorkflowError


def _json_object(data: bytes, kind: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"{kind} must be a UTF-8 JSON document") from error
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
    elif kind == "materials_manifest":
        try:
            lines = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("materials_manifest must be JSON Lines") from error
        if not lines or any("sha256" not in line or "revision" not in line for line in lines):
            raise WorkflowError("materials_manifest entries require sha256 and revision")
    elif kind == "source_identity_verification":
        value = _json_object(data, kind)
        if not isinstance(value.get("consistent"), bool):
            raise WorkflowError("source_identity_verification.consistent must be boolean")
    elif kind == "candidate_digest_anchor":
        value = _json_object(data, kind)
        digest = value.get("candidate_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise WorkflowError("candidate_digest_anchor.candidate_sha256 must be SHA256")
