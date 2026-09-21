from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class RepositoryGitResult:
    stdout: str
    record: RepositoryCommandRecord


class RepositoryFetchError(WorkflowError):
    """Acquisition can resume the same selection without a paid model decision."""


class RepositorySelectionError(WorkflowError):
    """A reachable remote rejected the selection; worker diagnosis is appropriate."""


class RepositoryGit:
    """Execute Git commands and retain successful, typed command evidence."""

    def __init__(self, project_root: Path, control_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.runner = CommandRunner(control_root.resolve() / "command-runs" / "acquisition")
        self._records: list[RepositoryCommandRecord] = []

    def run(
        self,
        arguments: list[str],
        *,
        operation: RepositoryCommandKind,
        role: RepositoryRole,
        cwd: Path | None = None,
    ) -> RepositoryGitResult:
        result = self.runner.run(
            ["git", *arguments],
            cwd=cwd or self.project_root,
            timeout_seconds=(
                1800 if operation in {
                    RepositoryCommandKind.BASELINE_FETCH,
                } else 600
            ),
        )
        stdout = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")
        if result.exit_code != 0:
            stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
            error_type = RepositoryFetchError if operation is RepositoryCommandKind.BASELINE_FETCH else WorkflowError
            if (operation is RepositoryCommandKind.BASELINE_FETCH
                    and not result.timed_out and result.launched
                    and arguments[:1] == ["-C"]):
                # Only diagnose after a failed download. Git's exit 2 proves a
                # reachable remote has no matching ref; other exits say nothing
                # about validity and remain resumable transport failures.
                full_commit = bool(re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", arguments[-1]))
                probe = self.runner.run(
                    ["git", "-C", arguments[1], "ls-remote", "--exit-code",
                     "origin", *([] if full_commit else [arguments[-1]])],
                    cwd=cwd or self.project_root, timeout_seconds=60,
                )
                if (probe.exit_code == 2 or full_commit and probe.exit_code == 0) and not probe.timed_out:
                    error_type = RepositorySelectionError
            raise error_type(
                f"git command failed ({result.exit_code}): git {' '.join(arguments)}: "
                f"{stderr.strip()}"
            )
        record = RepositoryCommandRecord.capture(operation, role, result)
        self._records.append(record)
        return RepositoryGitResult(stdout.strip(), record)

    def optional(
        self,
        arguments: list[str],
        *,
        operation: RepositoryCommandKind,
        role: RepositoryRole,
    ) -> RepositoryGitResult | None:
        result = self.runner.run(["git", *arguments], cwd=self.project_root, timeout_seconds=600)
        if result.exit_code != 0:
            return None
        record = RepositoryCommandRecord.capture(operation, role, result)
        self._records.append(record)
        stdout = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace").strip()
        return RepositoryGitResult(stdout, record)

    @property
    def records(self) -> tuple[RepositoryCommandRecord, ...]:
        return tuple(self._records)

    def reuse(self, record: RepositoryCommandRecord) -> None:
        record.verify_evidence(self.project_root)
        self._records.append(record)
