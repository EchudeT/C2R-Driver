from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..codex.contracts import CodexOutputError
from ..core.container_policy import (
    container_execution_summary,
    target_requires_asterinas_container,
)
from ..core.container_trace import ContainerTrace
from ..core.execution import CommandRunner, observed_script_command
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    ControllerError,
    FileArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from ..core.trace import qemu_experiment, successful_execs
from ..knowledge.index import file_sha256
from .contracts import EnvironmentArtifact, EnvironmentStage, ExperimentRouteMilestone
from .diagnostics import failures, message, platform_failures
from .documents import json_artifact, json_bytes, plan_path
from .evidence import (
    executable_identity,
    file_identity,
    frozen_repository_snapshot,
    workspace_path,
)
from .models import ArtifactMode, ExperimentPlan, ExperimentReadiness
from .smoke_recipe import archive
from .smoke_recipe import inputs as recipe_inputs


def _platform_binding(project):
    from ..platform.service import acceptance_binding

    try:
        return acceptance_binding(project)
    except WorkflowError as error:
        raise CodexOutputError(
            f"{error}. Use environment platform prepare/verify in this stage; "
            "a device-model probe cannot substitute for target baseline build/boot."
        ) from error


def _capture_harness(script_path, attempt_dir, recipe):
    trace_path = attempt_dir / "execve.log"
    infrastructure_errors = []
    if recipe:
        from .managed_smoke import run as run_managed_smoke
        from .smoke_recipe import NAME, read_recipe

        prepared = read_recipe(script_path.parent / NAME)
        capture = run_managed_smoke(
            script_path.parent,
            attempt_dir,
            recipe,
            script_path.parent / prepared["probe"],
        )
        result = capture.command
        host_executions = ()
        executions = capture.executions
        container_output = capture.output
        infrastructure_errors = capture.infrastructure_errors
    else:
        with ContainerTrace(
            script_path.parent, attempt_dir / "container-processes.json"
        ) as containers:
            result = CommandRunner(attempt_dir / "command").run(
                observed_script_command(script_path, trace_path),
                cwd=script_path.parent,
                environment={
                    "DPF_PROJECT_ROOT": str(script_path.parents[3]),
                    "DPF_ENVIRONMENT_WORKDIR": str(script_path.parent),
                },
                timeout_seconds=3600,
            )
        host_executions = (
            successful_execs(trace_path.read_text(errors="replace").splitlines())
            if trace_path.is_file()
            else ()
        )
        executions = host_executions + containers.executions(trace_path)
        container_output = containers.output
    return result, host_executions, executions, container_output, infrastructure_errors


def _input_changes(project, script_path, script_digest, recipe, container_summary):
    reasons = []
    if recipe:
        if container_summary["image_ids"] and set(container_summary["image_ids"]) != {
            recipe["image_id"]
        }:
            reasons.append("Observed container image differs from the prepared recipe")
        try:
            unchanged = recipe_inputs(project, script_path) == recipe
        except (WorkflowError, OSError, ValueError):
            unchanged = False
        if not unchanged:
            reasons.append("Recipe/probe changed during the smoke; rerun the current inputs")
    try:
        script_unchanged = file_sha256(script_path) == script_digest
    except OSError:
        script_unchanged = False
    if not script_unchanged:
        reasons.append("Smoke script changed during execution; rerun current inputs")
    return reasons


@dataclass(frozen=True, slots=True)
class EnvironmentRunResult:
    route_id: str
    readiness: ExperimentReadiness
    stage_status: StageStatus
    attempt_path: str
    message: str


class ExperimentExecutor:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def run_codex_harness(
        self,
        project: Project,
        *,
        script_path: Path,
        work_report_path: Path,
    ) -> EnvironmentRunResult:
        """Rerun a Codex-authored smoke harness and record only mechanical evidence."""

        project.ensure_role(*self.ROLES)
        if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
            raise WorkflowError("environment_recovery is not RUNNING")
        if not script_path.is_file():
            raise CodexOutputError("environment work did not create environment-smoke.sh")
        if (
            not work_report_path.is_file()
            or not work_report_path.read_text(encoding="utf-8").strip()
        ):
            raise CodexOutputError("environment work report is missing or blank")

        _platform_binding(project)
        recipe = recipe_inputs(project, script_path)
        if recipe is None and target_requires_asterinas_container(project.config.target_platform):
            raise CodexOutputError(
                "Official container smoke requires environment prepare-smoke and a device probe; "
                "the controller owns execution and capture. Do not write a Docker wrapper."
            )
        script_digest = file_sha256(script_path)
        attempt_dir = project.control / "environment" / "codex-harness" / str(uuid.uuid4())
        attempt_dir.mkdir(parents=True, exist_ok=True)
        if recipe:
            archive(script_path, attempt_dir)
        trace_path = attempt_dir / "execve.log"
        result, host_executions, executions, container_output, infrastructure_errors = (
            _capture_harness(script_path, attempt_dir, recipe)
        )
        executed = list(dict.fromkeys(path for path, _ in executions))
        qemu_programs = [
            path
            for path, line in executions
            if Path(path).name.startswith("qemu-system-") and qemu_experiment(line)
        ]
        host_qemu_programs = [
            path
            for path, line in host_executions
            if Path(path).name.startswith("qemu-system-") and qemu_experiment(line)
        ]
        container_summary = container_execution_summary(
            observations_path=container_output,
            target_platform=project.config.target_platform,
            host_qemu_execs=tuple(host_qemu_programs),
        )
        # The Codex-authored harness has no structured route proposal.  The
        # controller therefore derives a valid mode from the observed runner;
        # never persist the old free-form ``documented-in-work-report`` value,
        # which could make an unclassified model smoke look like a delivery
        # route.
        target_name = project.config.target_platform.strip().lower().replace(" ", "-")
        artifact_mode = (
            ArtifactMode.OFFICIAL_CONTAINER_OR_SDK.value
            if target_name in {"asterinas", "asterinas-os", "asterinas_os"}
            and container_summary["satisfied"]
            else ArtifactMode.VERIFIED_LOCAL_RUNNER.value
        )
        ready = (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code == 0
            and bool(qemu_programs)
            and container_summary["satisfied"]
        )
        collector = json.loads(trace_path.with_suffix(".collector.json").read_text())
        reasons = (
            failures(result, qemu_programs, container_summary, collector) if not ready else []
        ) + platform_failures(project, container_summary)
        reasons.extend(
            _input_changes(project, script_path, script_digest, recipe, container_summary)
        )
        if not recipe and (
            not collector.get("available")
            or (container_summary["required"] and not container_summary["satisfied"])
        ):
            infrastructure_errors.append(
                "Legacy execution collector could not establish provenance"
            )
        reasons.extend(infrastructure_errors)
        ready = ready and not reasons
        readiness = ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL
        route_id = f"codex-harness-{script_digest[:16]}"
        attempt = {
            "schema_version": 3,
            "repair_inputs": {"script_sha256": script_digest, "container_recipe": recipe},
            "route": {
                "route_id": route_id,
                "artifact_mode": artifact_mode,
                "script": str(script_path.relative_to(project.root)),
            },
            "command": asdict(result),
            "script": {
                "path": str(script_path.relative_to(project.root)),
                "sha256": script_digest,
            },
            "work_report": {
                "path": str(work_report_path.relative_to(project.root)),
                "sha256": file_sha256(work_report_path),
            },
            "exec_trace": {
                "collector": collector,
                "path": str(trace_path.relative_to(project.root)),
                "sha256": file_sha256(trace_path),
                "executed_programs": executed,
                "qemu_programs": qemu_programs,
                "host_qemu_programs": host_qemu_programs,
                "container_execution": container_summary,
                "container_evidence": str(container_output),
                "container_evidence_sha256": file_sha256(container_output),
            },
            "readiness": readiness.value,
            "failure_reasons": reasons,
            "failure_class": "INFRASTRUCTURE"
            if infrastructure_errors
            else (None if ready else "PROBE"),
            "recorded_at": utc_now(),
        }
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(json_bytes(attempt))
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.RECOVERY_ATTEMPT, attempt_path),
        )
        if infrastructure_errors:
            detail = message(reasons, attempt_path, result)
            project.note_check(detail)
            raise ControllerError(
                "Environment infrastructure failed; no worker repair or automatic retry. " + detail
            )
        if not ready:
            detail = message(reasons, attempt_path, result)
            project.note_check(detail)
            return EnvironmentRunResult(
                route_id,
                readiness,
                StageStatus.RUNNING,
                str(attempt_path),
                detail,
            )

        inventory = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.INVENTORY,
            direction=ArtifactDirection.INPUT,
        )
        candidates = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.MODE_CANDIDATES,
            direction=ArtifactDirection.INPUT,
        )
        mode = {
            "schema_version": 3,
            "artifact_mode": artifact_mode,
            "selected_route_id": route_id,
            "work_report": attempt["work_report"],
        }
        route = {
            "platform_execution": _platform_binding(project),
            "schema_version": 3,
            "milestone": ExperimentRouteMilestone.READY,
            "route_id": route_id,
            "artifact_mode": artifact_mode,
            "command": list(result.argv),
            "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
            "qemu_programs": qemu_programs,
            "container_execution": container_summary,
            "migrated_driver_runtime_ready": False,
            "mechanical_readiness": readiness.value,
        }
        project.finalize_stage(
            EnvironmentStage.RECOVERY,
            (
                json_artifact(EnvironmentArtifact.INVENTORY, inventory),
                json_artifact(EnvironmentArtifact.MODE_CANDIDATES, candidates),
                json_artifact(EnvironmentArtifact.MODE_RECORD, mode),
                FileArtifact(EnvironmentArtifact.EXPERIMENT_READY_RUN, attempt_path),
                json_artifact(EnvironmentArtifact.EXPERIMENT_ROUTE, route),
            ),
        )
        return EnvironmentRunResult(
            route_id,
            readiness,
            StageStatus.PASS,
            str(attempt_path),
            "EXPERIMENT_READY proven by a captured QEMU exec",
        )

    @staticmethod
    def _executed_programs(trace_path: Path) -> list[str]:
        if not trace_path.is_file():
            return []
        lines = trace_path.read_text(encoding="utf-8", errors="replace").splitlines()
        return sorted({path for path, _ in successful_execs(lines)})

    def run(self, project: Project, route_id: str) -> EnvironmentRunResult:
        project.ensure_role(*self.ROLES)
        if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
            raise WorkflowError("environment_recovery is not RUNNING")
        plan = self._load_plan(project, route_id)
        _platform_binding(project)
        self._require_frozen_inputs(project, plan)
        attempt_path = project.control / "environment" / "attempts" / f"{route_id}.json"
        if attempt_path.exists():
            raise WorkflowError(f"route {route_id} already ran; retries require a new route ID")

        cwd = workspace_path(project, plan.cwd)
        executable = executable_identity(plan.command[0], cwd)
        result = CommandRunner(
            project.control / "command-runs" / "environment" / plan.route_id
        ).run(
            [str(executable["resolved"]), *plan.command[1:]],
            cwd=cwd,
            environment=plan.environment,
            timeout_seconds=plan.timeout_seconds,
        )
        ready = (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code in plan.accepted_exit_codes
        )
        readiness = ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL
        attempt = {
            "schema_version": 3,
            "repair_inputs": {
                "route": {key: value for key, value in plan.to_dict().items() if key != "route_id"},
                "executable": executable,
                "runner_evidence": [
                    file_identity(project, item) for item in plan.runner_evidence_paths
                ],
            },
            "route": plan.to_dict(),
            "frozen_repositories": plan.frozen_repositories,
            "command": asdict(result),
            "runner_evidence": [
                file_identity(project, item) for item in plan.runner_evidence_paths
            ],
            "readiness": readiness.value,
            "recorded_at": utc_now(),
        }
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(json_bytes(attempt))
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.RECOVERY_ATTEMPT, attempt_path),
        )
        if readiness is ExperimentReadiness.FAIL:
            return EnvironmentRunResult(
                route_id,
                readiness,
                StageStatus.RUNNING,
                str(attempt_path),
                "QEMU experiment command failed; plan one distinct recovery route",
            )
        self._finalize(project, plan, attempt_path, attempt["runner_evidence"])
        return EnvironmentRunResult(
            route_id,
            readiness,
            StageStatus.PASS,
            str(attempt_path),
            "EXPERIMENT_READY proven; migrated-driver runtime remains unproven",
        )

    @staticmethod
    def _load_plan(project: Project, route_id: str) -> ExperimentPlan:
        controlled = plan_path(project, route_id)
        if not controlled.is_file():
            raise WorkflowError(f"unknown environment route: {route_id}")
        data = controlled.read_bytes()
        matches = [
            ref
            for ref in project.artifact_refs(
                stage=EnvironmentStage.RECOVERY,
                direction=ArtifactDirection.INPUT,
            )
            if ref.kind == EnvironmentArtifact.EXPERIMENT_PLAN.value
            and ref.source == str(controlled)
            and project.artifacts.read(ref) == data
        ]
        if len(matches) != 1:
            raise WorkflowError("controlled experiment plan differs from its frozen artifact")
        try:
            plan = ExperimentPlan.from_dict(json.loads(data))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("controlled experiment plan is invalid JSON") from error
        if plan.route_id != route_id or plan.executable_lock is None:
            raise WorkflowError("controlled experiment plan has invalid frozen identity")
        return plan

    @staticmethod
    def _require_frozen_inputs(project: Project, plan: ExperimentPlan) -> None:
        if frozen_repository_snapshot(project) != plan.frozen_repositories:
            raise WorkflowError("frozen repositories changed after route registration")
        candidates = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.MODE_CANDIDATES,
            direction=ArtifactDirection.INPUT,
        )
        candidate_modes = {ArtifactMode(item["artifact_mode"]) for item in candidates["candidates"]}
        if plan.artifact_mode not in candidate_modes:
            raise WorkflowError("experiment route did not select a discovered artifact mode")
        cwd = workspace_path(project, plan.cwd)
        if executable_identity(plan.command[0], cwd) != plan.executable_lock:
            raise WorkflowError("experiment runner changed after route registration")

    @staticmethod
    def _finalize(
        project: Project,
        plan: ExperimentPlan,
        attempt_path: Path,
        evidence: list[dict[str, object]],
    ) -> None:
        mode = {
            "schema_version": 3,
            "artifact_mode": plan.artifact_mode,
            "selected_route_id": plan.route_id,
            "driver_insertion_or_packaging_path": plan.driver_insertion_or_packaging_path,
            "runner_evidence": evidence,
        }
        route = {
            "platform_execution": _platform_binding(project),
            "schema_version": 3,
            "milestone": ExperimentRouteMilestone.READY,
            "route_id": plan.route_id,
            "device_identity": plan.device_identity,
            "topology": plan.topology,
            "artifact_mode": plan.artifact_mode,
            "command": list(plan.command),
            "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
            "migrated_driver_runtime_ready": False,
        }
        inventory = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.INVENTORY,
            direction=ArtifactDirection.INPUT,
        )
        candidates = project.load_json_artifact(
            EnvironmentStage.RECOVERY,
            EnvironmentArtifact.MODE_CANDIDATES,
            direction=ArtifactDirection.INPUT,
        )
        project.finalize_stage(
            EnvironmentStage.RECOVERY,
            (
                json_artifact(EnvironmentArtifact.INVENTORY, inventory),
                json_artifact(EnvironmentArtifact.MODE_CANDIDATES, candidates),
                json_artifact(EnvironmentArtifact.MODE_RECORD, mode),
                FileArtifact(EnvironmentArtifact.EXPERIMENT_READY_RUN, attempt_path),
                json_artifact(EnvironmentArtifact.EXPERIMENT_ROUTE, route),
            ),
        )
