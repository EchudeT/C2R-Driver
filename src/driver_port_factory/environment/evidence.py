from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..acquisition.frozen_checkout_validation import verify_git_checkout, verify_lock
from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_filesystem import paths_overlap
from ..acquisition.repository_role import RepositoryRole
from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from ..core.project import Project
from .models import WorkspaceEntryKind


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


def freeze_qemu_executable(project: Project, command: str, cwd: Path) -> dict[str, Any]:
    identity = executable_identity(command, cwd)
    resolved = identity["resolved"]
    if resolved is None:
        raise WorkflowError(f"QEMU executable is unavailable: {command}")
    result = CommandRunner(project.control / "command-runs" / "environment-version").run(
        [resolved, "--version"], cwd=cwd, timeout_seconds=30
    )
    stdout = Path(result.stdout_path).read_bytes()
    stderr = Path(result.stderr_path).read_bytes()
    version = (stdout + b"\n" + stderr).decode("utf-8", errors="replace").strip()
    if result.exit_code != 0 or "QEMU" not in version:
        raise WorkflowError("direct-QEMU executable did not produce a QEMU version identity")
    acquisition = load_repository_acquisition(project)
    qemu = acquisition.checkout(RepositoryRole.QEMU)
    return {
        **identity,
        "version": version,
        "version_argv": [resolved, "--version"],
        "version_stdout_sha256": result.stdout_sha256,
        "version_stderr_sha256": result.stderr_sha256,
        "provenance": {
            "kind": "observed-host-executable",
            "qemu_source_url": qemu.source_url,
            "qemu_source_commit": qemu.resolved_commit,
            "qemu_source_tree": qemu.tree_id,
            "qemu_source_lock_sha256": qemu.lock_sha256,
        },
    }


def frozen_repository_snapshot(project: Project) -> dict[str, Any]:
    project.verify_integrity()
    acquisition = load_repository_acquisition(project)
    frozen_paths: list[Path] = []
    repositories = []
    for role in RepositoryRole:
        checkout = acquisition.checkout(role)
        verify_lock(project.root, checkout)
        verify_git_checkout(project.root, checkout)
        checkout_path = (project.root / checkout.checkout_path).resolve()
        frozen_paths.append(checkout_path)
        repositories.append(
            {
                "role": role.value,
                "origin": checkout.source_url,
                "commit": checkout.resolved_commit,
                "tree": checkout.tree_id,
                "checkout_path": checkout.checkout_path,
                "lock_path": checkout.lock_path,
                "lock_sha256": checkout.lock_sha256,
                "clean": True,
            }
        )
    writable_target = (project.root / acquisition.target_worktree.path).resolve()
    if any(paths_overlap(writable_target, path) for path in frozen_paths):
        raise WorkflowError("writable target tree overlaps a frozen repository")
    manifest = project.artifact(
        AcquisitionStage.REPOSITORY_ACQUISITION,
        AcquisitionArtifact.REPOSITORY_MANIFEST,
    )
    return {
        "repository_manifest_sha256": manifest.digest,
        "repositories": repositories,
        "writable_target_path": acquisition.target_worktree.path,
        "writable_target_separate": True,
    }
