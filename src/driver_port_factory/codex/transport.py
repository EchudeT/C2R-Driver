"""Stream CLI checkpoints so an interrupted controller can resume its conversation."""

from __future__ import annotations

import json
import os
import signal
import selectors
import time
import codecs
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError


def execute(
    command: list[str], *, cwd: Path, prompt: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    idle_timeout_seconds: float = 3600,
    timeout_seconds: float = 21600,
) -> subprocess.CompletedProcess[str]:
    # File-backed stdin handles large prompts without blocking on pipe capacity;
    # stderr must not share the stdout pipe or a verbose CLI could deadlock.
    with tempfile.TemporaryFile(mode="w+") as stdin, tempfile.TemporaryFile(mode="w+b") as stderr:
        stdin.write(prompt)
        stdin.seek(0)
        process = subprocess.Popen(
            command, cwd=cwd, stdin=stdin, stdout=subprocess.PIPE,
            stderr=stderr, text=True, start_new_session=True,
        )
        lines: list[str] = []
        try:
            assert process.stdout is not None
            for line in _lines(process, idle_timeout_seconds, timeout_seconds):
                lines.append(line)
                if on_event:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        event = {"type": "unparsed.stdout", "text": line}
                    if isinstance(event, dict):
                        on_event(event)
            code = process.wait()
        except BaseException:
            # Only terminate our own process group, including any owned tool children.
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
            raise
        finally:
            if process.stdout:
                process.stdout.close()
        stderr.seek(0, os.SEEK_END)
        stderr_bytes = stderr.tell()
        stderr.seek(max(0, stderr_bytes - 8000))
        stderr_tail = stderr.read().decode("utf-8", errors="replace")
        if on_event and stderr_tail:
            on_event({"type": "transport.stderr", "text": stderr_tail,
                      "bytes_total": stderr_bytes, "tail_only": stderr_bytes > 8000})
        return subprocess.CompletedProcess(
            command, code, "".join(lines), stderr_tail
        )


def _lines(process: subprocess.Popen, idle: float, total: float):
    """Bound silent provider hangs without buffering partial JSON indefinitely."""
    started = last_output = time.monotonic()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    pending = ""
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            now = time.monotonic()
            remaining = min(idle - (now - last_output), total - (now - started))
            if remaining <= 0:
                raise WorkflowError(
                    "Codex CLI activity/turn deadline exceeded; owned processes stopped. "
                    "Resume the preserved session after checking provider/tool availability."
                )
            if not selector.select(min(30, remaining)):
                continue
            chunk = os.read(process.stdout.fileno(), 65536)
            if not chunk:
                pending += decoder.decode(b"", final=True)
                if pending:
                    yield pending
                # EOF is not activity or proof of exit: preserve BOTH deadlines.
                now = time.monotonic()
                remaining = min(idle - (now - last_output), total - (now - started))
                try:
                    process.wait(timeout=max(0, remaining))
                except subprocess.TimeoutExpired as error:
                    raise WorkflowError(
                        "Codex CLI activity/turn deadline exceeded; owned processes stopped. "
                        "Resume the preserved session after checking provider/tool availability."
                    ) from error
                return
            last_output = time.monotonic()
            pending += decoder.decode(chunk)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                yield line + "\n"
