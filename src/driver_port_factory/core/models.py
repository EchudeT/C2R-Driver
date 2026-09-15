from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


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


class EvidenceStatus(StrEnum):
    VERIFIED = "VERIFIED"
    INFERRED = "INFERRED"
    PLANNED = "PLANNED"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    BLOCKED = "BLOCKED"
    FAIL = "FAIL"
    PASS = "PASS"


class EvaluationResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_RUN = "NOT_RUN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    HARNESS_INVALID = "HARNESS_INVALID"
    NON_INDEPENDENT = "NON_INDEPENDENT"


class StageOwner(StrEnum):
    STATIC = "static"
    CODEX = "codex"
    HYBRID = "hybrid"
    INDEPENDENT = "independent"


class StageName(StrEnum):
    PROJECT_INIT = "project_init"
    SOURCE_CLOSURE = "source_closure"
    STRUCTURED_C_ANALYSIS = "structured_c_analysis"


class IntakeStatus(StrEnum):
    UNRESOLVED = "UNRESOLVED"
    ANALYZING = "ANALYZING"
    NEEDS_USER_CONFIRMATION = "NEEDS_USER_CONFIRMATION"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    CONFIRMED = "CONFIRMED"
    FROZEN = "FROZEN"


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
    name: str
    description: str
    owner: StageOwner
    dependencies: tuple[str, ...] = ()
    required_outputs: tuple[str, ...] = ()
    allowed_roles: tuple[ActorRole, ...] = ()
    accept_failed_dependencies: bool = False


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    digest: str
    kind: str
    size: int
    cas_path: str
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class StageView:
    name: str
    position: int
    owner: StageOwner
    status: StageStatus
    dependencies: tuple[str, ...]
    required_outputs: tuple[str, ...]
    description: str


class WorkflowError(RuntimeError):
    """Raised when a workflow invariant would be violated."""
