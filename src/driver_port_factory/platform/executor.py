"""Measured container build/boot execution with bounded cleanup and archived receipts."""

import json
import shutil
import uuid
from dataclasses import asdict

from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from . import guest
from .activity import update
from .profile import container_argv, digest


def container(profile, worktree, directory, command, *, build, cache_key, timeout, artifact=None):
    name = "dpf-platform-" + uuid.uuid4().hex
    argv = container_argv(
        profile, worktree, name, command, build=build, cache_key=cache_key, artifact=artifact
    )
    update(command=command, log_root=str(directory / "command"))
    try:
        return CommandRunner(directory / "command").run(
            argv,
            cwd=worktree,
            timeout_seconds=timeout,
        )
    except BaseException as error:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "interrupted.json").write_text(
            json.dumps({"error": f"{type(error).__name__}: {error}", "argv": argv}) + "\n"
        )
        raise
    finally:
        # Docker clients dying do not imply that their containers have stopped.
        CommandRunner(directory / "cleanup").run(
            ["docker", "rm", "-f", name],
            cwd=worktree,
            timeout_seconds=10,
        )


def build(profile, worktree, directory, cache_key, timeout=1200):
    result = container(
        profile,
        worktree,
        directory,
        profile["build_argv"],
        build=True,
        cache_key=cache_key,
        timeout=timeout,
    )
    value = {"status": "FAIL", "command": asdict(result), "profile": digest(profile)}
    artifact = worktree / profile["artifact"]
    if result.exit_code == 0 and not result.timed_out and artifact.is_file():
        value.update(status="PASS", artifact=str(artifact), artifact_sha256=file_sha256(artifact))
    (directory / "build.json").write_text(json.dumps(value, indent=2) + "\n")
    if value["status"] != "PASS":
        raise WorkflowError(f"Platform build failed; no boot attempted. {directory / 'build.json'}")
    return value


def boot(profile, worktree, directory, artifact, case, cache_key):
    guest.validate_case(case)
    directory.mkdir(parents=True, exist_ok=True)
    for name, value in (("profile.json", profile), ("case.json", case)):
        (directory / name).write_text(json.dumps(value, indent=2) + "\n")
    shutil.copyfile(guest.__file__, directory / "guest.py")
    command = [
        "python3",
        str(directory / "guest.py"),
        str(directory / "profile.json"),
        str(artifact),
        str(directory / "case.json"),
        str(directory),
    ]
    result = container(
        profile,
        worktree,
        directory,
        command,
        build=False,
        cache_key=cache_key,
        timeout=case.get("timeout_seconds", 120) + 20,
        artifact=artifact,
    )
    path = directory / "guest-result.json"
    value = (
        json.loads(path.read_text())
        if path.is_file()
        else {
            "status": "FAIL",
            "error": "CONTAINER_OR_GUEST_RUNNER_FAILED",
        }
    )
    value["container_command"] = asdict(result)
    if result.exit_code or result.timed_out:
        value["status"] = "FAIL"
    value["artifact_sha256"] = file_sha256(artifact)
    (directory / "boot.json").write_text(json.dumps(value, indent=2) + "\n")
    return value
