from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias

from ..core.models import WorkflowError
from .parsing import byte_limit, exact_object, http_url, relative_path, sha256
from .repository_role import RepositoryRole


class EvidenceAuthority(StrEnum):
    PRIMARY = "primary"
    CORROBORATED = "corroborated"


class AuthorityBasisKind(StrEnum):
    REPOSITORY_ENDORSEMENT = "repository_endorsement"
    CORROBORATED = "corroborated"


@dataclass(frozen=True, slots=True)
class CorroborationLocator:
    source_url: str
    expected_sha256: str
    max_bytes: int

    @classmethod
    def from_dict(cls, value: object) -> CorroborationLocator:
        candidate = exact_object(
            value,
            required={"source_url", "expected_sha256", "max_bytes"},
            label="corroboration locator",
        )
        return cls(
            http_url(candidate["source_url"], "corroboration URL"),
            sha256(candidate["expected_sha256"], "corroboration expected SHA256"),
            byte_limit(candidate["max_bytes"], "corroboration max_bytes"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_url": self.source_url,
            "expected_sha256": self.expected_sha256,
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True, slots=True)
class RepositoryEndorsementAuthority:
    repository: RepositoryRole
    path: str
    line_start: int
    line_end: int
    kind = AuthorityBasisKind.REPOSITORY_ENDORSEMENT

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "repository": self.repository.value,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass(frozen=True, slots=True)
class CorroboratedAuthority:
    sources: tuple[CorroborationLocator, ...]
    kind = AuthorityBasisKind.CORROBORATED

    def to_dict(self) -> dict[str, object]:
        return {"kind": self.kind.value, "sources": [source.to_dict() for source in self.sources]}


AuthorityBasis: TypeAlias = RepositoryEndorsementAuthority | CorroboratedAuthority


def parse_external_authority(value: object) -> AuthorityBasis:
    candidate = exact_object(
        value,
        required={"kind"},
        optional={"repository", "path", "line_start", "line_end", "sources"},
        label="external evidence authority",
    )
    try:
        kind = AuthorityBasisKind(candidate["kind"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("external evidence authority has an invalid kind") from error
    if kind is AuthorityBasisKind.CORROBORATED:
        return _corroborated(candidate)
    return _repository_endorsement(candidate)


def _repository_endorsement(candidate: dict[str, object]) -> RepositoryEndorsementAuthority:
    exact_object(
        candidate,
        required={"kind", "repository", "path", "line_start", "line_end"},
        label="repository endorsement authority",
    )
    try:
        repository = RepositoryRole(candidate["repository"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("repository endorsement authority has an invalid role") from error
    line_start = candidate["line_start"]
    line_end = candidate["line_end"]
    if (
        not isinstance(line_start, int)
        or not isinstance(line_end, int)
        or line_start <= 0
        or line_end <= 0
    ):
        raise WorkflowError("repository endorsement has an invalid source span")
    return RepositoryEndorsementAuthority(
        repository,
        relative_path(candidate["path"], "repository endorsement path"),
        line_start,
        line_end,
    )


def _corroborated(candidate: dict[str, object]) -> CorroboratedAuthority:
    exact_object(
        candidate,
        required={"kind", "sources"},
        label="corroborated authority",
    )
    raw_sources = candidate["sources"]
    if not isinstance(raw_sources, list) or not raw_sources:
        raise WorkflowError("corroborated authority requires retrieval sources")
    return CorroboratedAuthority(
        tuple(CorroborationLocator.from_dict(item) for item in raw_sources)
    )
