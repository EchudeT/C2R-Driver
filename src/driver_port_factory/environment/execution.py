from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..core.execution import CommandResult, CommandRunner
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    FileArtifact,
    StageStatus,
    WorkflowError,
    utc_now,
)
from ..core.project import Project
from .contracts import EnvironmentArtifact, EnvironmentStage, ExperimentRouteMilestone
from .documents import json_artifact, json_bytes, plan_path
from .evidence import (
    executable_identity,
    file_identity,
    workspace_path,
)
from .models import ExperimentPlan, ExperimentReadiness
from .planning import ExperimentPlanValidator


@dataclass(frozen=True, slots=True)
class EnvironmentRunResult:
    route_id: str
    readiness: ExperimentReadiness
    stage_status: StageStatus
    attempt_path: str
    message: str


class ExperimentExecutor:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def run(self, project: Project, route_id: str) -> EnvironmentRunResult:
        project.ensure_role(*self.ROLES)
        if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
            raise WorkflowError("environment_recovery is not RUNNING")
        controlled_plan = plan_path(project, route_id)
        if not controlled_plan.is_file():
            raise WorkflowError(f"unknown environment route: {route_id}")
        plan = ExperimentPlan.from_dict(json.loads(controlled_plan.read_text(encoding="utf-8")))
        ExperimentPlanValidator().validate(project, plan)
        cwd = workspace_path(project, plan.cwd)
        attempt_path = project.control / "environment" / "attempts" / f"{plan.route_id}.json"
        if attempt_path.exists():
            raise WorkflowError(
                f"route {plan.route_id} already ran; retries require a new route ID"
            )
        result = CommandRunner(project.control / "command-runs" / "environment").run(
            plan.command,
            cwd=cwd,
            environment=plan.environment,
            timeout_seconds=plan.timeout_seconds,
        )
        observations = self._observations(plan, result)
        acquisition = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        evidence = [file_identity(project, item) for item in plan.runner_evidence_paths]
        attempt = {
            "schema_version": 1,
            "route": plan.to_dict(),
            "run": asdict(result),
            "runner_evidence": evidence,
            "command_executable": executable_identity(plan.command[0], cwd),
            "frozen_repository_locks": acquisition["checkouts"],
            "marker_observations": observations["markers"],
            "exit_accepted": observations["exit_accepted"],
            "readiness": observations["readiness"],
            "recorded_at": utc_now(),
        }
        attempt_path.parent.mkdir(parents=True, exist_ok=True)
        attempt_path.write_bytes(json_bytes(attempt))
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.RECOVERY_ATTEMPT, attempt_path),
        )
        readiness = ExperimentReadiness(observations["readiness"])
        if readiness is ExperimentReadiness.FAIL:
            return EnvironmentRunResult(
                plan.route_id,
                readiness,
                StageStatus.RUNNING,
                str(attempt_path),
                "route did not satisfy its frozen marker/exit oracle; plan a distinct retry",
            )
        self._finalize(project, plan, attempt_path, evidence)
        return EnvironmentRunResult(
            plan.route_id,
            readiness,
            StageStatus.PASS,
            str(attempt_path),
            "EXPERIMENT_READY observed; migrated-driver runtime remains unproven",
        )

    @staticmethod
    def _observations(plan: ExperimentPlan, result: CommandResult) -> dict[str, object]:
        stdout = Path(result.stdout_path).read_bytes()
        stderr = Path(result.stderr_path).read_bytes()
        combined = (stdout + b"\n" + stderr).decode("utf-8", errors="replace")
        markers = {marker: marker in combined for marker in plan.expected_markers}
        exit_accepted = result.exit_code in plan.accepted_exit_codes or (
            result.timed_out and plan.accept_timeout
        )
        ready = result.launched and exit_accepted and all(markers.values())
        return {
            "markers": markers,
            "exit_accepted": exit_accepted,
            "readiness": ExperimentReadiness.PASS if ready else ExperimentReadiness.FAIL,
        }

    @staticmethod
    def _finalize(
        project: Project,
        plan: ExperimentPlan,
        attempt_path: Path,
        evidence: list[dict[str, object]],
    ) -> None:
        artifact_mode = {
            "schema_version": 1,
            "artifact_mode": plan.artifact_mode,
            "route_kind": plan.route_kind,
            "selected_route_id": plan.route_id,
            "compiled_reused_injected_or_prebuilt": (
                "environment smoke only; migrated-driver payload identity is established later"
            ),
            "driver_insertion_or_packaging_path": plan.driver_insertion_or_packaging_path,
            "runner_evidence": evidence,
        }
        route = {
            "schema_version": 1,
            "milestone": ExperimentRouteMilestone.READY,
            "route_id": plan.route_id,
            "device_identity": plan.device_identity,
            "topology": plan.topology,
            "artifact_mode": plan.artifact_mode,
            "route_kind": plan.route_kind,
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
                json_artifact(EnvironmentArtifact.MODE_RECORD, artifact_mode),
                FileArtifact(EnvironmentArtifact.EXPERIMENT_READY_RUN, attempt_path),
                json_artifact(EnvironmentArtifact.EXPERIMENT_ROUTE, route),
            ),
        )
