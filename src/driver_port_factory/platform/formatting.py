"""Explicit package formatting in the verified toolchain; never runtime evidence."""

import json
import uuid
from dataclasses import asdict

from ..core.models import WorkflowError
from ..migration.implementation import worktree_files
from . import executor, service
from .profile import digest


def run(project, packages, *, write=False):
    if (
        not isinstance(packages, list)
        or not packages
        or any(not isinstance(p, str) or not p.strip() or p.startswith("-") for p in packages)
        or type(write) is not bool
    ):
        raise WorkflowError("Format needs explicit Cargo package names and a boolean write flag")
    service.active(project)
    service.verified(project)
    with service.locked(project) as directory:
        profile, worktree = service.load(project)
        service.check_image(project, profile)
        before = worktree_files(worktree, profile["target_revision"])
        argv = ["cargo", "fmt"]
        for package in dict.fromkeys(packages):
            argv.extend(["--package", package])
        if not write:
            argv.extend(["--", "--check"])
        attempt = directory / "runs" / uuid.uuid4().hex
        attempt.mkdir(parents=True)
        result = executor.container(
            profile,
            worktree,
            attempt,
            argv,
            build=True,
            cache_key=profile["cache_key"],
            timeout=120,
        )
        after = worktree_files(worktree, profile["target_revision"])
        ok = result.launched and not result.timed_out and result.exit_code == 0
        value = {
            "status": "FORMAT_OK" if ok and (write or before == after) else "FORMAT_FAILED",
            "scope": "formatting only; no build or runtime acceptance",
            "profile": digest(profile),
            "write": write,
            "before": before,
            "after": after,
            "command": asdict(result),
        }
        receipt = attempt / "format.json"
        receipt.write_text(json.dumps(value, indent=2) + "\n")
        service.check_image(project, profile)
        if value["status"] != "FORMAT_OK":
            raise WorkflowError(
                f"Format failed: {receipt}; "
                f"stdout={result.stdout_path}; stderr={result.stderr_path}"
            )
        return {
            "status": value["status"],
            "receipt": str(receipt),
            "source_changed": before != after,
        }
