"""Controller liveness is separate from durable stage progress."""

import fcntl
import json
import os
import signal
import threading
from contextlib import contextmanager

from ..core.models import WorkflowError, utc_now


def controller_status(project):
    path = project.control / "controller.lock"
    if not path.exists():
        return {"state": "UNTRACKED", "note": "Stage RUNNING does not prove a live controller."}
    with path.open("r") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"state": "ACTIVE"}
        return {
            "state": "STOPPED",
            "note": "No controller owns this run; inspect errors before resume.",
        }


@contextmanager
def controller_run(project):
    with (project.control / "controller.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise WorkflowError("project controller already running; refusing duplicate") from error
        path = project.control / "controller.json"
        record = {"pid": os.getpid(), "started_at": utc_now(), "state": "ACTIVE"}

        def save():
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record))
            temporary.replace(path)

        save()
        old_handler = None
        if threading.current_thread() is threading.main_thread():

            def interrupt(signum, frame):
                raise KeyboardInterrupt("controller termination requested")

            old_handler = signal.signal(signal.SIGTERM, interrupt)
        try:
            yield
        except BaseException as error:
            record["error"] = f"{type(error).__name__}: {error}"
            raise
        finally:
            if old_handler is not None:
                signal.signal(signal.SIGTERM, old_handler)
            record.update(state="STOPPED", completed_at=utc_now())
            save()
