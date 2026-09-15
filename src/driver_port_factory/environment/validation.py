from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, require_fields
from .contracts import EnvironmentArtifact, ExperimentRouteMilestone
from .models import ExperimentReadiness


def _inventory(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.INVENTORY.value)
    if not isinstance(value.get("host"), dict) or not isinstance(value.get("tools"), list):
        raise WorkflowError("environment_inventory requires host and tools records")
    if not isinstance(value.get("frozen_repositories"), dict):
        raise WorkflowError("environment_inventory requires frozen repository identities")


def _mode_candidates(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.MODE_CANDIDATES.value)
    if not isinstance(value.get("candidates"), list) or not value.get("selection_rule"):
        raise WorkflowError("artifact_mode_candidates requires candidates and a selection rule")


def _experiment_plan(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.EXPERIMENT_PLAN.value)
    if ExperimentRouteMilestone(value.get("milestone")) is not ExperimentRouteMilestone.READY:
        raise WorkflowError("environment plan must target EXPERIMENT_READY")
    if not isinstance(value.get("command"), list) or not value["command"]:
        raise WorkflowError("environment plan requires a non-empty command")
    if not isinstance(value.get("expected_markers"), list) or not value["expected_markers"]:
        raise WorkflowError("environment plan requires expected markers")


def _recovery_attempt(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.RECOVERY_ATTEMPT.value)
    if not isinstance(value.get("route"), dict) or not isinstance(value.get("run"), dict):
        raise WorkflowError("environment recovery attempt requires route and run evidence")


def _ready_run(data: bytes) -> None:
    _recovery_attempt(data)
    value = json_object(data, EnvironmentArtifact.EXPERIMENT_READY_RUN.value)
    if ExperimentReadiness(value.get("readiness")) is not ExperimentReadiness.PASS:
        raise WorkflowError("experiment_ready_run must have readiness=PASS")


def _mode_record(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.MODE_RECORD.value)
    require_fields(
        value,
        {"artifact_mode", "route_kind", "selected_route_id"},
        EnvironmentArtifact.MODE_RECORD.value,
    )


def _experiment_route(data: bytes) -> None:
    value = json_object(data, EnvironmentArtifact.EXPERIMENT_ROUTE.value)
    if ExperimentRouteMilestone(value.get("milestone")) is not ExperimentRouteMilestone.READY:
        raise WorkflowError("experiment_route must record EXPERIMENT_READY")
    if value.get("migrated_driver_runtime_ready") is not False:
        raise WorkflowError("environment recovery cannot claim migrated-driver runtime readiness")


VALIDATORS = MappingProxyType[EnvironmentArtifact, ArtifactValidator](
    {
        EnvironmentArtifact.INVENTORY: _inventory,
        EnvironmentArtifact.MODE_CANDIDATES: _mode_candidates,
        EnvironmentArtifact.EXPERIMENT_PLAN: _experiment_plan,
        EnvironmentArtifact.RECOVERY_ATTEMPT: _recovery_attempt,
        EnvironmentArtifact.MODE_RECORD: _mode_record,
        EnvironmentArtifact.EXPERIMENT_READY_RUN: _ready_run,
        EnvironmentArtifact.EXPERIMENT_ROUTE: _experiment_route,
    }
)
