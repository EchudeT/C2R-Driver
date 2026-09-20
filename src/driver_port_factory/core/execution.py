from __future__ import annotations

import hashlib
import json
import os
import shlex
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import utc_now


def script_command(path: Path) -> list[str]:
    """Honor the authored interpreter even when the script is not executable."""
    with path.open(encoding="utf-8") as stream:
        first = stream.readline().strip()
    interpreter = shlex.split(first[2:]) if first.startswith("#!") else ["/bin/bash"]
    if not interpreter:
        raise ValueError(f"empty script interpreter: {path}")
    return [*interpreter, str(path.resolve())]


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: str
    started_at: str
    completed_at: str
    exit_code: int
    launched: bool
    launch_error: str | None
    timed_out: bool
    duration_milliseconds: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_path: str
    stderr_path: str


class CommandRunner:
    """Run commands without a shell and preserve immutable stdout/stderr evidence."""

    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root.resolve()
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: int = 300,
    ) -> CommandResult:
        started_at = utc_now()
        run_material = json.dumps(
            {"argv": list(argv), "cwd": str(cwd.resolve()), "started_at": started_at},
            sort_keys=True,
        ).encode("utf-8")
        run_id = hashlib.sha256(run_material).hexdigest()[:20]
        run_dir = self.runs_root / run_id
        run_dir.mkdir()
        timed_out = False
        launched = False
        launch_error = None
        monotonic_start = time.monotonic()
        stdout_path = run_dir / "stdout.bin"
        stderr_path = run_dir / "stderr.bin"
        try:
            launched = True
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(
                    list(argv),
                    cwd=cwd.resolve(),
                    env={**os.environ, **dict(environment or {})},
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=timeout_seconds)
                except BaseException:
                    # Timeout/cancellation must not leave child builds or QEMU running.
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                    except ProcessLookupError:
                        pass
                    finally:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    raise
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            exit_code = 124
        except OSError as error:
            launched = False
            launch_error = f"{type(error).__name__}: {error}"
            stderr_path.write_text(launch_error + "\n", encoding="utf-8", errors="replace")
            exit_code = 127
        duration_milliseconds = round((time.monotonic() - monotonic_start) * 1000)
        result = CommandResult(
            argv=tuple(argv),
            cwd=str(cwd.resolve()),
            started_at=started_at,
            completed_at=utc_now(),
            exit_code=exit_code,
            launched=launched,
            launch_error=launch_error,
            timed_out=timed_out,
            duration_milliseconds=duration_milliseconds,
            stdout_sha256=self._file_sha256(stdout_path),
            stderr_sha256=self._file_sha256(stderr_path),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
        (run_dir / "result.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return result

    @staticmethod
    def _file_sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
