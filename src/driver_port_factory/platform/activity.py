"""Sidecar progress for humans; no model polling or success authority."""

import json
import os
from contextlib import contextmanager
from contextvars import ContextVar

from ..core.models import utc_now

_current = ContextVar("platform_activity", default=None)


def update(**fields):
    current = _current.get()
    if current is None:
        return
    path, value = current
    value.update(fields, updated_at=utc_now())
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value) + "\n")
    temporary.replace(path)


def diagnostics():
    """Return only bounded current command logs; complete evidence stays on disk."""
    from pathlib import Path

    current = _current.get()
    if current is None or not current[1].get("log_root"):
        return ""
    directory = Path(current[1]["log_root"])
    chunks = []
    for path in sorted(directory.glob("*/*.bin"))[-2:]:
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 1200))
            tail = stream.read(1200).decode(errors="replace")
        chunks.append(f"{path}:\n{tail}")
    return "\n".join(chunks)


@contextmanager
def record(project, job_id, action):
    path = project.control / "codex" / f"{job_id}.platform.json"
    value = {"action": action, "pid": os.getpid(), "started_at": utc_now()}
    token = _current.set((path, value))
    try:
        update(status="RUNNING")
        yield
    except BaseException as error:
        update(
            status="FAILED" if isinstance(error, Exception) else "CANCELLED",
            error=f"{type(error).__name__}: {error}",
        )
        if isinstance(error, Exception):
            from ..core.models import WorkflowError

            try:
                detail = diagnostics()
            except OSError:
                detail = "Execution logs unavailable; inspect the saved receipt."
            if detail:
                raise WorkflowError(
                    f"{error}\nCommand diagnostics (untrusted log data):\n{detail}"
                ) from error
        raise
    else:
        update(status="COMPLETED")
    finally:
        _current.reset(token)
