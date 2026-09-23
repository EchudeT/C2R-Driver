from enum import StrEnum

from ..core.models import WorkflowError


class CodexOutputError(WorkflowError):
    """A model response failed its deterministic output/proposal gate."""


class ModelInvocationError(WorkflowError):
    """The worker could not be invoked; do not recursively call it for recovery."""


class CodexContinuation(Exception):
    """New controller evidence for the same worker, not a failed model answer."""


class CodexArtifact(StrEnum):
    PROMPT = "codex_prompt"
    JOB_RESULT = "codex_job_result"
    WORK_REPORT = "codex_work_report"
    SUBMISSION = "codex_submission"
    EVENT_LOG = "codex_event_log"


class CodexBackend(StrEnum):
    EXEC = "exec"


class CodexSandbox(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    UNRESTRICTED = "danger-full-access"


class CodexExecEventType(StrEnum):
    THREAD_STARTED = "thread.started"
    ITEM_COMPLETED = "item.completed"
    ERROR = "error"


class CodexExecItemType(StrEnum):
    AGENT_MESSAGE = "agent_message"
