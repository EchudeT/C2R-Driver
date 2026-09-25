from __future__ import annotations

from pathlib import Path

from ..core.models import WorkflowError, utc_now
from .commands import RepositoryCommandKind
from .git_execution import RepositoryGit
from .repository_checkout import CheckoutRecord
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
from .repository_storage import BareRepositoryStore


class BaselineRepositoryAcquirer:
    """Acquire one immutable, detached repository baseline."""

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
        self.store = BareRepositoryStore(
            self.project_root,
            self.control_root,
            git,
            caches=caches,
            local_repositories=local_repositories or {},
        )

    def acquire(self, spec: RepositorySpec, checkout_name: str) -> CheckoutRecord:
        bare = self.store.prepare(spec)
        checkouts = self.control_root / "worktrees"
        checkouts.mkdir(parents=True, exist_ok=True)
        self.store.fetch(bare, spec)
        fetched_commit = self.git.run(
            ["-C", str(bare), "rev-parse", "FETCH_HEAD^{commit}"],
            operation=RepositoryCommandKind.BASELINE_COMMIT,
            role=spec.role,
        ).stdout.lower()
        checkout = checkouts / f"{checkout_name}-{self.store.repository_name(spec)}-{fetched_commit}"
        if getattr(spec, "resolved_commit", fetched_commit) != fetched_commit:
            raise WorkflowError(
                f"fetched {spec.role.value} commit {fetched_commit} does not match "
                f"planned {spec.resolved_commit}"
            )
        tree_id = self.git.run(
            ["-C", str(bare), "rev-parse", f"{fetched_commit}^{{tree}}"],
            operation=RepositoryCommandKind.BASELINE_TREE,
            role=spec.role,
        ).stdout.lower()
        if checkout.exists():
            self._validate_existing(checkout, fetched_commit, spec.role)
        else:
            self.git.run(
                ["-C", str(bare), "worktree", "add", "--detach", str(checkout), fetched_commit],
                operation=RepositoryCommandKind.BASELINE_WORKTREE,
                role=spec.role,
            )
        self._require_clean(checkout, spec.role)
        lock_path, lock_sha256 = self.store.publish_lock(
            spec,
            resolved_commit=fetched_commit,
            tree_id=tree_id,
        )
        return CheckoutRecord(
            spec.role,
            spec.platform,
            spec.url,
            spec.requested_ref,
            fetched_commit,
            tree_id,
            str(bare.relative_to(self.project_root)),
            str(checkout.relative_to(self.project_root)),
            True,
            str(lock_path.relative_to(self.project_root)),
            lock_sha256,
            utc_now(),
        )

    def _validate_existing(
        self,
        checkout: Path,
        expected_commit: str,
        role: RepositoryRole,
    ) -> None:
        if not checkout.is_dir() or checkout.is_symlink():
            raise WorkflowError(f"checkout path is not a managed directory: {checkout}")
        observed = self.git.run(
            ["-C", str(checkout), "rev-parse", "HEAD^{commit}"],
            operation=RepositoryCommandKind.BASELINE_WORKTREE,
            role=role,
        ).stdout.lower()
        if observed != expected_commit:
            raise WorkflowError(f"existing checkout {checkout} drifted from the planned commit")

    def _require_clean(self, checkout: Path, role: RepositoryRole) -> None:
        status = self.git.run(
            [
                "-C",
                str(checkout),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            operation=RepositoryCommandKind.BASELINE_CLEANLINESS,
            role=role,
        ).stdout
        if status:
            raise WorkflowError(f"frozen {role.value} baseline is dirty: {checkout}")
