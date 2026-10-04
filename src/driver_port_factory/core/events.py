from enum import StrEnum


class RunEvent(StrEnum):
    CONTINUATION = "run.continuation"
    SESSION_RESET = "run.session_reset"
    CONTEXT_POLICY = "run.context_policy"
    RECOVERY_RESUMED = "run.recovery_resumed"
    CHECKER_DECISION = "run.checker_decision"
    CREATED = "run.created"
    TASK_REUSE = "run.task_reuse"
    REPAIR_PREPARED = "run.repair_prepared"
    DELIVERY_PREPARED = "run.delivery_prepared"
    ANALYSIS_PREPARED = "run.analysis_prepared"
    WORKER_SUBMISSION = "run.worker_submission"
    MODEL_BUDGET = "run.model_budget"
    ROUTE_REVISION = "run.route_revision"
    ROUTE_PROBE = "run.route_probe"
    ROUTE_LEARN = "run.route_learn"
    PLATFORM_CASE_CAPTURED = "run.platform_case_captured"
    PLATFORM_LOG_CHECKED = "run.platform_log_checked"
    BEHAVIOR_PROGRESS = "run.behavior_progress"
    SHARED_KNOWLEDGE_BOUND = "run.shared_knowledge_bound"
    KNOWLEDGE_FEEDBACK = "run.knowledge_feedback"
    KNOWLEDGE_PUBLISHED = "run.knowledge_published"
    KNOWLEDGE_OFFERED = "run.knowledge_offered"


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
