from __future__ import annotations

import subprocess
from pathlib import Path

from ..core.models import WorkflowError


def git_output(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ("git", *arguments),
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise WorkflowError(f"Git identity verification failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def git_bytes(root: Path, *arguments: str) -> bytes:
    completed = subprocess.run(("git", *arguments), cwd=root, check=False, capture_output=True)
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        raise WorkflowError(f"Git blob verification failed: {stderr.strip()}")
    return completed.stdout


def workspace_file(root: Path, value: object, label: str) -> Path:
    path = workspace_path(root, value, label)
    if not path.is_file():
        raise WorkflowError(f"{label} is not a file: {value}")
    return path


def workspace_directory(root: Path, value: object, label: str) -> Path:
    path = workspace_path(root, value, label)
    if not path.is_dir():
        raise WorkflowError(f"{label} is not a directory: {value}")
    return path


def workspace_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise WorkflowError(f"{label} path must be non-empty")
    resolved_root = root.resolve()
    path = (resolved_root / value).resolve()
    if path != resolved_root and resolved_root not in path.parents:
        raise WorkflowError(f"{label} escapes the project workspace")
    return path


def paths_overlap(first: Path, second: Path) -> bool:
    return first == second or first in second.parents or second in first.parents
