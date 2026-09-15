from __future__ import annotations

import json
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ..core.models import ActorRole, WorkflowError


@dataclass(frozen=True, slots=True)
class CodexJob:
    stage: str
    actor_role: ActorRole
    objective: str
    prompt: str
    workspace: Path
    output_schema: Path | None = None
    output_path: Path | None = None
    model: str | None = None
    sandbox: str = "workspace-write"
    thread_id: str | None = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass(frozen=True, slots=True)
class CodexResult:
    job_id: str
    final_response: str
    thread_id: str | None
    events: tuple[dict[str, Any], ...] = ()


class CodexGateway(Protocol):
    def run(self, job: CodexJob) -> CodexResult: ...


class CodexExecGateway:
    """Structured subprocess gateway for `codex exec`."""

    def __init__(self, codex_bin: str = "codex") -> None:
        self.codex_bin = codex_bin

    def run(self, job: CodexJob) -> CodexResult:
        workspace = job.workspace.resolve()
        if not workspace.is_dir():
            raise WorkflowError(f"Codex workspace does not exist: {workspace}")
        if job.thread_id:
            raise WorkflowError(
                "CodexExecGateway runs ephemeral jobs; use CodexSdkGateway to resume a thread"
            )
        command = [
            self.codex_bin,
            "exec",
            "--json",
            "--ephemeral",
            "--sandbox",
            job.sandbox,
        ]
        if job.model:
            command.extend(["--model", job.model])
        if job.output_schema:
            command.extend(["--output-schema", str(job.output_schema.resolve())])
        if job.output_path:
            command.extend(["--output-last-message", str(job.output_path.resolve())])
        command.append(job.prompt)
        completed = subprocess.run(
            command,
            cwd=workspace,
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
            raise WorkflowError(
                f"codex exec failed with exit {completed.returncode}: {completed.stderr.strip()}"
            )
        thread_id = next(
            (
                event.get("thread_id")
                for event in events
                if event.get("type") == "thread.started"
            ),
            None,
        )
        if job.output_path and job.output_path.exists():
            final_response = job.output_path.read_text(encoding="utf-8")
        else:
            final_response = next(
                (
                    event["item"].get("text", "")
                    for event in reversed(events)
                    if event.get("type") == "item.completed"
                    and event.get("item", {}).get("type") == "agent_message"
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
            "read-only": Sandbox.read_only,
            "workspace-write": Sandbox.workspace_write,
            "danger-full-access": Sandbox.full_access,
        }
        try:
            sandbox = sandbox_names[job.sandbox]
        except KeyError as error:
            raise WorkflowError(f"unsupported Codex sandbox: {job.sandbox}") from error
        options: dict[str, Any] = {"sandbox": sandbox, "cwd": str(job.workspace.resolve())}
        if job.model:
            options["model"] = job.model
        with Codex() as codex:
            if job.thread_id:
                thread = codex.thread_resume(job.thread_id)
            else:
                thread = codex.thread_start(**options)
            result = thread.run(job.prompt)
            thread_id = getattr(thread, "id", None) or getattr(thread, "thread_id", None)
            return CodexResult(job.job_id, result.final_response, thread_id)


class RecordingGateway:
    """Deterministic gateway used by tests and dry runs."""

    def __init__(self, response: str = "{}") -> None:
        self.response = response
        self.jobs: list[CodexJob] = []

    def run(self, job: CodexJob) -> CodexResult:
        self.jobs.append(job)
        return CodexResult(job.job_id, self.response, f"recording-{job.job_id}")
