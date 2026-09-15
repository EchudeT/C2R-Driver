from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import ArtifactRef, WorkflowError
from ..core.validation import BundleValidationContext
from .contracts import SourceAnalysisArtifact


@dataclass(frozen=True, slots=True)
class ArtifactPayload:
    ref: ArtifactRef
    data: bytes

    @property
    def source_path(self) -> Path:
        if not self.ref.source:
            raise WorkflowError(f"{self.ref.kind} requires a provenance source path")
        return Path(self.ref.source).resolve()


class StructuredArtifactInventory:
    """Resolve bundle artifacts and prove that every linked artifact is consumed once."""

    LINKED_KINDS = frozenset(
        {
            SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT.value,
            SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX.value,
            SourceAnalysisArtifact.STRUCTURED_C_COMMAND_RECORDS.value,
        }
    )

    def __init__(self, context: BundleValidationContext) -> None:
        self.project_root = context.project_root
        self.current = tuple(ArtifactPayload(*item) for item in context.artifacts)
        self.dependencies = tuple(ArtifactPayload(*item) for item in context.dependency_artifacts)
        self._expected_links: Counter[tuple[str, Path, str]] = Counter()

    def current_one(self, kind: SourceAnalysisArtifact) -> ArtifactPayload:
        return self._one(self.current, kind)

    def dependency_one(self, kind: SourceAnalysisArtifact) -> ArtifactPayload:
        return self._one(self.dependencies, kind)

    def linked(
        self,
        kind: SourceAnalysisArtifact,
        record: Any,
        label: str,
    ) -> ArtifactPayload:
        if not isinstance(record, dict):
            raise WorkflowError(f"{label} has no artifact reference")
        path = self.project_path(record.get("path"), label)
        digest = record.get("sha256")
        matches = [
            payload
            for payload in self.current
            if payload.ref.kind == kind.value
            and payload.source_path == path
            and payload.ref.digest == digest
        ]
        if len(matches) != 1:
            raise WorkflowError(f"{label} does not identify exactly one finalized artifact")
        payload = matches[0]
        if hashlib.sha256(payload.data).hexdigest() != digest:
            raise WorkflowError(f"{label} content hash differs")
        if "size" in record and record.get("size") != len(payload.data):
            raise WorkflowError(f"{label} content size differs")
        self._expected_links[(kind.value, path, str(digest))] += 1
        return payload

    def validate_consumption(self) -> None:
        actual: Counter[tuple[str, Path, str]] = Counter()
        for payload in self.current:
            if payload.ref.kind in self.LINKED_KINDS:
                actual[(payload.ref.kind, payload.source_path, payload.ref.digest)] += 1
        if actual != self._expected_links:
            raise WorkflowError("structured linked-artifact inventory differs from unit references")

    def project_path(self, value: Any, label: str) -> Path:
        if not isinstance(value, str) or not value:
            raise WorkflowError(f"{label} has no path")
        path = (self.project_root / value).resolve()
        require_within(self.project_root, path, label)
        return path

    @staticmethod
    def _one(
        payloads: tuple[ArtifactPayload, ...], kind: SourceAnalysisArtifact
    ) -> ArtifactPayload:
        matches = [payload for payload in payloads if payload.ref.kind == kind.value]
        if len(matches) != 1:
            raise WorkflowError(f"structured C bundle requires exactly one {kind.value}")
        return matches[0]


def require_within(root: Path, path: Path, label: str) -> None:
    if path != root and root not in path.parents:
        raise WorkflowError(f"{label} escapes its approved root")
