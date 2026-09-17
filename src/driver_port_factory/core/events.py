from enum import StrEnum


class RunEvent(StrEnum):
    CREATED = "run.created"


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
