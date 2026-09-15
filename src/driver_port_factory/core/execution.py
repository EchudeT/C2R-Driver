from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from .models import utc_now


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
        try:
            launched = True
            completed = subprocess.run(
                list(argv),
                cwd=cwd.resolve(),
                env={**os.environ, **dict(environment or {})},
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
            )
            stdout = completed.stdout
            stderr = completed.stderr
            exit_code = completed.returncode
        except subprocess.TimeoutExpired as error:
            timed_out = True
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            exit_code = 124
        except OSError as error:
            launched = False
            launch_error = f"{type(error).__name__}: {error}"
            stdout = b""
            stderr = (launch_error + "\n").encode("utf-8", errors="replace")
            exit_code = 127
        duration_milliseconds = round((time.monotonic() - monotonic_start) * 1000)
        stdout_path = run_dir / "stdout.bin"
        stderr_path = run_dir / "stderr.bin"
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
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
            stdout_sha256=hashlib.sha256(stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(stderr).hexdigest(),
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
        )
        (run_dir / "result.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        return result
