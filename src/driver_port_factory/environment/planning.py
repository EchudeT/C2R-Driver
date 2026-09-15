from __future__ import annotations

import json
from pathlib import Path

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import ActorRole, ArtifactDirection, FileArtifact, StageStatus, WorkflowError
from ..core.project import Project
from .contracts import EnvironmentArtifact, EnvironmentStage
from .documents import json_bytes, plan_path
from .evidence import checkout_for, executable_identity, workspace_path
from .models import ExperimentPlan
from .route_policy import ExperimentRoutePolicy, RouteEvidence


class ExperimentPlanValidator:
    def validate(self, project: Project, plan: ExperimentPlan) -> None:
        cwd = workspace_path(project, plan.cwd)
        if not cwd.is_dir():
            raise WorkflowError(f"experiment cwd does not exist: {cwd}")
        for relative in plan.runner_evidence_paths:
            if not workspace_path(project, relative).exists():
                raise WorkflowError(f"runner evidence does not exist: {relative}")
        acquisition = project.load_json_artifact(
            AcquisitionStage.EVIDENCE_ACQUISITION,
            AcquisitionArtifact.ACQUISITION_MANIFEST,
        )
        checkouts = tuple(CheckoutRecord.from_dict(record) for record in acquisition["checkouts"])
        evidence_paths = tuple(Path(path).as_posix() for path in plan.runner_evidence_paths)
        cited_files = tuple(
            path for path in plan.runner_evidence_paths if workspace_path(project, path).is_file()
        )
        executable = executable_identity(plan.command[0], cwd)
        executable_name = (
            Path(executable["resolved"]).name
            if executable["resolved"]
            else Path(plan.command[0]).name
        )
        roots = {role: checkout_for(checkouts, role).checkout_path for role in RepositoryRole}
        ExperimentRoutePolicy().validate(
            plan.route_kind,
            RouteEvidence(
                executable_name,
                evidence_paths,
                cited_files,
                roots[RepositoryRole.SOURCE],
                roots[RepositoryRole.TARGET],
                str(acquisition["target_worktree"]),
                roots[RepositoryRole.QEMU],
            ),
        )


class ExperimentPlanRegistrar:
    ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)

    def register(self, project: Project, path: Path) -> ExperimentPlan:
        project.ensure_role(*self.ROLES)
        if project.stage(EnvironmentStage.RECOVERY).status is not StageStatus.RUNNING:
            raise WorkflowError("inspect the environment before registering a route plan")
        try:
            value = json.loads(path.resolve().read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("experiment plan must be readable UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise WorkflowError("experiment plan must be a JSON object")
        plan = ExperimentPlan.from_dict(value)
        ExperimentPlanValidator().validate(project, plan)
        controlled = plan_path(project, plan.route_id)
        proposed = json_bytes(plan.to_dict())
        if controlled.exists() and controlled.read_bytes() != proposed:
            raise WorkflowError(f"route_id {plan.route_id} is immutable; choose a new route ID")
        if not controlled.exists():
            controlled.parent.mkdir(parents=True, exist_ok=True)
            controlled.write_bytes(proposed)
        project.record_artifact(
            EnvironmentStage.RECOVERY,
            FileArtifact(EnvironmentArtifact.EXPERIMENT_PLAN, controlled),
            direction=ArtifactDirection.INPUT,
        )
        return plan
