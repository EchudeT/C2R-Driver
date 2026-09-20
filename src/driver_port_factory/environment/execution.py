from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.container_trace import ContainerTrace
from ..codex.contracts import CodexOutputError
from ..core.execution import CommandRunner, script_command
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    FileArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from ..core.trace import qemu_experiment, successful_execs
from ..knowledge.index import file_sha256
from .contracts import EnvironmentArtifact, EnvironmentStage, ExperimentRouteMilestone
from .documents import json_artifact, json_bytes, plan_path
from .evidence import (
    executable_identity,
    file_identity,
    frozen_repository_snapshot,
    workspace_path,
)
from .models import ArtifactMode, ExperimentPlan, ExperimentReadiness


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
        if not work_report_path.is_file() or not work_report_path.read_text(
            encoding="utf-8"
        ).strip():
            raise CodexOutputError("environment work report is missing or blank")
        strace = shutil.which("strace")
        if strace is None:
            raise WorkflowError("strace is required to prove that the smoke harness executed QEMU")

        script_digest = file_sha256(script_path)
        attempt_dir = project.control / "environment" / "codex-harness" / str(uuid.uuid4())
        attempt_dir.mkdir(parents=True, exist_ok=True)
        trace_path = attempt_dir / "execve.log"
        with ContainerTrace(script_path.parent, attempt_dir / "container-processes.json") as containers:
            result = CommandRunner(attempt_dir / "command").run(
                [
                    strace,
                    "-f",
                    "-qq",
                    "-s",
                    "65535",
                    "-e",
                    "trace=execve",
                    "-o",
                    str(trace_path),
                    *script_command(script_path),
                ],
                cwd=script_path.parent,
                environment={
                    "DPF_PROJECT_ROOT": str(project.root),
                    "DPF_ENVIRONMENT_WORKDIR": str(script_path.parent),
                },
                timeout_seconds=3600,
            )
        executions = successful_execs(trace_path.read_text(errors="replace").splitlines()) if trace_path.is_file() else ()
        executions += containers.executions(trace_path)
        executed = list(dict.fromkeys(path for path, _ in executions))
        qemu_programs = [
            path for path, line in executions
            if Path(path).name.startswith("qemu-system-") and qemu_experiment(line)
        ]
        ready = (
            result.launched
            and result.launch_error is None
            and not result.timed_out
            and result.exit_code == 0
            and bool(qemu_programs)
        )
        readiness = ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL
        route_id = f"codex-harness-{script_digest[:16]}"
        attempt = {
            "schema_version": 3,
            "repair_inputs": {"script_sha256": script_digest},
            "route": {
                "route_id": route_id,
                "artifact_mode": "documented-in-work-report",
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
                "path": str(trace_path.relative_to(project.root)),
                "sha256": file_sha256(trace_path),
                "executed_programs": executed,
                "qemu_programs": qemu_programs,
                "container_evidence": str(containers.output),
                "container_evidence_sha256": file_sha256(containers.output),
            },
            "readiness": readiness.value,
            "recorded_at": utc_now(),
        }
        attempt_path = attempt_dir / "attempt.json"
        attempt_path.write_bytes(json_bytes(attempt))
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.RECOVERY_ATTEMPT, attempt_path),
        )
        if not ready:
            return EnvironmentRunResult(
                route_id,
                readiness,
                StageStatus.RUNNING,
                str(attempt_path),
                (f"smoke harness exit={result.exit_code}, timed_out={result.timed_out}, "
                 f"observed QEMU experiment execs={len(qemu_programs)} (version/help is not smoke). "
                 "Container runs require a fresh container, an exact workspace bind mount, "
                 "a traced docker run, and a live QEMU process observation. "
                 f"Inspect stdout={result.stdout_path}, stderr={result.stderr_path}, "
                 f"trace={trace_path}"),
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
            "artifact_mode": "documented-in-work-report",
            "selected_route_id": route_id,
            "work_report": attempt["work_report"],
        }
        route = {
            "schema_version": 3,
            "milestone": ExperimentRouteMilestone.READY,
            "route_id": route_id,
            "artifact_mode": "documented-in-work-report",
            "command": list(result.argv),
            "attempt_sha256": hashlib.sha256(attempt_path.read_bytes()).hexdigest(),
            "qemu_programs": qemu_programs,
            "migrated_driver_runtime_ready": False,
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
                "runner_evidence": [file_identity(project, item) for item in plan.runner_evidence_paths],
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
