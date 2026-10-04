from __future__ import annotations

import json
import uuid
import fcntl
import hashlib
import os
import tempfile
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.contracts import StageKey
from ..core.models import ActorRole, WorkflowError
from .contracts import CodexExecEventType, CodexExecItemType, CodexSandbox
from .policy import CodexExecutionPolicy
from .runtime import relay_overrides
from .transport import execute


@dataclass(frozen=True, slots=True)
class CodexJob:
    stage: StageKey
    actor_role: ActorRole
    objective: str
    prompt: str
    execution_root: Path
    sandbox: CodexSandbox
    model: str | None = None
    thread_id: str | None = None
    job_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    compact_token_limit: int = 224000
    checks_project: Path | None = None
    worker_project: Path | None = None


@dataclass(frozen=True, slots=True)
class CodexResult:
    job_id: str
    final_response: str
    thread_id: str | None
    events: tuple[dict[str, Any], ...] = ()
    error: str | None = None


class CodexExecGateway:
    """Structured subprocess gateway for `codex exec`."""

    def __init__(
        self, codex_bin: str = "codex",
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.codex_bin = codex_bin
        self.on_event = on_event

    def run(self, job: CodexJob) -> CodexResult:
        # A resumed thread has one writer even across controllers or cwd changes.
        identity = job.thread_id or str(job.execution_root.resolve())
        root = Path(tempfile.gettempdir()) / f"dpf-session-locks-{os.getuid()}"
        root.mkdir(mode=0o700, exist_ok=True)
        with (root / hashlib.sha256(identity.encode()).hexdigest()).open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise WorkflowError("conversation already running; do not start another writer") from error
            return self._run(job)

    def _run(self, job: CodexJob) -> CodexResult:
        execution_root = job.execution_root.resolve()
        if not execution_root.is_dir():
            raise WorkflowError(f"Codex execution root does not exist: {execution_root}")
        prefix = [self.codex_bin, "--cd", str(execution_root), "exec"]
        if job.thread_id:
            command = [*prefix, "resume", "--json"]
        else:
            command = [
                *prefix,
                "--json",
                "--sandbox",
                job.sandbox.value,
            ]
        if job.model:
            command.extend(["--model", job.model])
        command.extend(
            [
                "-c",
                f"model_auto_compact_token_limit={job.compact_token_limit}",
                "-c",
                'approval_policy="never"',
                "-c",
                f'sandbox_mode="{job.sandbox.value}"',
            ]
        )
        if job.worker_project is not None:
            variables = {"DPF_WORKER_PROJECT": str(job.worker_project),
                         "DPF_WORKER_JOB": job.job_id, "DPF_WORKER_STAGE": job.stage.value}
            for name, value in variables.items():
                command.extend(["-c", f"shell_environment_policy.set.{name}={json.dumps(value)}"])
        command.extend(["--disable", "apps"])
        command.extend([
            "-c", "sandbox_workspace_write.network_access="
            + ("true" if job.stage in CodexExecutionPolicy.DEPENDENCY_STAGES else "false"),
        ])
        # Preserve the environment that recovery proved usable. An empty forced
        # CARGO_HOME can hide installed cargo subcommands and discard warm caches.
        command.extend(relay_overrides())
        if job.checks_project is not None:
            source_root = str(Path(__file__).resolve().parents[2])
            config = {
                "command": sys.executable,
                "args": ["-m", "driver_port_factory.codex.check_mcp",
                         str(job.checks_project), job.job_id],
                "env": {"PYTHONPATH": os.pathsep.join(filter(None, (
                    source_root, os.environ.get("PYTHONPATH"))))},
                "tool_timeout_sec": 86400,
            }
            for name, value in config.items():
                # TOML inline tables differ from JSON objects.
                encoded = ("{ " + ", ".join(f"{k} = {json.dumps(v)}" for k, v in value.items())
                           + " }" if isinstance(value, dict) else json.dumps(value))
                command.extend(["-c", f"mcp_servers.driver_checks.{name}={encoded}"])
        # Structured data is exchanged through the file-backed submission
        # command.  The final agent message is only an activity transcript;
        # never ask Codex to serialize workflow data into it.
        if job.thread_id:
            command.append(job.thread_id)
        command.append("-")
        completed = execute(
            command, cwd=execution_root, prompt=job.prompt, on_event=self.on_event
        )
        events: list[dict[str, Any]] = []
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    events.append(event)
            except json.JSONDecodeError:
                events.append({"type": "unparsed.stdout", "text": line})
        failure = None
        if completed.returncode != 0:
            event_error = next(
                (
                    event.get("message")
                    for event in reversed(events)
                    if event.get("type") == CodexExecEventType.ERROR.value
                ),
                None,
            )
            detail = (event_error or completed.stderr.strip()[-2000:]
                      or completed.stdout.strip()[-2000:])
            failure = f"codex exec failed with exit {completed.returncode}: {detail}"
        thread_id = (
            next(
                (
                    event.get("thread_id")
                    for event in events
                    if event.get("type") == CodexExecEventType.THREAD_STARTED.value
                ),
                None,
            )
            or job.thread_id
        )
        final_response = next(
            (
                event["item"].get("text", "")
                for event in reversed(events)
                if event.get("type") == CodexExecEventType.ITEM_COMPLETED.value
                and event.get("item", {}).get("type") == CodexExecItemType.AGENT_MESSAGE.value
            ),
            "",
        )
        if not failure and any(event.get("type") == "turn.failed" for event in events):
            failure = "Codex turn failed; inspect preserved event log"
        if not failure and not any(event.get("type") == "turn.completed" for event in events):
            failure = "Codex stream ended without a completed turn; resume the preserved conversation"
        return CodexResult(job.job_id, final_response, thread_id, tuple(events), failure)
