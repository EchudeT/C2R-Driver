from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..acquisition.repository import load_repository_acquisition
from ..core.contracts import ArtifactKey
from ..core.execution import CommandResult, CommandRunner
from ..core.models import (
    ArtifactDirection,
    FileArtifact,
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..core.validation import BundleValidationContext, json_object
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..environment.evidence import executable_identity, file_identity, workspace_path
from ..environment.models import ArtifactMode
from .contracts import ContractExecutionStatus, MigrationArtifact, MigrationStage

ARTIFACT_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.IMPLEMENTATION_BUNDLE,
    MigrationArtifact.COMPLIANCE_REPORT,
    EnvironmentArtifact.MODE_RECORD,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
)


def _strings(value: Any, label: str, *, empty: bool = False) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or (not empty and not value)
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise WorkflowError(f"artifact plan {label} must be a string list")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class PlannedCommand:
    argv: tuple[str, ...]
    environment: dict[str, str]
    timeout_seconds: int
    accepted_exit_codes: tuple[int, ...]

    @classmethod
    def from_dict(cls, value: Any) -> PlannedCommand:
        if not isinstance(value, dict):
            raise WorkflowError("planned command must be an object")
        environment = value.get("environment", {})
        timeout = value.get("timeout_seconds", 300)
        exit_codes = value.get("accepted_exit_codes", [0])
        if not isinstance(environment, dict) or not all(
            isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
        ):
            raise WorkflowError("planned command environment must map strings to strings")
        if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1:
            raise WorkflowError("planned command timeout must be positive")
        if (
            not isinstance(exit_codes, list)
            or not exit_codes
            or not all(isinstance(code, int) and not isinstance(code, bool) for code in exit_codes)
        ):
            raise WorkflowError("planned command exit codes must be integers")
        return cls(
            _strings(value.get("argv"), "argv"),
            dict(environment),
            timeout,
            tuple(exit_codes),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": list(self.argv),
            "environment": self.environment,
            "timeout_seconds": self.timeout_seconds,
            "accepted_exit_codes": list(self.accepted_exit_codes),
        }


@dataclass(frozen=True, slots=True)
class ArtifactPreparationPlan:
    plan_id: str
    artifact_mode: ArtifactMode
    cwd: str
    steps: tuple[PlannedCommand, ...]
    runtime_artifact: str
    base_artifact: str | None
    presence_check: PlannedCommand
    tool_evidence_paths: tuple[str, ...]

    @classmethod
    def read(cls, path: Path) -> tuple[ArtifactPreparationPlan, bytes]:
        try:
            data = path.read_bytes()
            value = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("artifact preparation plan is not UTF-8 JSON") from error
        if not isinstance(value, dict) or value.get("schema_version") != 2:
            raise WorkflowError("artifact preparation plan must be schema_version=2")
        try:
            steps = value["steps"]
            if not isinstance(steps, list):
                raise TypeError
            plan = cls(
                str(value["plan_id"]),
                ArtifactMode(value["artifact_mode"]),
                str(value["cwd"]),
                tuple(PlannedCommand.from_dict(item) for item in steps),
                str(value["runtime_artifact"]),
                str(value["base_artifact"]) if value.get("base_artifact") is not None else None,
                PlannedCommand.from_dict(value["presence_check"]),
                _strings(value.get("tool_evidence_paths", []), "tool_evidence_paths", empty=True),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("artifact preparation plan has an invalid boundary") from error
        for item in filter(None, (plan.cwd, plan.runtime_artifact, plan.base_artifact)):
            relative = PurePosixPath(item)
            if relative.is_absolute() or ".." in relative.parts or relative.as_posix() != item:
                raise WorkflowError("artifact preparation paths must be canonical and relative")
        return plan, data

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "plan_id": self.plan_id,
            "artifact_mode": self.artifact_mode.value,
            "cwd": self.cwd,
            "steps": [item.to_dict() for item in self.steps],
            "runtime_artifact": self.runtime_artifact,
            "base_artifact": self.base_artifact,
            "presence_check": self.presence_check.to_dict(),
            "tool_evidence_paths": list(self.tool_evidence_paths),
        }


class ArtifactPreparationService:
    def run(self, project: Project, plan_path: Path) -> dict[str, Any]:
        stage = project.stage(MigrationStage.ARTIFACT_PREPARATION)
        if stage.status is StageStatus.READY:
            project.start(MigrationStage.ARTIFACT_PREPARATION)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError("artifact_preparation must be READY or RUNNING")
        plan, plan_data = ArtifactPreparationPlan.read(plan_path)
        plan_ref = project.record_artifact(
            MigrationStage.ARTIFACT_PREPARATION,
            FileArtifact(MigrationArtifact.ARTIFACT_PREPARATION_PLAN, plan_path),
            direction=ArtifactDirection.INPUT,
        )
        inputs = {kind.value: self._input(project, kind).to_dict() for kind in ARTIFACT_INPUTS}
        mode = project.load_json_artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        )
        if plan.artifact_mode.value != mode["artifact_mode"]:
            raise WorkflowError("artifact plan differs from the selected artifact mode")

        acquisition = load_repository_acquisition(project)
        worktree = workspace_path(project, acquisition.target_worktree.path)
        cwd = workspace_path(project, plan.cwd)
        if cwd != worktree and worktree not in cwd.parents:
            raise WorkflowError("artifact commands must run in the writable target worktree")
        implementation = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationArtifact.IMPLEMENTATION_BUNDLE,
        )
        self._implementation_matches(worktree, implementation)
        base = file_identity(project, plan.base_artifact) if plan.base_artifact else None
        tool_evidence = [file_identity(project, item) for item in plan.tool_evidence_paths]
        runner = CommandRunner(
            project.control / "command-runs" / "artifact-preparation" / plan.plan_id
        )
        results = [self._execute(runner, item, cwd) for item in plan.steps]
        presence = self._execute(runner, plan.presence_check, cwd)
        passed = all(
            self._command_passed(result, command)
            for result, command in zip(
                (*results, presence), (*plan.steps, plan.presence_check), strict=True
            )
        )
        runtime_path = workspace_path(project, plan.runtime_artifact)
        if passed and not runtime_path.is_file():
            passed = False

        attempt = {
            "schema_version": 2,
            "status": StageStatus.PASS.value if passed else StageStatus.FAIL.value,
            "plan": plan.to_dict(),
            "plan_sha256": hashlib.sha256(plan_data).hexdigest(),
            "plan_artifact": plan_ref.to_dict(),
            "inputs": inputs,
            "steps": [asdict(item) for item in results],
            "presence_check": asdict(presence),
        }
        attempt_dir = project.control / "artifact-preparation" / plan.plan_id
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(self._json(attempt))
        attempt_ref = project.record_artifact(
            MigrationStage.ARTIFACT_PREPARATION,
            FileArtifact(MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT, attempt_path),
        )
        if not passed:
            return {
                "status": StageStatus.RUNNING.value,
                "attempt": str(attempt_path),
                "error": "artifact preparation or presence check failed",
            }

        runtime_data = runtime_path.read_bytes()
        runtime_sha256 = hashlib.sha256(runtime_data).hexdigest()
        payloads = [
            {key: item[key] for key in ("path", "role", "sha256")}
            for item in implementation["files"]
        ]
        presence_result = asdict(presence)
        driver_presence = self._driver_presence(
            inputs[MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"],
            runtime_sha256,
            payloads,
            presence_result,
        )
        identity = {
            "schema_version": 2,
            "inputs": inputs,
            "artifact_mode": plan.artifact_mode.value,
            "runtime_artifact": {
                "path": plan.runtime_artifact,
                "sha256": runtime_sha256,
                "size": len(runtime_data),
            },
            "base_artifact": base,
            "implementation_payloads": payloads,
            "presence_check": presence_result,
            "driver_presence": driver_presence,
            "tool_evidence": tool_evidence,
            "attempt_sha256": attempt_ref.digest,
            "runtime_status": ContractExecutionStatus.NOT_RUN.value,
        }
        project.finalize_stage(
            MigrationStage.ARTIFACT_PREPARATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.RUNTIME_ARTIFACT,
                    runtime_data,
                    f"generated:artifact-preparation:{plan.plan_id}",
                ),
                GeneratedArtifact(
                    MigrationArtifact.ARTIFACT_IDENTITY,
                    self._json(identity),
                    f"generated:artifact-identity:{plan.plan_id}",
                ),
            ),
        )
        return {
            "status": StageStatus.PASS.value,
            "attempt": str(attempt_path),
            "artifact_sha256": runtime_sha256,
        }

    @staticmethod
    def _execute(runner: CommandRunner, command: PlannedCommand, cwd: Path) -> CommandResult:
        executable = executable_identity(command.argv[0], cwd)
        if executable["resolved"] is None:
            raise WorkflowError(f"artifact tool is unavailable: {command.argv[0]}")
        return runner.run(
            [str(executable["resolved"]), *command.argv[1:]],
            cwd=cwd,
            environment=command.environment,
            timeout_seconds=command.timeout_seconds,
        )

    @staticmethod
    def _command_passed(result: CommandResult, command: PlannedCommand) -> bool:
        return (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code in command.accepted_exit_codes
        )

    @staticmethod
    def _implementation_matches(worktree: Path, bundle: dict[str, Any]) -> None:
        declared = {str(item["path"]): item for item in bundle["files"]}
        changed = set(filter(None, _git(worktree, "diff", "--name-only", "HEAD").splitlines()))
        changed.update(
            filter(None, _git(worktree, "ls-files", "--others", "--exclude-standard").splitlines())
        )
        if changed != set(declared):
            raise WorkflowError("target worktree differs from the frozen implementation")
        for relative, item in declared.items():
            if hashlib.sha256((worktree / relative).read_bytes()).hexdigest() != item["sha256"]:
                raise WorkflowError("target implementation changed after compliance review")

    @staticmethod
    def _input(project: Project, kind: ArtifactKey):
        dependencies = project.workflow.spec(MigrationStage.ARTIFACT_PREPARATION).dependencies
        matches = [
            ref
            for stage in dependencies
            for ref in project.current_artifact_refs(stage=stage)
            if ref.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"artifact preparation requires one {kind.value} input")
        return matches[0]

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()

    @classmethod
    def _driver_presence(
        cls,
        implementation_sha256: str,
        runtime_sha256: str,
        payloads: list[dict[str, Any]],
        presence_check: dict[str, Any],
    ) -> dict[str, str]:
        return {
            "implementation_sha256": implementation_sha256,
            "runtime_artifact_sha256": runtime_sha256,
            "payloads_sha256": hashlib.sha256(cls._json({"files": payloads})).hexdigest(),
            "presence_check_sha256": hashlib.sha256(
                cls._json(presence_check)
            ).hexdigest(),
        }


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise WorkflowError(f"Git inspection failed: {result.stderr.strip()}")
    return result.stdout.strip()


def validate_artifact_bundle(context: BundleValidationContext) -> None:
    runtime_ref, runtime = context.one_current(MigrationArtifact.RUNTIME_ARTIFACT)
    attempt_ref, attempt_data = context.one_auxiliary(
        MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT
    )
    attempt = json_object(attempt_data, MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT.value)
    identity = json_object(
        context.one_current(MigrationArtifact.ARTIFACT_IDENTITY)[1],
        MigrationArtifact.ARTIFACT_IDENTITY.value,
    )
    expected_inputs = {
        kind.value: context.one_dependency(kind)[0].to_dict() for kind in ARTIFACT_INPUTS
    }
    expected_presence = ArtifactPreparationService._driver_presence(
        expected_inputs[MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"],
        runtime_ref.digest,
        identity.get("implementation_payloads", []),
        identity.get("presence_check", {}),
    )
    if (
        identity.get("schema_version") != 2
        or identity.get("inputs") != expected_inputs
        or identity.get("attempt_sha256") != attempt_ref.digest
        or identity.get("runtime_artifact", {}).get("sha256") != runtime_ref.digest
        or hashlib.sha256(runtime).hexdigest() != runtime_ref.digest
        or identity.get("runtime_status") != ContractExecutionStatus.NOT_RUN.value
        or attempt.get("status") != StageStatus.PASS.value
        or identity.get("presence_check") != attempt.get("presence_check")
        or identity.get("driver_presence") != expected_presence
    ):
        raise WorkflowError("runtime artifact identity is detached from its preparation run")
