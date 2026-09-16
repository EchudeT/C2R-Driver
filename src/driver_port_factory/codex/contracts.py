from enum import StrEnum

from ..core.models import WorkflowError


class CodexOutputError(WorkflowError):
    """A model response failed its deterministic output/proposal gate."""


class CodexArtifact(StrEnum):
    PROMPT = "codex_prompt"
    JOB_RESULT = "codex_job_result"
    EVENT_LOG = "codex_event_log"


class CodexBackend(StrEnum):
    EXEC = "exec"
    SDK = "sdk"


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
