from __future__ import annotations

from dataclasses import dataclass

from ..core.contracts import ArtifactKey, StageKey
from ..core.models import ArtifactDirection, ArtifactRef, WorkflowError
from ..core.project import Project
from .parsing import exact_object


@dataclass(frozen=True, slots=True)
class ArtifactOccurrence:
    digest: str
    ordinal: int


@dataclass(frozen=True, slots=True)
class JobResultBinding:
    digest: str
    ordinal: int
    source: str

    @classmethod
    def from_dict(cls, value: object) -> JobResultBinding:
        candidate = exact_object(
            value,
            required={"digest", "ordinal", "source"},
            label="job result binding",
        )
        ordinal = candidate["ordinal"]
        if not isinstance(ordinal, int) or ordinal < 0:
            raise WorkflowError("job result ordinal is invalid")
        return cls(
            sha256(candidate["digest"], "job result"),
            ordinal,
            nonempty(candidate["source"], "job result source"),
        )

    def to_dict(self) -> dict[str, object]:
        return {"digest": self.digest, "ordinal": self.ordinal, "source": self.source}


def exact_occurrence(
    project: Project,
    *,
    stage: StageKey,
    kind: ArtifactKey,
    occurrence: ArtifactOccurrence,
) -> ArtifactRef:
    matches = [
        ref
        for ref in project.artifact_refs(stage=stage, direction=ArtifactDirection.OUTPUT)
        if ref.kind == kind.value
        and ref.digest == occurrence.digest
        and ref.ordinal == occurrence.ordinal
    ]
    if len(matches) != 1:
        raise WorkflowError(
            f"expected one {kind.value} occurrence at ordinal {occurrence.ordinal} "
            f"with digest {occurrence.digest}"
        )
    return matches[0]


def ordinal(value: int | None) -> int:
    if value is None:
        raise WorkflowError("artifact occurrence has no persisted ordinal")
    return value


def nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be a non-empty string")
    return value.strip()


def sha256(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WorkflowError(f"{label} digest must be lowercase SHA256")
    return value
