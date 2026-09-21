from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .contracts import ArtifactKey, StageKey


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ActorRole(StrEnum):
    DEVELOPER = "developer"
    MIGRATION_OPERATOR = "migration_operator"
    CURATOR = "curator"
    EVALUATOR = "evaluator"
    AUDITOR = "auditor"


class EvaluationMode(StrEnum):
    DEVELOPER_EVIDENCE = "developer-evidence"
    PROSPECTIVE_BLIND = "prospective-blind"
    POST_HOC_SEALED_BLIND = "post-hoc-sealed-blind"


class StageStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"

    @property
    def terminal(self) -> bool:
        return self in {
            self.PASS,
            self.FAIL,
            self.BLOCKED,
            self.INCONCLUSIVE,
            self.NOT_APPLICABLE,
        }

    @property
    def satisfies_dependency(self) -> bool:
        return self in {self.PASS, self.NOT_APPLICABLE}


class StageOwner(StrEnum):
    STATIC = "static"
    CODEX = "codex"
    HYBRID = "hybrid"
    INDEPENDENT = "independent"


class ArtifactDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class OutputCardinality(StrEnum):
    EXACTLY_ONE = "exactly-one"
    ONE_OR_MORE = "one-or-more"


@dataclass(frozen=True, slots=True)
class ArtifactRequirement:
    kind: ArtifactKey
    cardinality: OutputCardinality = OutputCardinality.EXACTLY_ONE

    @property
    def value(self) -> str:
        return self.kind.value


@dataclass(frozen=True, slots=True)
class ProjectConfig:
    project_id: str
    source_platform: str
    target_platform: str
    driver_name: str
    evaluation_mode: EvaluationMode
    actor_role: ActorRole
    skill_root: str | None = None
    prompt_pack: str | None = None
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evaluation_mode"] = self.evaluation_mode.value
        value["actor_role"] = self.actor_role.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ProjectConfig:
        data = dict(value)
        data["evaluation_mode"] = EvaluationMode(data["evaluation_mode"])
        data["actor_role"] = ActorRole(data["actor_role"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class StageSpec:
    name: StageKey
    description: str
    owner: StageOwner
    dependencies: tuple[StageKey, ...] = ()
    required_outputs: tuple[ArtifactRequirement, ...] = ()
    auxiliary_outputs: tuple[ArtifactKey, ...] = ()
    allowed_roles: tuple[ActorRole, ...] = ()
    accept_failed_dependencies: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactContent:
    digest: str
    kind: str
    size: int
    cas_path: str


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    """One stage-owned occurrence of immutable artifact content."""

    content: ArtifactContent
    source: str
    ordinal: int | None = None

    @property
    def digest(self) -> str:
        return self.content.digest

    @property
    def kind(self) -> str:
        return self.content.kind

    @property
    def size(self) -> int:
        return self.content.size

    @property
    def cas_path(self) -> str:
        return self.content.cas_path

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "kind": self.kind,
            "size": self.size,
            "cas_path": self.cas_path,
            "source": self.source,
            "ordinal": self.ordinal,
        }


@dataclass(frozen=True, slots=True)
class FileArtifact:
    kind: ArtifactKey
    path: Path


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    kind: ArtifactKey
    data: bytes
    source: str


@dataclass(frozen=True, slots=True)
class StageView:
    name: StageKey
    position: int
    owner: StageOwner
    status: StageStatus
    dependencies: tuple[StageKey, ...]
    required_outputs: tuple[ArtifactRequirement, ...]
    auxiliary_outputs: tuple[ArtifactKey, ...]
    description: str
    message: str | None = None


class WorkflowError(RuntimeError):
    """Raised when a workflow invariant would be violated."""


class RepairExhausted(WorkflowError):
    """The same prerequisite repair repeated with identical accepted inputs."""
