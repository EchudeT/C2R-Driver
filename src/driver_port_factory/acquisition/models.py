from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class RepositoryRole(StrEnum):
    SOURCE = "source"
    TARGET = "target"
    QEMU = "qemu"


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    role: RepositoryRole
    platform: str
    url: str
    requested_ref: str
    resolved_commit: str
    selection_rule: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["role"] = self.role.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RepositorySpec:
        data = dict(value)
        data["role"] = RepositoryRole(data["role"])
        return cls(**data)


@dataclass(frozen=True, slots=True)
class AcquisitionPlan:
    schema_version: int
    repositories: tuple[RepositorySpec, ...]
    migration_envelope_digest: str
    planned_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "repositories": [repository.to_dict() for repository in self.repositories],
            "migration_envelope_digest": self.migration_envelope_digest,
            "planned_at": self.planned_at,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AcquisitionPlan:
        return cls(
            schema_version=int(value["schema_version"]),
            repositories=tuple(
                RepositorySpec.from_dict(repository) for repository in value["repositories"]
            ),
            migration_envelope_digest=str(value["migration_envelope_digest"]),
            planned_at=str(value["planned_at"]),
        )


@dataclass(frozen=True, slots=True)
class CheckoutRecord:
    role: RepositoryRole
    platform: str
    source_url: str
    requested_ref: str
    resolved_commit: str
    tree_id: str
    bare_repository: str
    checkout_path: str
    clean: bool
    lock_sha256: str
    acquired_at: str

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["role"] = self.role.value
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> CheckoutRecord:
        data = dict(value)
        data["role"] = RepositoryRole(data["role"])
        return cls(**data)
