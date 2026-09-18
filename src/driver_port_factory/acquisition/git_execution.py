from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .repository_role import RepositoryRole


@dataclass(frozen=True, slots=True)
class RepositoryGitResult:
    stdout: str
    record: RepositoryCommandRecord


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
                    RepositoryCommandKind.COMMIT_MEMBERSHIP,
                } else 600
            ),
        )
        stdout = Path(result.stdout_path).read_text(encoding="utf-8", errors="replace")
        if result.exit_code != 0:
            stderr = Path(result.stderr_path).read_text(encoding="utf-8", errors="replace")
            raise WorkflowError(
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
