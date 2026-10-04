"""Bounded guest-only replay of a recorded direct Docker/OSDK invocation.

External harness peers are not replayed. Samples diagnose this new execution,
never the historical timeout or functional benchmark acceptance.
"""

import fcntl
import json
import shutil
import subprocess
import tomllib
import uuid
from dataclasses import asdict
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..core.execution import CommandRunner
from ..core.models import WorkflowError
from ..core.trace import exec_arguments, successful_execs
from ..knowledge.index import file_sha256
from .experiment_ack import _job
from .implementation import worktree_files


def tool():
    return {
        "name": "debug",
        "description": "Guest-only diagnostic replay of the latest recorded direct Docker cargo osdk run/test. "
        "Capture two bounded CPU stacks/register samples. External peers are not replayed; "
        "this is not the historical timeout or acceptance. Unsupported routes return an error. "
        "No environment fallback, concurrent edit/build, or automatic deadlock verdict.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "capture_after_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 120,
                    "default": 30,
                }
            },
        },
    }


def _record(project):
    receipts = sorted(
        (project.control / "experiments").glob("*/receipt.json"),
        key=lambda p: p.stat().st_mtime_ns,
        reverse=True,
    )
    if not receipts:
        raise WorkflowError("Run a managed runtime check before requesting guest diagnostics")
    path = receipts[0]
    value = json.loads(path.read_text())
    for name, digest in value["evidence"].items():
        if not Path(name).is_file() or file_sha256(Path(name)) != digest:
            raise WorkflowError("Recorded runtime evidence changed")
    trace = Path(value["result"]["trace_path"])
    observations = json.loads((trace.parent / "container-processes.json").read_text())[
        "observations"
    ]
    candidates = []
    for executable, line in successful_execs(trace.read_text().splitlines()):
        argv = exec_arguments(line)
        if Path(executable).name != "docker" or len(argv) < 3 or argv[1] != "run":
            continue
        for observation in observations:
            image = observation["image"]
            if image not in argv:
                continue
            index = argv.index(image)
            command = argv[index + 1 :]
            if (
                len(command) >= 3
                and command[:2] == ["cargo", "osdk"]
                and command[2] in {"run", "test"}
            ):
                candidates.append((argv, index, observation, command[2]))
    unique = {json.dumps(c[:2]): c for c in candidates}
    if len(unique) != 1:
        raise WorkflowError(
            "Need one recorded direct docker run IMAGE cargo osdk run/test command; shell/custom routes unsupported"
        )
    return path, value, next(iter(unique.values()))


def _bundle(worktree, action, observation):
    roots = [
        m["Destination"]
        for m in observation["mounts"]
        if Path(m["Source"]).resolve() == worktree.resolve()
    ]
    if len(roots) != 1:
        raise WorkflowError("Diagnostic needs one observed target worktree mount")
    matches = []
    for path in (worktree / "target/osdk").glob("*/bundle.toml"):
        value = tomllib.loads(path.read_text())
        if (
            value.get("action", "").lower() == action
            and value.get("config", {}).get("work_dir") == roots[0]
        ):
            matches.append((path, value))
    if len(matches) != 1:
        raise WorkflowError("Need one matching OSDK bundle with source-level symbols")
    path, value = matches[0]
    elf = (path.parent / value["aster_bin"]["path"]).resolve()
    if value["aster_bin"].get("stripped") or not elf.is_relative_to(worktree) or not elf.is_file():
        raise WorkflowError("Diagnostic bundle has no controlled unstripped ELF")
    qemu = value["config"][action]["qemu"]["path"]
    if "/dpf-debug/" in qemu:
        raise WorkflowError("Run a normal runtime check before another diagnostic replay")
    return elf, str(Path(roots[0]) / elf.relative_to(worktree)), qemu


def run(project, job_id, arguments):
    _job(project, job_id)
    if not isinstance(arguments, dict) or set(arguments) - {"capture_after_seconds"}:
        raise WorkflowError("Use capture_after_seconds only")
    delay = arguments.get("capture_after_seconds", 30)
    if type(delay) is not int or not 1 <= delay <= 120:
        raise WorkflowError("Capture delay must be an integer between 1 and 120")
    root = project.control / "experiments"
    root.mkdir(exist_ok=True)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return _run(project, delay)


def _run(project, delay):
    receipt, previous, (argv, image_index, observation, action) = _record(project)
    target = load_repository_acquisition(project).target_worktree
    worktree = (project.root / target.path).resolve()
    before = worktree_files(worktree, target.base_commit)
    if previous["identity"]["source"] != before:
        raise WorkflowError(
            "Source changed since the last runtime check; rebuild/check before diagnostic replay"
        )
    elf, guest_elf, qemu = _bundle(worktree, action, observation)
    if any(a.startswith(("--qemu-exe", "--cidfile", "--name=")) for a in argv):
        raise WorkflowError("Existing diagnostic/custom container naming options are unsupported")
    from . import qemu_capture

    directory = project.control / "debug" / uuid.uuid4().hex
    directory.mkdir(parents=True)
    shutil.copyfile(qemu_capture.__file__, directory / "qemu_capture.py")
    (directory / "qemu_capture.py").chmod(0o755)
    mount = "/opt/dpf-debug"
    config = {"qemu": qemu, "elf": guest_elf, "output": mount, "capture_after_seconds": delay}
    (directory / "config.json").write_text(json.dumps(config))
    prefix = argv[:image_index]
    if "--name" in prefix:
        index = prefix.index("--name")
        del prefix[index : index + 2]
    name = "dpf-debug-" + directory.name
    command = [
        *prefix,
        "--name",
        name,
        "--mount",
        f"type=bind,src={directory},dst={mount}",
        observation["image_id"],
        *argv[image_index + 1 :],
        "--qemu-exe",
        mount + "/qemu_capture.py",
    ]
    # A diagnostic disturbs runtime state: invalidate execution/smoke reuse before launch.
    (project.control / "diagnostic-epoch").write_text(directory.name)
    elf_digest = file_sha256(elf)
    try:
        observed = CommandRunner(directory / "command").run(
            command,
            cwd=worktree,
            environment=previous["identity"].get("environment", {}),
            timeout_seconds=previous["identity"]["timeout"],
        )
    finally:
        # Docker CLI cancellation alone may leave its daemon-owned container running.
        subprocess.run([argv[0], "rm", "-f", name], capture_output=True, timeout=15, check=False)
    capture = directory / "capture.json"
    value = {
        "diagnostic_only": True,
        "prior_receipt": {"path": str(receipt), "sha256": file_sha256(receipt)},
        "elf_sha256_before": elf_digest,
        "source": before,
        "inputs_unchanged": worktree_files(worktree, target.base_commit) == before,
        "command": asdict(observed),
        "capture": json.loads(capture.read_text()) if capture.is_file() else None,
        "limits": "Guest-only replay; external stimuli not replayed; sampling changes timing; no acceptance",
    }
    (directory / "receipt.json").write_text(json.dumps(value, indent=2) + "\n")
    return json.dumps(
        {
            "receipt": str(directory / "receipt.json"),
            "capture": str(capture),
            "logs": [str(p) for p in directory.glob("sample-*.log")],
            "diagnostic_only": True,
            "limits": value["limits"],
        },
        indent=2,
    )
