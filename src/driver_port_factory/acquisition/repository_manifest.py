from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .commands import RepositoryCommandRecord
from .parsing import exact_object, nonempty, schema_version, sha256
from .repository_checkout import CheckoutRecord, WritableTargetTree
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class RepositoryAcquisition:
    schema_version: int
    project_id: str
    acquired_at: str
    migration_envelope_sha256: str
    repository_plan_sha256: str
    checkouts: tuple[CheckoutRecord, ...]
    target_worktree: WritableTargetTree
    commands: tuple[RepositoryCommandRecord, ...]

    def checkout(self, role: RepositoryRole) -> CheckoutRecord:
        matches = tuple(record for record in self.checkouts if record.role is role)
        if len(matches) != 1:
            raise WorkflowError(
                f"repository acquisition requires exactly one {role.value} checkout"
            )
        return matches[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "project_id": self.project_id,
            "acquired_at": self.acquired_at,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "repository_plan_sha256": self.repository_plan_sha256,
            "checkouts": [record.to_dict() for record in self.checkouts],
            "target_worktree": self.target_worktree.to_dict(),
            "commands": [command.to_dict() for command in self.commands],
        }

    @classmethod
    def from_dict(cls, value: object) -> RepositoryAcquisition:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "project_id",
                "acquired_at",
                "migration_envelope_sha256",
                "repository_plan_sha256",
                "checkouts",
                "target_worktree",
                "commands",
            },
            label="repository manifest",
        )
        schema_version(candidate, "repository manifest")
        checkouts = candidate["checkouts"]
        commands = candidate["commands"]
        if not isinstance(checkouts, list) or not isinstance(commands, list) or not commands:
            raise WorkflowError("repository manifest collections are invalid")
        return cls(
            1,
            nonempty(candidate["project_id"], "repository manifest project ID"),
            nonempty(candidate["acquired_at"], "repository acquisition timestamp"),
            sha256(candidate["migration_envelope_sha256"], "repository envelope SHA256"),
            sha256(candidate["repository_plan_sha256"], "repository plan SHA256"),
            tuple(CheckoutRecord.from_dict(record) for record in checkouts),
            WritableTargetTree.from_dict(candidate["target_worktree"]),
            tuple(RepositoryCommandRecord.from_dict(command) for command in commands),
        )
