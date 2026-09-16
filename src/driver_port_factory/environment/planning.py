from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..core.models import ActorRole, ArtifactDirection, FileArtifact, StageStatus, WorkflowError
from ..core.project import Project
from .contracts import EnvironmentArtifact, EnvironmentStage
from .documents import json_bytes, plan_path
from .evidence import (
    freeze_qemu_executable,
    frozen_repository_snapshot,
    workspace_path,
)
from .models import ExperimentPlan


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
        repositories = frozen_repository_snapshot(project)
        cwd = workspace_path(project, plan.cwd)
        if not cwd.is_dir():
            raise WorkflowError(f"experiment cwd does not exist: {cwd}")
        if any(not workspace_path(project, path).exists() for path in plan.runner_evidence_paths):
            raise WorkflowError("runner evidence path does not exist")
        acquisition = load_repository_acquisition(project)
        qemu_root = acquisition.checkout(RepositoryRole.QEMU).checkout_path
        if not any(
            path == qemu_root or path.startswith(qemu_root + "/")
            for path in plan.runner_evidence_paths
        ):
            raise WorkflowError("direct-QEMU route must cite the frozen QEMU checkout")
        if "-qmp" in plan.command or "-monitor" in plan.command:
            raise WorkflowError("the QMP controller owns monitor transport arguments")
        executable = freeze_qemu_executable(project, plan.command[0], cwd)
        if not Path(executable["resolved"]).name.startswith("qemu-system-"):
            raise WorkflowError("direct-QEMU route must execute a QEMU system binary")
        plan = replace(
            plan,
            executable_lock=executable,
            frozen_repositories=repositories,
        )
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
