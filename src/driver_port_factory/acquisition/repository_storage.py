from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .git_execution import RepositoryGit
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec


class BareRepositoryStore:
    """Publish verified bare repositories and immutable identity locks."""

    def __init__(
        self,
        project_root: Path,
        control_root: Path,
        git: RepositoryGit,
        *,
        caches=(),
        local_repositories=None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.control_root = control_root.resolve()
        self.git = git
        self.caches = tuple(Path(path).resolve() for path in caches)
        self.local_repositories = {
            role: Path(path).resolve()
            for role, path in (local_repositories or {}).items()
            if path
        }

    @staticmethod
    def repository_name(spec) -> str:
        return f"{spec.role.value}-{hashlib.sha256(spec.url.encode()).hexdigest()[:16]}"

    def path(self, spec) -> Path:
        return self.control_root / "git" / f"{self.repository_name(spec)}.git"

    def prepare(self, spec: RepositorySpec) -> Path:
        repositories = self.control_root / "git"
        repositories.mkdir(parents=True, exist_ok=True)
        bare = self.path(spec)
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

    def fetch(self, bare: Path, spec) -> None:
        """Keep a successful download and its receipt across controller restarts."""
        local = self.local_repositories.get(spec.role)
        if local is not None:
            if self._reuse_local_receipt(bare, spec, local):
                return
            if not self._seed_from_local(bare, spec, local):
                raise WorkflowError(
                    f"local {spec.role.value} repository does not contain "
                    f"requested revision {spec.requested_ref}: {local}"
                )
            return
        argv = ["-C", str(bare), "fetch", "--depth=1", "--no-tags", "origin", spec.requested_ref]
        receipt = bare / "dpf-fetch.json"
        if receipt.is_file():
            record = RepositoryCommandRecord.from_dict(json.loads(receipt.read_text()))
            if record.role is spec.role and record.result.argv == ("git", *argv):
                record.verify_evidence(self.project_root)
                observed = self.git.optional(["-C", str(bare), "rev-parse", "FETCH_HEAD^{commit}"],
                    operation=RepositoryCommandKind.BASELINE_COMMIT, role=spec.role)
                if observed is not None:
                    self._reuse_cache_receipt(bare)
                    self.git.reuse(record)
                    return
        self._seed_from_cache(bare, spec)
        result = self.git.run(argv, operation=RepositoryCommandKind.BASELINE_FETCH, role=spec.role)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(result.record.to_dict()) + "\n")
        temporary.replace(receipt)

    def _seed_from_local(self, bare: Path, spec, local: Path) -> bool:
        """Import a selected commit from a user-owned repository without copying its worktree.

        The managed bare repository receives only Git objects and FETCH_HEAD.  The
        subsequent detached worktree is always created by the normal acquisition
        path, so dirty files in the user's checkout can never enter the run.
        """
        if not local.is_dir() or local.is_symlink():
            raise WorkflowError(f"local {spec.role.value} repository is not a directory: {local}")
        commit = self.git.optional(
            ["-C", str(local), "rev-parse", f"{spec.requested_ref}^{{commit}}"],
            operation=RepositoryCommandKind.BASELINE_CACHE_IMPORT,
            role=spec.role,
        )
        if commit is None:
            return False
        resolved = commit.stdout.strip().lower()
        fetch_argv = [
            "-C",
            str(bare),
            "fetch",
            "--depth=1",
            "--no-tags",
            str(local),
            f"{resolved}:refs/dpf-cache/local",
        ]
        result = self.git.run(
            fetch_argv,
            operation=RepositoryCommandKind.BASELINE_FETCH,
            role=spec.role,
        )
        receipt = bare / "dpf-local-fetch.json"
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps([result.record.to_dict()]) + "\n")
        temporary.replace(receipt)
        return True

    def _reuse_local_receipt(self, bare: Path, spec, local: Path) -> bool:
        """Reuse a prior local import only when it still names this local commit."""
        if not local.is_dir() or local.is_symlink():
            raise WorkflowError(f"local {spec.role.value} repository is not a directory: {local}")
        commit = self.git.optional(
            ["-C", str(local), "rev-parse", f"{spec.requested_ref}^{{commit}}"],
            operation=RepositoryCommandKind.BASELINE_CACHE_IMPORT,
            role=spec.role,
        )
        if commit is None:
            return False
        resolved = commit.stdout.strip().lower()
        receipt = bare / "dpf-local-fetch.json"
        if not receipt.is_file():
            return False
        try:
            records = [
                RepositoryCommandRecord.from_dict(value)
                for value in json.loads(receipt.read_text())
            ]
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            TypeError,
            ValueError,
            WorkflowError,
        ):
            return False
        expected = (
            "git",
            "-C",
            str(bare),
            "fetch",
            "--depth=1",
            "--no-tags",
            str(local),
            f"{resolved}:refs/dpf-cache/local",
        )
        matching = [
            record
            for record in records
            if record.role is spec.role
            and record.operation is RepositoryCommandKind.BASELINE_FETCH
            and record.result.argv == expected
        ]
        if not matching:
            return False
        observed = self.git.optional(
            ["-C", str(bare), "rev-parse", "FETCH_HEAD^{commit}"],
            operation=RepositoryCommandKind.BASELINE_COMMIT,
            role=spec.role,
        )
        if observed is None or observed.stdout.lower() != resolved:
            return False
        for record in records:
            record.verify_evidence(self.project_root)
            self.git.reuse(record)
        return True

    def _seed_from_cache(self, bare, spec):
        """Import committed objects only; origin fetch still pins the public revision.

        No alternates/shared object dependency and no copying dirty worktree files.
        The normal fetch negotiates using these objects instead of downloading them.
        """
        if self._reuse_cache_receipt(bare):
            return
        first_record = len(self.git.records)
        for cache in self.caches:
            if not cache.is_dir() or cache == bare:
                continue
            origin = self.git.optional(["-C", str(cache), "remote", "get-url", "origin"],
                operation=RepositoryCommandKind.ORIGIN_VERIFICATION, role=spec.role)
            if origin is None or origin.stdout != spec.url:
                continue
            commit = self.git.optional(
                ["-C", str(cache), "rev-parse", f"{spec.requested_ref}^{{commit}}"],
                operation=RepositoryCommandKind.BASELINE_CACHE_IMPORT,
                role=spec.role,
            )
            if commit is None:
                continue
            imported = self.git.optional(
                ["-C", str(bare), "fetch", "--depth=1", "--no-tags", str(cache),
                 f"{commit.stdout}:refs/dpf-cache/seed"],
                operation=RepositoryCommandKind.BASELINE_CACHE_IMPORT, role=spec.role)
            if imported is not None:
                receipt = bare / "dpf-cache.json"
                temporary = receipt.with_suffix(".tmp")
                temporary.write_text(json.dumps([
                    record.to_dict() for record in self.git.records[first_record:]
                ]) + "\n")
                temporary.replace(receipt)
                return

    def _reuse_cache_receipt(self, bare):
        receipt = bare / "dpf-cache.json"
        if not receipt.is_file():
            return False
        for value in json.loads(receipt.read_text()):
            self.git.reuse(RepositoryCommandRecord.from_dict(value))
        return True

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
        path = self.control_root / "manifests" / "repository-locks" / f"{digest}.json"
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
