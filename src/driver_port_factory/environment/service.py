from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.execution import CommandRunner
from ..core.models import ActorRole, StageStatus, WorkflowError, utc_now
from ..core.project import Project
from .models import ArtifactMode, ExperimentPlan, ExperimentReadiness, RouteKind


@dataclass(frozen=True, slots=True)
class EnvironmentRunResult:
    route_id: str
    readiness: ExperimentReadiness
    stage_status: StageStatus
    attempt_path: str
    message: str


class EnvironmentService:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)
    TOOLS = (
        "qemu-system-x86_64",
        "qemu-system-aarch64",
        "qemu-system-riscv64",
        "qemu-storage-daemon",
        "docker",
        "podman",
        "cargo",
        "rustc",
        "clang",
        "cmake",
        "make",
        "ninja",
        "git",
    )

    def inspect(self, project: Project) -> dict[str, Any]:
        project.ensure_role(*self.ROLES)
        stage = project.store.stage("environment_recovery")
        if stage.status is StageStatus.READY:
            project.start("environment_recovery")
        elif stage.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"environment_recovery must be READY or RUNNING, got {stage.status.value}"
            )
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        target = self._checkout(checkouts, RepositoryRole.TARGET)
        qemu = self._checkout(checkouts, RepositoryRole.QEMU)
        target_root = self._workspace_path(project, target.checkout_path)
        qemu_root = self._workspace_path(project, qemu.checkout_path)
        metadata_paths = self._operational_metadata(target_root)
        tools = [
            {"name": name, "path": shutil.which(name), "available": shutil.which(name) is not None}
            for name in self.TOOLS
        ]
        disk = shutil.disk_usage(project.root)
        inventory = {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "host": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": sys.version.split()[0],
                "filesystem_device": os.stat(project.root).st_dev,
            },
            "disk": {
                "total_bytes": disk.total,
                "used_bytes": disk.used,
                "free_bytes": disk.free,
            },
            "tools": tools,
            "frozen_repositories": {
                "target": {
                    "path": target.checkout_path,
                    "revision": target.resolved_commit,
                    "tree_id": target.tree_id,
                },
                "qemu": {
                    "path": qemu.checkout_path,
                    "revision": qemu.resolved_commit,
                    "tree_id": qemu.tree_id,
                },
            },
            "existing_operational_metadata": metadata_paths,
        }
        candidates = {
            "schema_version": 1,
            "recorded_at": utc_now(),
            "selection_rule": (
                "choose the least expensive reproducible route that can contain the migrated "
                "driver; discovery is evidence, not proof that a route works"
            ),
            "candidates": self._artifact_candidates(metadata_paths, tools, qemu_root, project),
        }
        self._add_json(project, "environment_inventory", inventory)
        self._add_json(project, "artifact_mode_candidates", candidates)
        return {"inventory": inventory, "artifact_mode_candidates": candidates}

    def register_plan(self, project: Project, path: Path) -> ExperimentPlan:
        project.ensure_role(*self.ROLES)
        if project.store.stage("environment_recovery").status is not StageStatus.RUNNING:
            raise WorkflowError("inspect the environment before registering a route plan")
        value = json.loads(path.resolve().read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise WorkflowError("experiment plan must be a JSON object")
        plan = ExperimentPlan.from_dict(value)
        self._validate_plan_paths(project, plan)
        plan_path = self._plan_path(project, plan.route_id)
        if plan_path.exists():
            existing = plan_path.read_bytes()
            proposed = self._json_bytes(plan.to_dict())
            if existing != proposed:
                raise WorkflowError(f"route_id {plan.route_id} is immutable; choose a new route ID")
        else:
            plan_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.write_bytes(self._json_bytes(plan.to_dict()))
        project.add_artifact("environment_recovery", "environment_experiment_plan", plan_path)
        return plan

    def run(self, project: Project, route_id: str) -> EnvironmentRunResult:
        project.ensure_role(*self.ROLES)
        if project.store.stage("environment_recovery").status is not StageStatus.RUNNING:
            raise WorkflowError("environment_recovery is not RUNNING")
        plan_path = self._plan_path(project, route_id)
        if not plan_path.is_file():
            raise WorkflowError(f"unknown environment route: {route_id}")
        value = json.loads(plan_path.read_text(encoding="utf-8"))
        plan = ExperimentPlan.from_dict(value)
        self._validate_plan_paths(project, plan)
        cwd = self._workspace_path(project, plan.cwd)
        attempts = project.control / "environment" / "attempts"
        attempt_path = attempts / f"{plan.route_id}.json"
        if attempt_path.exists():
            raise WorkflowError(
                f"route {plan.route_id} already ran; retries require a new route ID"
            )
        runner = CommandRunner(project.control / "command-runs" / "environment")
        result = runner.run(
            plan.command,
            cwd=cwd,
            environment=plan.environment,
            timeout_seconds=plan.timeout_seconds,
        )
        stdout = Path(result.stdout_path).read_bytes()
        stderr = Path(result.stderr_path).read_bytes()
        combined = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
        markers = {marker: marker in combined for marker in plan.expected_markers}
        exit_accepted = result.exit_code in plan.accepted_exit_codes or (
            result.timed_out and plan.accept_timeout
        )
        ready = result.launched and exit_accepted and all(markers.values())
        readiness = ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL
        evidence = [self._file_identity(project, item) for item in plan.runner_evidence_paths]
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        attempt = {
            "schema_version": 1,
            "route": plan.to_dict(),
            "run": asdict(result),
            "runner_evidence": evidence,
            "command_executable": self._executable_identity(plan.command[0], cwd),
            "frozen_repository_locks": acquisition["checkouts"],
            "marker_observations": markers,
            "exit_accepted": exit_accepted,
            "readiness": readiness.value,
            "recorded_at": utc_now(),
        }
        attempts.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(self._json_bytes(attempt))
        project.add_artifact("environment_recovery", "environment_recovery_attempt", attempt_path)
        if not ready:
            return EnvironmentRunResult(
                route_id=plan.route_id,
                readiness=readiness,
                stage_status=StageStatus.RUNNING,
                attempt_path=str(attempt_path),
                message="route did not satisfy its frozen marker/exit oracle; plan a distinct retry",
            )
        artifact_mode = {
            "schema_version": 1,
            "artifact_mode": plan.artifact_mode.value,
            "route_kind": plan.route_kind.value,
            "selected_route_id": plan.route_id,
            "compiled_reused_injected_or_prebuilt": (
                "environment smoke only; migrated-driver payload identity is established later"
            ),
            "driver_insertion_or_packaging_path": plan.driver_insertion_or_packaging_path,
            "runner_evidence": evidence,
        }
        route = {
            "schema_version": 1,
            "milestone": "EXPERIMENT_READY",
            "route_id": plan.route_id,
            "device_identity": plan.device_identity,
            "topology": plan.topology,
            "artifact_mode": plan.artifact_mode.value,
            "route_kind": plan.route_kind.value,
            "command": list(plan.command),
            "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
            "migrated_driver_runtime_ready": False,
        }
        self._add_json(project, "artifact_mode_record", artifact_mode)
        project.add_artifact("environment_recovery", "experiment_ready_run", attempt_path)
        self._add_json(project, "experiment_route", route)
        project.complete("environment_recovery", StageStatus.PASS)
        return EnvironmentRunResult(
            route_id=plan.route_id,
            readiness=readiness,
            stage_status=StageStatus.PASS,
            attempt_path=str(attempt_path),
            message="EXPERIMENT_READY observed; migrated-driver runtime remains unproven",
        )

    @staticmethod
    def _checkout(checkouts: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
        matches = [record for record in checkouts if record.role is role]
        if len(matches) != 1:
            raise WorkflowError(f"expected exactly one {role.value} checkout")
        return matches[0]

    @staticmethod
    def _workspace_path(project: Project, relative: str) -> Path:
        path = (project.root / relative).resolve()
        if path != project.root and project.root not in path.parents:
            raise WorkflowError(f"path escapes project workspace: {relative}")
        return path

    @staticmethod
    def _operational_metadata(target_root: Path) -> list[str]:
        markers = (
            "makefile",
            "cargo.toml",
            "justfile",
            "taskfile",
            "dockerfile",
            "devcontainer",
            "workflow",
            "gitlab-ci",
            "qemu",
            "runner",
            "run.sh",
            "boot",
            "image",
            "initramfs",
            "repack",
            "package",
            "component",
            "module",
            "sdk",
            "release",
        )
        matches: list[str] = []
        for path in target_root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(target_root).as_posix()
            lowered = relative.lower()
            if any(marker in lowered for marker in markers):
                matches.append(relative)
            if len(matches) >= 500:
                break
        return sorted(matches)

    @staticmethod
    def _artifact_candidates(
        metadata_paths: list[str], tools: list[dict[str, Any]], qemu_root: Path, project: Project
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        rules = (
            (
                ArtifactMode.VERIFIED_LOCAL_RUNNER,
                ("runner", "run.sh", "qemu"),
                "versioned target runner or QEMU helper",
            ),
            (
                ArtifactMode.OFFICIAL_CONTAINER_OR_SDK,
                ("dockerfile", "devcontainer", "container", "sdk"),
                "official container or SDK metadata",
            ),
            (
                ArtifactMode.PREBUILT_COMPONENT_INSERTION,
                ("component", "module", "package"),
                "component/module/package insertion metadata",
            ),
            (
                ArtifactMode.IMAGE_REPACK,
                ("repack", "initramfs", "image"),
                "image overlay or repack metadata",
            ),
            (
                ArtifactMode.CI_DERIVED_BUILD,
                ("workflow", "gitlab-ci", ".github"),
                "versioned CI/release automation",
            ),
            (
                ArtifactMode.SOURCE_BUILD,
                ("makefile", "cargo.toml", "justfile", "taskfile"),
                "source build metadata; use only if lighter routes cannot contain the driver",
            ),
        )
        for mode, tokens, reason in rules:
            evidence = [
                path for path in metadata_paths if any(token in path.lower() for token in tokens)
            ]
            if evidence:
                candidates.append(
                    {
                        "artifact_mode": mode.value,
                        "reason": reason,
                        "evidence_paths": evidence[:50],
                        "status": "DISCOVERED_NOT_EXECUTED",
                    }
                )
        qemu_tools = [
            item for item in tools if item["available"] and item["name"].startswith("qemu-")
        ]
        if qemu_tools:
            candidates.append(
                {
                    "artifact_mode": ArtifactMode.DIRECT_DEVICE_MODEL.value,
                    "reason": "host QEMU binary plus frozen QEMU model source can support a direct smoke",
                    "evidence_paths": [
                        str(qemu_root.relative_to(project.root)),
                        *[item["path"] for item in qemu_tools],
                    ],
                    "status": "DISCOVERED_NOT_EXECUTED",
                }
            )
        return candidates

    def _validate_plan_paths(self, project: Project, plan: ExperimentPlan) -> None:
        cwd = self._workspace_path(project, plan.cwd)
        if not cwd.is_dir():
            raise WorkflowError(f"experiment cwd does not exist: {cwd}")
        for path in plan.runner_evidence_paths:
            evidence = self._workspace_path(project, path)
            if not evidence.exists():
                raise WorkflowError(f"runner evidence does not exist: {path}")
        acquisition = project.load_json_artifact("evidence_acquisition", "acquisition_manifest")
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        source = self._checkout(checkouts, RepositoryRole.SOURCE).checkout_path
        target = self._checkout(checkouts, RepositoryRole.TARGET).checkout_path
        qemu = self._checkout(checkouts, RepositoryRole.QEMU).checkout_path
        target_working = str(acquisition["target_worktree"])
        normalized_evidence = tuple(
            str(Path(path).as_posix()) for path in plan.runner_evidence_paths
        )
        file_evidence = tuple(
            path
            for path in plan.runner_evidence_paths
            if self._workspace_path(project, path).is_file()
        )
        executable = self._executable_identity(plan.command[0], cwd)
        executable_name = (
            Path(executable["resolved"]).name
            if executable["resolved"]
            else Path(plan.command[0]).name
        )
        if plan.route_kind is RouteKind.DIRECT_QEMU:
            if not (
                executable_name.startswith("qemu-system-")
                or executable_name == "qemu-storage-daemon"
            ):
                raise WorkflowError("direct-qemu route must execute a QEMU system binary")
            if not any(path == qemu or path.startswith(qemu + "/") for path in normalized_evidence):
                raise WorkflowError("direct-qemu route must cite the frozen QEMU source checkout")
        elif plan.route_kind is RouteKind.CONTAINERIZED_QEMU:
            if executable_name not in {"docker", "podman"}:
                raise WorkflowError("containerized-qemu route must execute docker or podman")
            if not file_evidence:
                raise WorkflowError(
                    "containerized-qemu route must cite a frozen container/runner file"
                )
        elif plan.route_kind is RouteKind.OFFICIAL_TARGET_RUNNER:
            allowed_roots = (target, target_working)
            if not any(
                path == root or path.startswith(root + "/")
                for root in allowed_roots
                for path in normalized_evidence
            ):
                raise WorkflowError(
                    "official-target-runner must cite frozen target metadata or its writable worktree"
                )
            if not file_evidence:
                raise WorkflowError(
                    "official-target-runner must cite a concrete runner or metadata file"
                )
        elif plan.route_kind is RouteKind.SOURCE_BASELINE_RUNNER:
            if not any(
                path == source or path.startswith(source + "/") for path in normalized_evidence
            ):
                raise WorkflowError("source-baseline-runner must cite the frozen source checkout")
            if not file_evidence:
                raise WorkflowError("source-baseline-runner must cite a concrete runner file")
        elif plan.route_kind is RouteKind.QTEST_OR_QMP_HARNESS:
            if not any(path == qemu or path.startswith(qemu + "/") for path in normalized_evidence):
                raise WorkflowError(
                    "qtest-or-qmp-harness must cite the frozen QEMU source checkout"
                )
            if not file_evidence:
                raise WorkflowError("qtest-or-qmp-harness must cite a concrete frozen harness file")

    @staticmethod
    def _plan_path(project: Project, route_id: str) -> Path:
        if not route_id or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
            for character in route_id
        ):
            raise WorkflowError("route_id may contain only letters, digits, '-' and '_'")
        return project.control / "environment" / "plans" / f"{route_id}.json"

    @staticmethod
    def _file_identity(project: Project, relative: str) -> dict[str, Any]:
        path = EnvironmentService._workspace_path(project, relative)
        if path.is_file():
            return {
                "path": relative,
                "kind": "file",
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size": path.stat().st_size,
            }
        return {"path": relative, "kind": "directory", "sha256": None, "size": None}

    @staticmethod
    def _executable_identity(command: str, cwd: Path) -> dict[str, Any]:
        candidate = Path(command)
        if candidate.is_absolute():
            resolved = candidate.resolve()
        elif "/" in command:
            resolved = (cwd / candidate).resolve()
        else:
            located = shutil.which(command)
            resolved = Path(located).resolve() if located else None
        if resolved is None or not resolved.is_file():
            return {"requested": command, "resolved": None, "sha256": None}
        return {
            "requested": command,
            "resolved": str(resolved),
            "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
            "size": resolved.stat().st_size,
        }

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(
            "utf-8"
        )

    def _add_json(self, project: Project, kind: str, value: dict[str, Any]) -> str:
        return project.add_bytes(
            "environment_recovery",
            kind,
            self._json_bytes(value),
            source=f"generated:environment:{kind}",
        )
