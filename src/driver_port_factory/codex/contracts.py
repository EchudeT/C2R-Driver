from enum import StrEnum


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


class CodexExecEventType(StrEnum):
    THREAD_STARTED = "thread.started"
    ITEM_COMPLETED = "item.completed"


class CodexExecItemType(StrEnum):
    AGENT_MESSAGE = "agent_message"
