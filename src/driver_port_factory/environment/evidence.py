from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ..acquisition.models import CheckoutRecord, RepositoryRole
from ..core.models import WorkflowError
from ..core.project import Project
from .models import WorkspaceEntryKind


def checkout_for(checkouts: tuple[CheckoutRecord, ...], role: RepositoryRole) -> CheckoutRecord:
    matches = [record for record in checkouts if record.role is role]
    if len(matches) != 1:
        raise WorkflowError(f"expected exactly one {role.value} checkout")
    return matches[0]


def workspace_path(project: Project, relative: str) -> Path:
    path = (project.root / relative).resolve()
    if path != project.root and project.root not in path.parents:
        raise WorkflowError(f"path escapes project workspace: {relative}")
    return path


def file_identity(project: Project, relative: str) -> dict[str, Any]:
    path = workspace_path(project, relative)
    if path.is_file():
        return {
            "path": relative,
            "kind": WorkspaceEntryKind.FILE,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
    return {
        "path": relative,
        "kind": WorkspaceEntryKind.DIRECTORY,
        "sha256": None,
        "size": None,
    }


def executable_identity(command: str, cwd: Path) -> dict[str, Any]:
    candidate = Path(command)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    elif "/" in command:
        resolved = (cwd / candidate).resolve()
    else:
        located = shutil.which(command)
        resolved = Path(located).resolve() if located else None
    if resolved is None or not resolved.is_file():
        return {"requested": command, "resolved": None, "sha256": None}
    return {
        "requested": command,
        "resolved": str(resolved),
        "sha256": hashlib.sha256(resolved.read_bytes()).hexdigest(),
        "size": resolved.stat().st_size,
    }
