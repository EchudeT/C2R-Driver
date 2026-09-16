from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from .commands import RepositoryCommandKind
from .git_execution import RepositoryGit
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec


class BareRepositoryStore:
    """Publish verified bare repositories and immutable identity locks."""

    def __init__(self, project_root: Path, control_root: Path, git: RepositoryGit) -> None:
        self.project_root = project_root.resolve()
        self.control_root = control_root.resolve()
        self.git = git

    def prepare(self, spec: RepositorySpec) -> Path:
        repositories = self.control_root / "git"
        repositories.mkdir(parents=True, exist_ok=True)
        bare = repositories / f"{spec.role.value}.git"
        if bare.is_symlink():
            raise WorkflowError(f"managed repository path cannot be a symlink: {bare}")
        if bare.exists() and not self._valid(bare, spec):
            self._quarantine(bare, spec.role)
        if bare.exists():
            return bare
        attempts = self.control_root / "git-attempts"
        attempts.mkdir(parents=True, exist_ok=True)
        attempt = attempts / f"{spec.role.value}-{uuid.uuid4().hex}.git"
        self.git.run(
            ["init", "--bare", str(attempt)],
            operation=RepositoryCommandKind.BASELINE_INITIALIZATION,
            role=spec.role,
        )
        self.git.run(
            ["-C", str(attempt), "remote", "add", "origin", spec.url],
            operation=RepositoryCommandKind.ORIGIN_CONFIGURATION,
            role=spec.role,
        )
        if bare.exists():
            raise WorkflowError(f"managed repository path appeared during publish: {bare}")
        attempt.rename(bare)
        return bare

    def publish_lock(
        self,
        spec: RepositorySpec,
        *,
        resolved_commit: str,
        tree_id: str,
    ) -> tuple[Path, str]:
        lock = {
            "role": spec.role.value,
            "platform": spec.platform,
            "source_url": spec.url,
            "resolved_commit": resolved_commit,
            "tree_id": tree_id,
        }
        data = canonical_json(lock).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        path = self.control_root / "manifests" / "repository-locks" / f"{spec.role.value}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_bytes() != data:
                raise WorkflowError(f"repository lock changed during retry: {spec.role.value}")
            return path, digest
        temporary = path.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_bytes(data)
        os.replace(temporary, path)
        return path, digest

    def _valid(self, bare: Path, spec: RepositorySpec) -> bool:
        if not bare.is_dir():
            return False
        is_bare = self.git.optional(
            ["-C", str(bare), "rev-parse", "--is-bare-repository"],
            operation=RepositoryCommandKind.ORIGIN_VERIFICATION,
            role=spec.role,
        )
        if is_bare is None or is_bare.stdout != "true":
            return False
        origin = self.git.optional(
            ["-C", str(bare), "remote", "get-url", "origin"],
            operation=RepositoryCommandKind.ORIGIN_VERIFICATION,
            role=spec.role,
        )
        return origin is not None and origin.stdout == spec.url

    def _quarantine(self, path: Path, role: RepositoryRole) -> None:
        if path.parent.resolve() != (self.control_root / "git").resolve():
            raise WorkflowError("refusing to quarantine an unmanaged repository path")
        quarantine = self.control_root / "quarantine" / "repositories"
        quarantine.mkdir(parents=True, exist_ok=True)
        path.rename(quarantine / f"{role.value}-{uuid.uuid4().hex}.git")
