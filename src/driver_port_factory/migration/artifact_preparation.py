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
from ..environment.evidence import executable_identity, frozen_repository_snapshot, workspace_path
from ..environment.models import ArtifactMode
from .contracts import ContractExecutionStatus, MigrationArtifact, MigrationStage

ARTIFACT_INPUTS = (
    MigrationArtifact.HANDOFF,
    MigrationArtifact.IMPLEMENTATION_BUNDLE,
    MigrationArtifact.TRANSLATION_COVERAGE,
    MigrationArtifact.TARGET_CHANGE_INVENTORY,
    MigrationArtifact.COMPLIANCE_REPORT,
    EnvironmentArtifact.MODE_RECORD,
    EnvironmentArtifact.EXPERIMENT_ROUTE,
)


def _strings(value: Any, label: str) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(item, str) and item for item in value)
    ):
        raise WorkflowError(f"artifact plan {label} must be a non-empty string list")
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
            raise WorkflowError("artifact plan command must be an object")
        try:
            environment = value["environment"]
            timeout = value["timeout_seconds"]
            exit_codes = value["accepted_exit_codes"]
            if not isinstance(environment, dict) or not all(
                isinstance(key, str) and isinstance(item, str) for key, item in environment.items()
            ):
                raise TypeError
            if {"HOME", "CODEX_HOME"} & environment.keys():
                raise TypeError
            if (
                not isinstance(timeout, int)
                or isinstance(timeout, bool)
                or not 1 <= timeout <= 3600
            ):
                raise TypeError
            if (
                not isinstance(exit_codes, list)
                or not exit_codes
                or not all(
                    isinstance(code, int) and not isinstance(code, bool) for code in exit_codes
                )
            ):
                raise TypeError
            return cls(
                _strings(value["argv"], "argv"),
                dict(environment),
                timeout,
                tuple(exit_codes),
            )
        except (KeyError, TypeError) as error:
            raise WorkflowError("artifact plan command has an invalid typed boundary") from error

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
    build: PlannedCommand
    inspect: PlannedCommand
    base_artifact: str
    final_artifact: str
    packaged_test_artifact: str
    presence_manifest: str
    tool_evidence_paths: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: Any) -> ArtifactPreparationPlan:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise WorkflowError("artifact preparation plan must be a schema_version=1 object")
        try:
            plan = cls(
                str(value["plan_id"]),
                ArtifactMode(value["artifact_mode"]),
                str(value["cwd"]),
                PlannedCommand.from_dict(value["build"]),
                PlannedCommand.from_dict(value["inspect"]),
                str(value["base_artifact"]),
                str(value["final_artifact"]),
                str(value["packaged_test_artifact"]),
                str(value["presence_manifest"]),
                _strings(value["tool_evidence_paths"], "tool_evidence_paths"),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError(
                "artifact preparation plan has an invalid typed boundary"
            ) from error
        for label, item in (
            ("plan_id", plan.plan_id),
            ("cwd", plan.cwd),
            ("base_artifact", plan.base_artifact),
            ("final_artifact", plan.final_artifact),
            ("packaged_test_artifact", plan.packaged_test_artifact),
            ("presence_manifest", plan.presence_manifest),
        ):
            relative = PurePosixPath(item)
            if (
                not item
                or relative.is_absolute()
                or ".." in relative.parts
                or relative.as_posix() != item
            ):
                raise WorkflowError(f"artifact plan {label} must be a canonical relative path")
        return plan

    @classmethod
    def read(cls, path: Path) -> tuple[ArtifactPreparationPlan, bytes]:
        try:
            data = path.read_bytes()
            value = json.loads(data)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("artifact preparation plan is not UTF-8 JSON") from error
        return cls.from_dict(value), data

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "plan_id": self.plan_id,
            "artifact_mode": self.artifact_mode.value,
            "cwd": self.cwd,
            "build": self.build.to_dict(),
            "inspect": self.inspect.to_dict(),
            "base_artifact": self.base_artifact,
            "final_artifact": self.final_artifact,
            "packaged_test_artifact": self.packaged_test_artifact,
            "presence_manifest": self.presence_manifest,
            "tool_evidence_paths": list(self.tool_evidence_paths),
        }


class ArtifactPreparationService:
    def run(self, project: Project, plan_path: Path) -> dict[str, Any]:
        stage = project.stage(MigrationStage.ARTIFACT_PREPARATION)
        if stage.status is StageStatus.READY:
            project.start(MigrationStage.ARTIFACT_PREPARATION)
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError("artifact_preparation must be READY or RUNNING")
        controlled = workspace_path(project, str(plan_path.resolve().relative_to(project.root)))
        plan, plan_data = ArtifactPreparationPlan.read(controlled)
        plan_ref = project.record_artifact(
            MigrationStage.ARTIFACT_PREPARATION,
            FileArtifact(MigrationArtifact.ARTIFACT_PREPARATION_PLAN, controlled),
            direction=ArtifactDirection.INPUT,
        )
        attempt_dir = project.control / "artifact-preparation" / plan.plan_id
        attempt_dir.mkdir(parents=True, exist_ok=False)
        attempt_path = attempt_dir / "attempt.json"

        inputs = {kind.value: self._input(project, kind).to_dict() for kind in ARTIFACT_INPUTS}
        mode = project.load_json_artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        )
        if plan.artifact_mode.value != mode["artifact_mode"]:
            raise WorkflowError("artifact plan differs from the frozen artifact mode")

        acquisition = load_repository_acquisition(project)
        worktree = workspace_path(project, acquisition.target_worktree.path)
        cwd = workspace_path(project, plan.cwd)
        if cwd != worktree and worktree not in cwd.parents:
            raise WorkflowError("artifact commands must run inside the writable target worktree")
        paths = {
            name: workspace_path(project, value)
            for name, value in (
                ("base", plan.base_artifact),
                ("final", plan.final_artifact),
                ("packaged_test", plan.packaged_test_artifact),
                ("presence", plan.presence_manifest),
            )
        }
        if not paths["base"].is_file():
            raise WorkflowError("artifact plan base artifact does not exist")
        if any(paths[name].exists() for name in ("final", "packaged_test", "presence")):
            raise WorkflowError("artifact preparation outputs must use fresh paths")

        bundle = project.load_json_artifact(
            MigrationStage.DRIVER_IMPLEMENTATION,
            MigrationArtifact.IMPLEMENTATION_BUNDLE,
        )
        self._implementation_matches(worktree, bundle)
        repositories_before = frozen_repository_snapshot(project)
        base_before = self._identity(project, plan.base_artifact)
        tools_before = [self._identity(project, item) for item in plan.tool_evidence_paths]
        executable_locks = {
            "build": executable_identity(plan.build.argv[0], cwd),
            "inspect": executable_identity(plan.inspect.argv[0], cwd),
        }
        if any(lock["resolved"] is None for lock in executable_locks.values()):
            raise WorkflowError("artifact plan executable is unavailable")

        runner = CommandRunner(
            project.control / "command-runs" / "artifact-preparation" / plan.plan_id
        )
        build = self._execute(runner, plan.build, cwd, executable_locks["build"])
        inspection = None
        if self._command_passed(build, plan.build):
            inspection = self._execute(runner, plan.inspect, cwd, executable_locks["inspect"])

        identity, error = self._evaluate(
            project,
            plan,
            plan_data,
            inputs,
            bundle,
            worktree,
            base_before,
            build,
            inspection,
            executable_locks,
            tools_before,
            repositories_before,
        )

        attempt = {
            "schema_version": 1,
            "status": StageStatus.PASS.value if identity else StageStatus.FAIL.value,
            "plan_sha256": hashlib.sha256(plan_data).hexdigest(),
            "plan_artifact": plan_ref.to_dict(),
            "plan": plan.to_dict(),
            "inputs": inputs,
            "build": asdict(build),
            "inspection": asdict(inspection) if inspection else None,
            "error": error,
        }
        attempt_path.write_bytes(self._json(attempt))
        project.record_artifact(
            MigrationStage.ARTIFACT_PREPARATION,
            FileArtifact(MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT, attempt_path),
        )
        if identity is None:
            return {
                "status": StageStatus.RUNNING.value,
                "attempt": str(attempt_path),
                "error": error,
            }

        identity["attempt_sha256"] = hashlib.sha256(attempt_path.read_bytes()).hexdigest()
        project.finalize_stage(
            MigrationStage.ARTIFACT_PREPARATION,
            (
                GeneratedArtifact(
                    MigrationArtifact.RUNTIME_ARTIFACT,
                    paths["final"].read_bytes(),
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
            "artifact_sha256": identity["runtime_artifact"]["sha256"],
        }

    def _evaluate(
        self,
        project: Project,
        plan: ArtifactPreparationPlan,
        plan_data: bytes,
        inputs: dict[str, Any],
        bundle: dict[str, Any],
        worktree: Path,
        base: dict[str, Any],
        build: CommandResult,
        inspection: CommandResult | None,
        executables: dict[str, dict[str, Any]],
        tool_evidence: list[dict[str, Any]],
        repositories: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str | None]:
        try:
            if (
                not self._command_passed(build, plan.build)
                or inspection is None
                or not self._command_passed(inspection, plan.inspect)
            ):
                raise WorkflowError("artifact build or inspection command failed")
            if frozen_repository_snapshot(project) != repositories:
                raise WorkflowError("a frozen repository changed during artifact preparation")
            self._implementation_matches(worktree, bundle)
            if self._identity(project, plan.base_artifact) != base:
                raise WorkflowError("immutable base artifact changed during preparation")
            if [
                self._identity(project, item) for item in plan.tool_evidence_paths
            ] != tool_evidence:
                raise WorkflowError("build metadata changed during artifact preparation")
            identity = self._final_identity(
                project,
                plan,
                plan_data,
                inputs,
                bundle,
                base,
                build,
                inspection,
                executables,
                tool_evidence,
                repositories,
            )
            return identity, None
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, WorkflowError) as failure:
            return None, str(failure)

    @staticmethod
    def _execute(
        runner: CommandRunner,
        command: PlannedCommand,
        cwd: Path,
        executable_lock: dict[str, Any],
    ) -> CommandResult:
        return runner.run(
            [str(executable_lock["resolved"]), *command.argv[1:]],
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

    def _final_identity(
        self,
        project: Project,
        plan: ArtifactPreparationPlan,
        plan_data: bytes,
        inputs: dict[str, Any],
        bundle: dict[str, Any],
        base: dict[str, Any],
        build: CommandResult,
        inspection: CommandResult,
        executables: dict[str, dict[str, Any]],
        tool_evidence: list[dict[str, Any]],
        repositories: dict[str, Any],
    ) -> dict[str, Any]:
        final = self._identity(project, plan.final_artifact)
        packaged_test = self._identity(project, plan.packaged_test_artifact)
        presence = self._identity(project, plan.presence_manifest)
        if final["sha256"] == base["sha256"]:
            raise WorkflowError("final artifact is only the stale immutable base")
        manifest = json.loads(workspace_path(project, plan.presence_manifest).read_bytes())
        expected_payloads = [
            {"path": item["path"], "role": item["role"], "sha256": item["sha256"]}
            for item in bundle["files"]
        ]
        expected_bundle = inputs[MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"]
        if (
            manifest.get("schema_version") != 1
            or manifest.get("implementation_bundle_sha256") != expected_bundle
            or manifest.get("payloads") != expected_payloads
            or manifest.get("final_artifact_sha256") != final["sha256"]
            or manifest.get("packaged_test_sha256") != packaged_test["sha256"]
        ):
            raise WorkflowError("driver presence proof does not bind the current implementation")
        return {
            "schema_version": 1,
            "inputs": inputs,
            "plan": plan.to_dict(),
            "plan_sha256": hashlib.sha256(plan_data).hexdigest(),
            "artifact_mode": plan.artifact_mode.value,
            "base_artifact": base,
            "payloads": expected_payloads,
            "runtime_artifact": final,
            "packaged_test_artifact": packaged_test,
            "driver_presence": {**presence, "manifest": manifest},
            "commands": {"build": asdict(build), "inspect": asdict(inspection)},
            "executables": executables,
            "tool_evidence": tool_evidence,
            "frozen_repositories": repositories,
            "runtime_status": ContractExecutionStatus.NOT_RUN.value,
        }

    @staticmethod
    def _implementation_matches(worktree: Path, bundle: dict[str, Any]) -> None:
        declared = {str(item["path"]): item for item in bundle["files"]}
        changed = set(
            filter(
                None,
                ArtifactPreparationService._git(
                    worktree, "diff", "--name-only", "HEAD"
                ).splitlines(),
            )
        )
        changed.update(
            filter(
                None,
                ArtifactPreparationService._git(
                    worktree, "ls-files", "--others", "--exclude-standard"
                ).splitlines(),
            )
        )
        if changed != set(declared):
            raise WorkflowError("writable target worktree drifted from the frozen implementation")
        for relative, item in declared.items():
            data = (worktree / relative).read_bytes()
            if hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise WorkflowError("writable target implementation content drifted")

    @staticmethod
    def _identity(project: Project, relative: str) -> dict[str, Any]:
        path = workspace_path(project, relative)
        if not path.is_file():
            raise WorkflowError(f"artifact preparation file is missing: {relative}")
        data = path.read_bytes()
        return {"path": relative, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}

    @staticmethod
    def _input(project: Project, kind: ArtifactKey):
        dependencies = project.workflow.spec(MigrationStage.ARTIFACT_PREPARATION).dependencies
        matches = [
            reference
            for stage in dependencies
            for reference in project.artifact_refs(stage=stage)
            if reference.kind == kind.value
        ]
        if len(matches) != 1:
            raise WorkflowError(f"artifact preparation requires one {kind.value} input")
        return matches[0]

    @staticmethod
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

    @staticmethod
    def _json(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def validate_artifact_bundle(context: BundleValidationContext) -> None:
    _, runtime = context.one_current(MigrationArtifact.RUNTIME_ARTIFACT)
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
    implementation = json_object(
        context.one_dependency(MigrationArtifact.IMPLEMENTATION_BUNDLE)[1],
        MigrationArtifact.IMPLEMENTATION_BUNDLE.value,
    )
    compliance = json_object(
        context.one_dependency(MigrationArtifact.COMPLIANCE_REPORT)[1],
        MigrationArtifact.COMPLIANCE_REPORT.value,
    )
    mode = json_object(
        context.one_dependency(EnvironmentArtifact.MODE_RECORD)[1],
        EnvironmentArtifact.MODE_RECORD.value,
    )
    payloads = [
        {"path": item["path"], "role": item["role"], "sha256": item["sha256"]}
        for item in implementation["files"]
    ]
    final_hash = hashlib.sha256(runtime).hexdigest()
    presence = identity.get("driver_presence", {}).get("manifest", {})
    if (
        identity.get("inputs") != expected_inputs
        or compliance.get("status") != StageStatus.PASS.value
        or identity.get("artifact_mode") != mode.get("artifact_mode")
        or identity.get("runtime_status") != ContractExecutionStatus.NOT_RUN.value
        or identity.get("payloads") != payloads
        or identity.get("runtime_artifact", {}).get("sha256") != final_hash
        or identity.get("base_artifact", {}).get("sha256") == final_hash
        or presence.get("implementation_bundle_sha256")
        != expected_inputs[MigrationArtifact.IMPLEMENTATION_BUNDLE.value]["digest"]
        or presence.get("payloads") != payloads
        or presence.get("final_artifact_sha256") != final_hash
        or presence.get("packaged_test_sha256")
        != identity.get("packaged_test_artifact", {}).get("sha256")
        or identity.get("attempt_sha256") != attempt_ref.digest
        or attempt.get("status") != StageStatus.PASS.value
        or attempt.get("inputs") != expected_inputs
        or attempt.get("plan") != identity.get("plan")
        or attempt.get("plan_sha256") != identity.get("plan_sha256")
    ):
        raise WorkflowError("runtime artifact identity does not prove the current driver payload")
    recorded_files = (
        identity["base_artifact"],
        identity["runtime_artifact"],
        identity["packaged_test_artifact"],
        identity["driver_presence"],
        *identity["tool_evidence"],
    )
    if not all(_workspace_file_matches(context.project_root, item) for item in recorded_files):
        raise WorkflowError("artifact preparation identity file changed after execution")
    if not all(
        _command_matches(context.project_root, identity, name) for name in ("build", "inspect")
    ):
        raise WorkflowError("artifact preparation command identity is inconsistent")


def _workspace_file_matches(root: Path, identity: dict[str, Any]) -> bool:
    path = (root / str(identity.get("path", ""))).resolve()
    return (
        (path == root or root in path.parents)
        and path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == identity.get("sha256")
        and path.stat().st_size == identity.get("size")
    )


def _command_matches(root: Path, identity: dict[str, Any], name: str) -> bool:
    command = identity["commands"][name]
    planned = identity["plan"][name]
    executable = identity["executables"][name]
    executable_path = Path(str(executable["resolved"]))
    expected_argv = [str(executable_path), *planned["argv"][1:]]
    expected_cwd = str((root / identity["plan"]["cwd"]).resolve())
    streams = all(
        _captured_stream_matches(root, command, stream) for stream in ("stdout", "stderr")
    )
    return (
        executable_path.is_file()
        and hashlib.sha256(executable_path.read_bytes()).hexdigest() == executable["sha256"]
        and command["argv"] == expected_argv
        and command["cwd"] == expected_cwd
        and command["launched"] is True
        and command["launch_error"] is None
        and command["timed_out"] is False
        and command["exit_code"] in planned["accepted_exit_codes"]
        and streams
    )


def _captured_stream_matches(root: Path, command: dict[str, Any], stream: str) -> bool:
    path = Path(str(command[f"{stream}_path"])).resolve()
    return (
        root in path.parents
        and path.is_file()
        and hashlib.sha256(path.read_bytes()).hexdigest() == command[f"{stream}_sha256"]
    )
