from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.contracts import StageKey
from ..core.models import ActorRole, WorkflowError
from .contracts import CodexExecEventType, CodexExecItemType, CodexSandbox


@dataclass(frozen=True, slots=True)
class CodexJob:
    stage: StageKey
    actor_role: ActorRole
    objective: str
    prompt: str
    execution_root: Path
    sandbox: CodexSandbox
    output_schema: Path | None = None
    model: str | None = None
    thread_id: str | None = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True)
class CodexResult:
    job_id: str
    final_response: str
    thread_id: str | None
    events: tuple[dict[str, Any], ...] = ()


class CodexExecGateway:
    """Structured subprocess gateway for `codex exec`."""

    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin

    def run(self, job: CodexJob) -> CodexResult:
        execution_root = job.execution_root.resolve()
        if not execution_root.is_dir():
            raise WorkflowError(f"Codex execution root does not exist: {execution_root}")
        if job.thread_id:
            command = [self.codex_bin, "exec", "resume", "--json"]
        else:
            command = [
                self.codex_bin,
                "exec",
                "--json",
                "--sandbox",
                job.sandbox.value,
            ]
        if job.model:
            command.extend(["--model", job.model])
        if job.output_schema:
            command.extend(["--output-schema", str(job.output_schema.resolve())])
        if job.thread_id:
            command.append(job.thread_id)
        command.append(job.prompt)
        completed = subprocess.run(
            command,
            cwd=execution_root,
            text=True,
            capture_output=True,
            check=False,
        )
        events: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                events.append({"type": "unparsed.stdout", "text": line})
        if completed.returncode != 0:
            event_error = next(
                (
                    event.get("message")
                    for event in reversed(events)
                    if event.get("type") == CodexExecEventType.ERROR.value
                ),
                None,
            )
            detail = event_error or completed.stderr.strip() or completed.stdout.strip()
            raise WorkflowError(
                f"codex exec failed with exit {completed.returncode}: {detail}"
            )
        thread_id = next(
            (
                event.get("thread_id")
                for event in events
                if event.get("type") == CodexExecEventType.THREAD_STARTED.value
            ),
            None,
        ) or job.thread_id
        final_response = next(
            (
                event["item"].get("text", "")
                for event in reversed(events)
                if event.get("type") == CodexExecEventType.ITEM_COMPLETED.value
                and event.get("item", {}).get("type") == CodexExecItemType.AGENT_MESSAGE.value
            ),
            "",
        )
        return CodexResult(job.job_id, final_response, thread_id, tuple(events))


class CodexSdkGateway:
    """Python SDK gateway. Import is deferred so the base package has no SDK dependency."""

    def run(self, job: CodexJob) -> CodexResult:
        try:
            from openai_codex import Codex, Sandbox
        except ImportError as error:
            raise WorkflowError(
                "Python Codex SDK is not installed; install driver-port-factory[codex]"
            ) from error
        sandbox_names = {
            CodexSandbox.READ_ONLY: Sandbox.read_only,
            CodexSandbox.WORKSPACE_WRITE: Sandbox.workspace_write,
        }
        try:
            sandbox = sandbox_names[job.sandbox]
        except KeyError as error:
            raise WorkflowError(f"unsupported Codex sandbox: {job.sandbox.value}") from error
        options: dict[str, Any] = {
            "sandbox": sandbox,
            "cwd": str(job.execution_root.resolve()),
        }
        if job.model:
            options["model"] = job.model
        with Codex() as codex:
            thread = codex.thread_start(**options)
            result = thread.run(job.prompt)
            thread_id = getattr(thread, "id", None) or getattr(thread, "thread_id", None)
            return CodexResult(job.job_id, result.final_response, thread_id)
