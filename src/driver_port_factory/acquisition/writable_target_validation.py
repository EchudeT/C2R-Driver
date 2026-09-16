from __future__ import annotations

import hashlib
from pathlib import Path

from ..core.models import WorkflowError
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .repository_checkout import CheckoutRecord, WritableTargetObservation
from .repository_filesystem import git_bytes, git_output, paths_overlap, workspace_directory
from .repository_manifest import RepositoryAcquisition


class WritableTargetValidator:
    def validate(
        self,
        root: Path,
        acquisition: RepositoryAcquisition,
        target_checkout: CheckoutRecord,
        commands: list[RepositoryCommandRecord],
    ) -> None:
        record = acquisition.target_worktree
        target = workspace_directory(root, record.path, "target worktree")
        self._validate_boundary(root, target, acquisition)
        if record.base_commit != target_checkout.resolved_commit:
            raise WorkflowError("writable target tree base commit differs from target baseline")
        observation = record.observation
        if observation.head_commit != record.base_commit or observation.branch != record.branch:
            raise WorkflowError("writable target observation differs from its declared baseline")
        if observation.work_tree != record.path:
            raise WorkflowError("writable target observation changed its work tree path")
        self._validate_command_proof(root, target, observation, commands)
        self._validate_current_identity(root, target, observation)

    @staticmethod
    def _validate_boundary(
        root: Path,
        target: Path,
        acquisition: RepositoryAcquisition,
    ) -> None:
        allowed_root = (root.resolve() / "work").resolve()
        if allowed_root not in target.parents:
            raise WorkflowError("writable target tree is outside the project work area")
        protected = [root.resolve(), (root / ".dpf").resolve()]
        for checkout in acquisition.checkouts:
            protected.extend(
                (
                    (root / checkout.checkout_path).resolve(),
                    (root / checkout.bare_repository).resolve(),
                    (root / checkout.lock_path).resolve(),
                )
            )
        if target == root.resolve() or any(paths_overlap(target, path) for path in protected[1:]):
            raise WorkflowError("writable target tree overlaps project control or frozen inputs")

    @staticmethod
    def _validate_command_proof(
        root: Path,
        target: Path,
        observation: WritableTargetObservation,
        commands: list[RepositoryCommandRecord],
    ) -> None:
        expected = {
            RepositoryCommandKind.TARGET_HEAD_OBSERVATION: (
                ("git", "-C", str(target), "rev-parse", "HEAD^{commit}"),
                observation.head_commit,
            ),
            RepositoryCommandKind.TARGET_BRANCH_OBSERVATION: (
                ("git", "-C", str(target), "symbolic-ref", "--short", "HEAD"),
                observation.branch,
            ),
            RepositoryCommandKind.TARGET_GIT_DIR_OBSERVATION: (
                ("git", "-C", str(target), "rev-parse", "--absolute-git-dir"),
                str((root / observation.git_dir).resolve()),
            ),
            RepositoryCommandKind.TARGET_WORK_TREE_OBSERVATION: (
                ("git", "-C", str(target), "rev-parse", "--show-toplevel"),
                str((root / observation.work_tree).resolve()),
            ),
        }
        for operation, (argv, stdout) in expected.items():
            if not any(
                command.operation is operation
                and command.result.argv == argv
                and _stdout(command) == stdout
                for command in commands
            ):
                raise WorkflowError(
                    f"writable target observation lacks exact {operation.value} proof"
                )
        status_argv = (
            "git",
            "-C",
            str(target),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        if not any(
            command.operation is RepositoryCommandKind.TARGET_STATUS_OBSERVATION
            and command.result.argv == status_argv
            and command.result.stdout_sha256 == observation.status_sha256
            and bool(_stdout(command)) is observation.dirty
            for command in commands
        ):
            raise WorkflowError("writable target status observation lacks exact command proof")

    @staticmethod
    def _validate_current_identity(
        root: Path,
        target: Path,
        observation: WritableTargetObservation,
    ) -> None:
        status = git_bytes(
            root,
            "-C",
            str(target),
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
        )
        current = (
            git_output(root, "-C", str(target), "rev-parse", "HEAD^{commit}"),
            git_output(root, "-C", str(target), "symbolic-ref", "--short", "HEAD"),
            str(
                Path(
                    git_output(root, "-C", str(target), "rev-parse", "--absolute-git-dir")
                ).resolve()
            ),
            str(
                Path(git_output(root, "-C", str(target), "rev-parse", "--show-toplevel")).resolve()
            ),
            hashlib.sha256(status).hexdigest(),
            bool(status),
        )
        expected = (
            observation.head_commit,
            observation.branch,
            str((root / observation.git_dir).resolve()),
            str((root / observation.work_tree).resolve()),
            observation.status_sha256,
            observation.dirty,
        )
        if current != expected:
            raise WorkflowError("target worktree current identity differs from its observation")


def _stdout(command: RepositoryCommandRecord) -> str:
    return Path(command.result.stdout_path).read_text(encoding="utf-8", errors="replace").strip()
