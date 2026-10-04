"""Opt-in integration of the shared native suite with the existing platform interface."""

import json
import os
import shlex
from pathlib import Path

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .native_runner import BUILD, runtime_identity, setup
from .native_runner import verify_inputs as check_inputs


def read(path):
    return json.loads(Path(path).read_text())


def binding(project):
    path = project.control / "native-suite.json"
    return read(path) if path.is_file() else None


def configure(project, worktree, profile):
    root = os.environ.get("DPF_NATIVE_SUITE")
    frozen = binding(project)
    if root is None and frozen is None:
        if (worktree / ".native-task.json").exists():
            raise WorkflowError("Native seed requires the operator-selected DPF_NATIVE_SUITE")
        return
    root = Path(root or frozen["root"]).resolve()
    name = project.config.driver_name
    environment = read(root / "environment.json")
    if environment.get("runtime_identity") != runtime_identity():
        raise WorkflowError("DPF runtime differs from the frozen shared experiment runner")
    seed = read(root / "seeds" / name / "task.json")
    if (
        profile["image_id"] != environment["image_id"]
        or profile["accelerator"] != environment["accelerator"]
        or profile["target_revision"] != seed["target_root_commit"]
    ):
        raise WorkflowError("Native suite requires its published seed, fixed image and accelerator")
    value = {
        "root": str(root),
        "driver": name,
        "environment": environment,
        "task": read(root / "config.json")["drivers"][name],
    }
    if frozen is not None and frozen != value:
        raise WorkflowError("Native suite differs from the operator-selected environment")
    profile.update(native=value, build_argv=list(BUILD), build_network="bridge")
    (project.control / "native-suite.json").write_text(json.dumps(value, indent=2) + "\n")
    setup(root, worktree)
    verify_inputs(root, worktree)


def verify_inputs(root, target):
    try:
        check_inputs(root, target)
    except ValueError as error:
        raise WorkflowError(str(error)) from error


def case_names(task):
    return [*task["tests"], *task["benchmarks"]]


def definition(project):
    from .service import command

    config = binding(project)
    directory = ".dpf-output/harness/public"
    files = {
        f"{directory}/INTERFACE.md": (
            "# Unchanged upstream native tests\n\n" + config["task"]["scope"] + "\n\n"
            "Implement and register the driver through the retained target APIs. The controller "
            "builds one ISO and runs the complete original test set on it. "
            "Do not edit test sources, "
            "assertions, boot profile, or test wrappers. Device-unavailable skips do not pass.\n"
        )
    }
    rows = []
    for index, case in enumerate(case_names(config["task"]), 1):
        name = f"native-{config['driver']}-{index}"
        path = f"{directory}/{name}"
        files[path + ".json"] = json.dumps({"native_case": case}, indent=2) + "\n"
        files[path + ".sh"] = (
            "#!/bin/sh\nset -eu\nexec "
            + shlex.join(command(project, "run-case") + [path + ".json"])
            + "\n"
        )
        rows.append({"id": name, "script": path + ".sh", "timeout_seconds": 240})
    return files, rows


def validate_case(profile, case):
    if not isinstance(case, dict) or set(case) != {"native_case"}:
        raise WorkflowError("Use the installed native case files for this fixed test suite")
    name = case["native_case"]
    if name != "boot" and name not in case_names(profile["native"]["task"]):
        raise WorkflowError("Case is outside the frozen upstream native suite")
    return name


def boot(profile, worktree, directory, artifact, case, cache_key):
    from dataclasses import asdict

    from . import executor

    root = Path(profile["native"]["root"])
    name = validate_case(profile, case)
    verify_inputs(root, worktree)
    setup(root, worktree)
    directory.mkdir(parents=True, exist_ok=True)
    before = file_sha256(artifact)
    result = executor.container(
        profile,
        worktree,
        directory,
        ["python3", "/dpf-runner/runtime.py", name, str(artifact), str(directory)],
        build=False,
        cache_key=cache_key,
        timeout=200,
        artifact=artifact,
    )
    path = directory / "result.json"
    value = read(path) if path.is_file() else {"status": "ERROR", "error": "No runtime result"}
    value.update(container_command=asdict(result), artifact_sha256=before)
    if result.exit_code or result.timed_out:
        value["status"] = "FAIL"
    verify_inputs(root, worktree)
    if file_sha256(artifact) != before:
        raise WorkflowError("Native artifact changed during the runtime check")
    (directory / "boot.json").write_text(json.dumps(value, indent=2) + "\n")
    return value
