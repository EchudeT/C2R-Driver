from enum import StrEnum


class EnvironmentStage(StrEnum):
    RECOVERY = "environment_recovery"


class EnvironmentArtifact(StrEnum):
    INVENTORY = "environment_inventory"
    MODE_CANDIDATES = "artifact_mode_candidates"
    EXPERIMENT_PLAN = "environment_experiment_plan"
    RECOVERY_ATTEMPT = "environment_recovery_attempt"
    MODE_RECORD = "artifact_mode_record"
    EXPERIMENT_READY_RUN = "experiment_ready_run"
    EXPERIMENT_ROUTE = "experiment_route"


class ExperimentRouteMilestone(StrEnum):
    READY = "EXPERIMENT_READY"


class RouteDiscoveryStatus(StrEnum):
    DISCOVERED_NOT_EXECUTED = "DISCOVERED_NOT_EXECUTED"
