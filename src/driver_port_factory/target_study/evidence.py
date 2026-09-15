from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any

from ..core.models import WorkflowError
from ..knowledge.contracts import KnowledgeDomain
from ..knowledge.index import KnowledgeIndex, file_sha256


class TargetEvidenceVerifier:
    """Resolve a target-study citation back to an immutable indexed source location."""

    def __init__(self, knowledge: KnowledgeIndex) -> None:
        self.knowledge = knowledge

    def verify(self, reference: Any) -> dict[str, Any]:
        if not isinstance(reference, dict) or not reference.get("chunk_id"):
            raise WorkflowError("target evidence reference requires chunk_id")
        exact = self.knowledge.show(str(reference["chunk_id"]))["result"]
        if exact["domain"] != KnowledgeDomain.TARGET.value:
            raise WorkflowError(
                f"target-study evidence {exact['chunk_id']} is not in the target domain"
            )
        if reference.get("record_id") and reference["record_id"] != exact["record_id"]:
            raise WorkflowError(f"target evidence record mismatch for {exact['chunk_id']}")
        original = self.knowledge.controlled_path(str(exact["path"]))
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


def require_fields(value: Any, fields: AbstractSet[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkflowError(f"{label} must be an object")
    missing = sorted(fields - value.keys())
    if missing:
        raise WorkflowError(f"{label} missing fields: {', '.join(missing)}")
    return value
