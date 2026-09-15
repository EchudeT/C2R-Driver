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

    if kind == "identity_record":
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
    elif kind == "candidate_digest_anchor":
        value = _json_object(data, kind)
        digest = value.get("candidate_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise WorkflowError("candidate_digest_anchor.candidate_sha256 must be SHA256")
