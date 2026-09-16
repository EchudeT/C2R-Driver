from __future__ import annotations

from dataclasses import asdict, dataclass

from ..core.models import WorkflowError
from .parsing import exact_object, nonempty, object_id, relative_path, sha256
from .repository_role import RepositoryRole


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
    lock_path: str
    lock_sha256: str
    acquired_at: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["role"] = self.role.value
        return value

    @classmethod
    def from_dict(cls, value: object) -> CheckoutRecord:
        candidate = exact_object(
            value,
            required={
                "role",
                "platform",
                "source_url",
                "requested_ref",
                "resolved_commit",
                "tree_id",
                "bare_repository",
                "checkout_path",
                "clean",
                "lock_path",
                "lock_sha256",
                "acquired_at",
            },
            label="repository checkout",
        )
        try:
            role = RepositoryRole(candidate["role"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("repository checkout has an invalid role") from error
        clean = candidate["clean"]
        if not isinstance(clean, bool):
            raise WorkflowError("repository checkout clean flag must be boolean")
        return cls(
            role,
            nonempty(candidate["platform"], "checkout platform"),
            nonempty(candidate["source_url"], "checkout source URL"),
            nonempty(candidate["requested_ref"], "checkout requested ref"),
            object_id(candidate["resolved_commit"], "checkout resolved commit"),
            object_id(candidate["tree_id"], "checkout tree ID"),
            relative_path(candidate["bare_repository"], "checkout bare repository path"),
            relative_path(candidate["checkout_path"], "checkout path"),
            clean,
            relative_path(candidate["lock_path"], "checkout lock path"),
            sha256(candidate["lock_sha256"], "checkout lock SHA256"),
            nonempty(candidate["acquired_at"], "checkout acquisition timestamp"),
        )


@dataclass(frozen=True, slots=True)
class WritableTargetObservation:
    head_commit: str
    branch: str
    git_dir: str
    work_tree: str
    status_sha256: str
    dirty: bool

    @classmethod
    def from_dict(cls, value: object) -> WritableTargetObservation:
        candidate = exact_object(
            value,
            required={
                "head_commit",
                "branch",
                "git_dir",
                "work_tree",
                "status_sha256",
                "dirty",
            },
            label="writable target observation",
        )
        dirty = candidate["dirty"]
        if not isinstance(dirty, bool):
            raise WorkflowError("writable target dirty observation must be boolean")
        return cls(
            object_id(candidate["head_commit"], "writable target observed HEAD"),
            nonempty(candidate["branch"], "writable target observed branch"),
            relative_path(candidate["git_dir"], "writable target Git directory"),
            relative_path(candidate["work_tree"], "writable target work tree"),
            sha256(candidate["status_sha256"], "writable target status SHA256"),
            dirty,
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class WritableTargetTree:
    path: str
    base_commit: str
    branch: str
    observation: WritableTargetObservation

    @classmethod
    def from_dict(cls, value: object) -> WritableTargetTree:
        candidate = exact_object(
            value,
            required={"path", "base_commit", "branch", "observation"},
            label="writable target tree",
        )
        return cls(
            relative_path(candidate["path"], "writable target path"),
            object_id(candidate["base_commit"], "writable target base commit"),
            nonempty(candidate["branch"], "writable target branch"),
            WritableTargetObservation.from_dict(candidate["observation"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "base_commit": self.base_commit,
            "branch": self.branch,
            "observation": self.observation.to_dict(),
        }
