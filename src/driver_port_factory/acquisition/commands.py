from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

from ..core.execution import CommandResult
from ..core.models import WorkflowError
from .parsing import exact_object, nonempty, sha256, string_tuple
from .repository_role import RepositoryRole


class RepositoryCommandKind(StrEnum):
    BASELINE_CACHE_IMPORT = "baseline_cache_import"
    BASELINE_INITIALIZATION = "baseline_initialization"
    ORIGIN_CONFIGURATION = "origin_configuration"
    BASELINE_FETCH = "baseline_fetch"
    BASELINE_COMMIT = "baseline_commit"
    BASELINE_TREE = "baseline_tree"
    BASELINE_WORKTREE = "baseline_worktree"
    BASELINE_CLEANLINESS = "baseline_cleanliness"
    ORIGIN_VERIFICATION = "origin_verification"
    TARGET_BRANCH_LOOKUP = "target_branch_lookup"
    TARGET_WORKTREE_CREATION = "target_worktree_creation"
    TARGET_HEAD_OBSERVATION = "target_head_observation"
    TARGET_BRANCH_OBSERVATION = "target_branch_observation"
    TARGET_GIT_DIR_OBSERVATION = "target_git_dir_observation"
    TARGET_WORK_TREE_OBSERVATION = "target_work_tree_observation"
    TARGET_STATUS_OBSERVATION = "target_status_observation"


@dataclass(frozen=True, slots=True)
class RepositoryCommandRecord:
    operation: RepositoryCommandKind
    role: RepositoryRole
    result: CommandResult

    @classmethod
    def capture(
        cls,
        operation: RepositoryCommandKind,
        role: RepositoryRole,
        result: CommandResult,
    ) -> RepositoryCommandRecord:
        return cls(operation, role, result)

    @classmethod
    def from_dict(cls, value: object) -> RepositoryCommandRecord:
        candidate = exact_object(
            value,
            required={"operation", "role", "result"},
            label="repository command record",
        )
        try:
            operation = RepositoryCommandKind(candidate["operation"])
            role = RepositoryRole(candidate["role"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("repository command record has an invalid identity") from error
        return cls(operation, role, _command_result(candidate["result"]))

    def to_dict(self) -> dict[str, object]:
        return {
            "operation": self.operation.value,
            "role": self.role.value,
            "result": {**asdict(self.result), "argv": list(self.result.argv)},
        }

    def verify_evidence(self, project_root: Path) -> None:
        result = self.result
        if (
            not result.launched
            or result.timed_out
            or result.exit_code != 0
            or result.launch_error is not None
            or not result.argv
            or result.argv[0] != "git"
        ):
            raise WorkflowError("repository command record is not a successful Git operation")
        root = project_root.resolve()
        runs_root = (root / ".dpf" / "command-runs").resolve()
        for path_value, expected, label in (
            (result.stdout_path, result.stdout_sha256, "stdout"),
            (result.stderr_path, result.stderr_sha256, "stderr"),
        ):
            path = Path(path_value).resolve()
            if runs_root not in path.parents or not path.is_file():
                raise WorkflowError(f"repository command {label} evidence is outside control state")
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise WorkflowError(f"repository command {label} evidence digest drifted")


def _command_result(value: object) -> CommandResult:
    candidate = exact_object(
        value,
        required={
            "argv",
            "cwd",
            "started_at",
            "completed_at",
            "exit_code",
            "launched",
            "launch_error",
            "timed_out",
            "duration_milliseconds",
            "stdout_sha256",
            "stderr_sha256",
            "stdout_path",
            "stderr_path",
        },
        label="command result",
    )
    exit_code = candidate["exit_code"]
    launched = candidate["launched"]
    timed_out = candidate["timed_out"]
    duration = candidate["duration_milliseconds"]
    launch_error = candidate["launch_error"]
    if not isinstance(exit_code, int):
        raise WorkflowError("command result exit_code must be an integer")
    if not isinstance(launched, bool) or not isinstance(timed_out, bool):
        raise WorkflowError("command result launch fields must be boolean")
    if not isinstance(duration, int) or duration < 0:
        raise WorkflowError("command result duration must be non-negative")
    if launch_error is not None and not isinstance(launch_error, str):
        raise WorkflowError("command result launch_error must be a string or null")
    return CommandResult(
        string_tuple(candidate["argv"], "command argv", allow_empty=False),
        nonempty(candidate["cwd"], "command cwd"),
        nonempty(candidate["started_at"], "command started_at"),
        nonempty(candidate["completed_at"], "command completed_at"),
        exit_code,
        launched,
        launch_error,
        timed_out,
        duration,
        sha256(candidate["stdout_sha256"], "command stdout SHA256"),
        sha256(candidate["stderr_sha256"], "command stderr SHA256"),
        nonempty(candidate["stdout_path"], "command stdout path"),
        nonempty(candidate["stderr_path"], "command stderr path"),
    )
