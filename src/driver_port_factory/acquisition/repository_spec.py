from __future__ import annotations

from dataclasses import asdict, dataclass

from ..core.models import WorkflowError
from .parsing import exact_object, nonempty, object_id
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    role: RepositoryRole
    platform: str
    url: str
    requested_ref: str
    resolved_commit: str
    selection_rule: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["role"] = self.role.value
        return value

    @classmethod
    def from_dict(cls, value: object) -> RepositorySpec:
        candidate = exact_object(
            value,
            required={
                "role",
                "platform",
                "url",
                "requested_ref",
                "resolved_commit",
                "selection_rule",
            },
            label="repository specification",
        )
        try:
            role = RepositoryRole(candidate["role"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("repository specification has an invalid role") from error
        return cls(
            role,
            nonempty(candidate["platform"], "repository platform"),
            nonempty(candidate["url"], "repository URL"),
            nonempty(candidate["requested_ref"], "repository requested ref"),
            object_id(candidate["resolved_commit"], "repository resolved commit"),
            nonempty(candidate["selection_rule"], "repository selection rule"),
        )
