"""Resolve workspace dependencies before freezing build inputs, without network fallback."""

import json
import shutil
from dataclasses import asdict

from ..core.models import WorkflowError
from ..migration.implementation import worktree_files
from . import executor
from .profile import digest


def prepare(profile, worktree, directory):
    if not (worktree / "Cargo.toml").is_file():
        raise WorkflowError("Managed Asterinas build requires a workspace Cargo.toml")
    directory.mkdir(parents=True)
    receipt = directory / "dependencies.json"
    before = worktree_files(worktree, profile["target_revision"])
    value = {"status": "FAIL", "profile": digest(profile), "before": before}
    lock = worktree / "Cargo.lock"
    if lock.is_file():
        shutil.copyfile(lock, directory / "Cargo.lock.before")
    try:
        result = executor.container(
            profile,
            worktree,
            directory,
            ["cargo", "metadata", "--offline", "--format-version", "1"],
            build=True,
            cache_key=profile["cache_key"],
            timeout=120,
        )
        value["command"] = asdict(result)
        after = worktree_files(worktree, profile["target_revision"])
        value["after"] = after
        if not result.launched or result.exit_code or result.timed_out:
            raise WorkflowError("Offline dependency resolution failed; no build attempted")
        # Only the workspace lock file may change in this explicit preparation step.
        # The subsequent build compares the entire snapshot, including Cargo.lock.
        if [f for f in before if f["path"] != "Cargo.lock"] != [
            f for f in after if f["path"] != "Cargo.lock"
        ]:
            raise WorkflowError("Non-lockfile source changed during dependency resolution")
        value["status"] = "DEPENDENCIES_RESOLVED"
    except BaseException as error:
        value["error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, WorkflowError):
            raise WorkflowError(f"{error}; receipt: {receipt}") from error
        raise
    finally:
        if lock.is_file():
            shutil.copyfile(lock, directory / "Cargo.lock.after")
        receipt.write_text(json.dumps(value, indent=2) + "\n")
    return str(receipt)
