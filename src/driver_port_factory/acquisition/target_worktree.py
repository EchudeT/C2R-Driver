from __future__ import annotations

import re
from pathlib import Path

from ..core.models import WorkflowError
from .commands import RepositoryCommandKind
from .git_execution import RepositoryGit
from .repository_checkout import WritableTargetObservation, WritableTargetTree
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec


class TargetWorktreeManager:
    """Create or resume the sole writable worktree from the frozen target baseline."""

    def __init__(self, project_root: Path, control_root: Path, git: RepositoryGit) -> None:
        self.project_root = project_root.resolve()
        self.control_root = control_root.resolve()
        self.git = git

    def create(self, spec: RepositorySpec, project_id: str) -> WritableTargetTree:
        if spec.role is not RepositoryRole.TARGET:
            raise WorkflowError("a writable worktree can only be created for the target repository")
        bare = self.control_root / "git" / f"{spec.role.value}.git"
        target = self.project_root / "work" / "target-working"
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "-", project_id).strip("-") or "run"
        branch = f"dpf/{safe_id}"
        if not target.exists():
            branch_commit = self.git.optional(
                ["-C", str(bare), "rev-parse", f"refs/heads/{branch}^{{commit}}"],
                operation=RepositoryCommandKind.TARGET_BRANCH_LOOKUP,
                role=spec.role,
            )
            if branch_commit and branch_commit.stdout.lower() != spec.resolved_commit:
                raise WorkflowError(
                    f"target worktree branch {branch} drifted from the planned commit"
                )
            arguments = ["-C", str(bare), "worktree", "add"]
            if branch_commit:
                arguments.extend([str(target), branch])
            else:
                arguments.extend(["-b", branch, str(target), spec.resolved_commit])
            self.git.run(
                arguments,
                operation=RepositoryCommandKind.TARGET_WORKTREE_CREATION,
                role=spec.role,
            )
        observation = self._observe(target)
        if observation.head_commit != spec.resolved_commit or observation.branch != branch:
            raise WorkflowError("writable target worktree identity drifted from its baseline")
        return WritableTargetTree(
            str(target.relative_to(self.project_root)),
            spec.resolved_commit,
            branch,
            observation,
        )

    def _observe(self, target: Path) -> WritableTargetObservation:
        if not target.is_dir() or target.is_symlink():
            raise WorkflowError(f"target worktree path is not a managed directory: {target}")
        head = self.git.run(
            ["-C", str(target), "rev-parse", "HEAD^{commit}"],
            operation=RepositoryCommandKind.TARGET_HEAD_OBSERVATION,
            role=RepositoryRole.TARGET,
        ).stdout.lower()
        branch = self.git.run(
            ["-C", str(target), "symbolic-ref", "--short", "HEAD"],
            operation=RepositoryCommandKind.TARGET_BRANCH_OBSERVATION,
            role=RepositoryRole.TARGET,
        ).stdout
        git_dir = self.git.run(
            ["-C", str(target), "rev-parse", "--absolute-git-dir"],
            operation=RepositoryCommandKind.TARGET_GIT_DIR_OBSERVATION,
            role=RepositoryRole.TARGET,
        ).stdout
        work_tree = self.git.run(
            ["-C", str(target), "rev-parse", "--show-toplevel"],
            operation=RepositoryCommandKind.TARGET_WORK_TREE_OBSERVATION,
            role=RepositoryRole.TARGET,
        ).stdout
        status = self.git.run(
            ["-C", str(target), "status", "--porcelain=v1", "--untracked-files=all"],
            operation=RepositoryCommandKind.TARGET_STATUS_OBSERVATION,
            role=RepositoryRole.TARGET,
        )
        return WritableTargetObservation(
            head,
            branch,
            str(Path(git_dir).resolve().relative_to(self.project_root)),
            str(Path(work_tree).resolve().relative_to(self.project_root)),
            status.record.result.stdout_sha256,
            bool(status.stdout),
        )
