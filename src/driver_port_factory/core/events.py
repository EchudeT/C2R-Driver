from enum import StrEnum


class RunEvent(StrEnum):
    RECOVERY_RESUMED = "run.recovery_resumed"
    CHECKER_DECISION = "run.checker_decision"
    CREATED = "run.created"
    TASK_REUSE = "run.task_reuse"
    REPAIR_PREPARED = "run.repair_prepared"
    WORKER_SUBMISSION = "run.worker_submission"


class StageEvent(StrEnum):
    READY = "stage.ready"
    STARTED = "stage.started"
    RETRIED = "stage.retried"
    WAITING_FOR_USER = "stage.waiting_for_user"
    RESUMED_AFTER_USER = "stage.resumed_after_user"
    COMPLETED = "stage.completed"


class ArtifactEvent(StrEnum):
    REGISTERED = "artifact.registered"
    BUNDLE_REGISTERED = "artifacts.registered"
