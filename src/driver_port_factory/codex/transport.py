"""Stream CLI checkpoints so an interrupted controller can resume its conversation."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


def execute(
    command: list[str], *, cwd: Path, prompt: str,
    on_event: Callable[[dict[str, Any]], None] | None = None,
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
            for line in process.stdout:
                lines.append(line)
                if on_event:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
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
        stderr.seek(max(0, stderr.tell() - 8000))
        return subprocess.CompletedProcess(
            command, code, "".join(lines), stderr.read().decode("utf-8", errors="replace")
        )
