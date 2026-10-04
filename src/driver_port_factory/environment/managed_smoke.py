"""Controller-owned container lifetime and descendant exec capture, without polling.

The probe is an untrusted test, not a verdict. This provides execution provenance
for cooperative workers, not isolation from a malicious privileged probe.
"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from ..core.execution import CommandResult, CommandRunner
from ..core.trace import exec_arguments, qemu_experiment, successful_execs

# Trace files live in the container layer, outside the workspace bind mount.
# No event subscription, top snapshot, sleep, or host-QEMU fallback is used.
_ENTRYPOINT = """set -eu
mkdir -p "$1"
strace -f -qq -s 65535 -e trace=execve -o "$1/collector.log" /bin/true
exec strace -f -qq -s 65535 -e trace=execve -o "$1/execve.log" /bin/bash "$2"
"""


@dataclass
class Capture:
    command: CommandResult
    executions: tuple
    output: Path
    collector: dict
    infrastructure_errors: list[str]


class _Run:
    def __init__(self, workspace, directory, recipe):
        self.workspace = workspace.resolve()
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.recipe = recipe
        self.runner = CommandRunner(directory / "commands")
        self.commands = []
        self.errors = []
        self.identifier = None
        self.name = "dpf-capture-" + uuid.uuid4().hex
        self.trace_dir = "/tmp/" + self.name
        self.info = None
        self.collector = {"collector": "container-strace", "available": False}
        self.executions = ()
        self.result = None

    def call(self, *args, timeout=15):
        result = self.runner.run(["docker", *args], cwd=self.workspace, timeout_seconds=timeout)
        self.commands.append(asdict(result))
        return result

    def checked(self, *args, timeout=15):
        result = self.call(*args, timeout=timeout)
        self.result = result
        if not result.launched or result.timed_out or result.exit_code:
            raise RuntimeError(f"docker {args[0]} failed; stderr: {result.stderr_path}")
        return Path(result.stdout_path).read_text()

    def create(self, probe):
        actual = self.checked("image", "inspect", "--format", "{{.Id}}", self.recipe["image"])
        if actual.strip() != self.recipe["image_id"]:
            raise RuntimeError("Selected image identity changed before execution")
        devices = ["--device", "/dev/kvm"] if self.recipe.get("accelerator") == "kvm" else []
        identifier = self.checked(
            "create",
            "--pull=never",
            "--network",
            "none",
            "--name",
            self.name,
            "--entrypoint",
            "/bin/sh",
            "-v",
            f"{self.workspace}:{self.workspace}",
            "-w",
            str(self.workspace),
            *devices,
            self.recipe["image_id"],
            "-c",
            _ENTRYPOINT,
            "dpf-capture",
            self.trace_dir,
            str(probe.resolve()),
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise RuntimeError("Docker create did not return a container identity")
        self.identifier = identifier
        self.info = json.loads(self.checked("inspect", identifier))[0]
        if self.info["Id"] != identifier or self.info["Image"] != self.recipe["image_id"]:
            raise RuntimeError("Created container identity does not match the prepared image")
        mounts = self.info.get("Mounts", [])
        if not any(
            m.get("Type") == "bind"
            and m.get("Source") == str(self.workspace)
            and m.get("Destination") == str(self.workspace)
            for m in mounts
        ):
            raise RuntimeError("Created container does not mount the selected workspace")

    def execute(self):
        self.result = self.call(
            "start",
            "--attach",
            self.identifier,
            timeout=self.recipe["timeout_seconds"],
        )
        probe_result = self.result
        if probe_result.timed_out:
            self.checked("kill", self.identifier)
        state = json.loads(self.checked("inspect", self.identifier))[0]
        (self.directory / "container-final.json").write_text(json.dumps(state, indent=2))
        if (
            state["Id"] != self.identifier
            or state["Image"] != self.recipe["image_id"]
            or state["State"]["Running"]
        ):
            raise RuntimeError("Container final identity or stopped state could not be established")
        if state["State"].get("Error") or not probe_result.launched:
            raise RuntimeError(
                "Container execution failed; inspect the retained state and commands"
            )
        if not probe_result.timed_out and state["State"]["ExitCode"] != probe_result.exit_code:
            raise RuntimeError("Docker attach status disagrees with container exit status")
        self.result = probe_result

    def collect(self):
        probe_result = self.result
        for name in ("collector.log", "execve.log"):
            self.checked(
                "cp", f"{self.identifier}:{self.trace_dir}/{name}", str(self.directory / name)
            )
        calibration = successful_execs((self.directory / "collector.log").read_text().splitlines())
        if not any(path == "/bin/true" for path, _ in calibration):
            raise RuntimeError("Container exec collector failed its calibration")
        self.executions = successful_execs((self.directory / "execve.log").read_text().splitlines())
        if not any(path == "/bin/bash" for path, _ in self.executions):
            raise RuntimeError("Collector did not record the probe interpreter")
        self.collector.update(available=True, limitation=None)
        self.result = probe_result

    def finish(self):
        cleanup = self.call("rm", "-f", "-v", self.identifier or self.name)
        if cleanup.exit_code and self.identifier:
            self.errors.append(f"Container cleanup failed: {cleanup.stderr_path}")
        observations = []
        if self.collector["available"] and self.info:
            observations = [
                {
                    "container_id": self.identifier,
                    "image": self.recipe["image"],
                    "image_id": self.info["Image"],
                    "mounts": self.info["Mounts"],
                    "argv": exec_arguments(line),
                    "collector": "container-strace",
                }
                for path, line in self.executions
                if Path(path).name.startswith("qemu-system-") and qemu_experiment(line)
            ]
        output = self.directory / "container-processes.json"
        output.write_text(
            json.dumps({"observations": observations, "errors": self.errors}, indent=2)
        )
        if self.errors:
            self.collector["limitation"] = "; ".join(self.errors)
        (self.directory / "capture.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "container_id": self.identifier,
                    "image": self.recipe,
                    "initial_inspect": self.info,
                    "commands": self.commands,
                    "collector": self.collector,
                    "infrastructure_errors": self.errors,
                },
                indent=2,
            )
        )
        # These paths are also consumed by the existing environment attempt format.
        trace = self.directory / "execve.log"
        if not trace.exists():
            trace.write_text("")
        trace.with_suffix(".collector.json").write_text(json.dumps(self.collector))
        return Capture(self.result, self.executions, output, self.collector, self.errors)


def run(workspace, directory, recipe, probe):
    execution = _Run(workspace, directory, recipe)
    try:
        execution.create(probe)
        execution.execute()
        execution.collect()
    except (RuntimeError, OSError, ValueError, KeyError, IndexError) as error:
        execution.errors.append(str(error))
    finally:
        capture = execution.finish()
    return capture


def main():
    """Run a prepared debug probe; never finalize a project or certify a driver."""
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--image", required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--probe", required=True, type=Path)
    parser.add_argument("--timeout", required=True, type=int)
    parser.add_argument("--accelerator", choices=("kvm", "tcg"))
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    probe = (workspace / args.probe).resolve()
    if workspace not in probe.parents or not probe.is_file():
        parser.error("Probe must be a file inside the selected workspace")
    if not 1 <= args.timeout <= 600 or not re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id):
        parser.error("Invalid image identity or timeout")
    root = workspace / ".dpf-output/environment-runs"
    root.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=root))
    capture = run(
        workspace,
        directory,
        {
            "image": args.image,
            "image_id": args.image_id,
            "timeout_seconds": args.timeout,
            "accelerator": args.accelerator,
        },
        probe,
    )
    from .feedback import probe_summary

    print(json.dumps(probe_summary(capture, directory)))
    raise SystemExit(125 if capture.infrastructure_errors else capture.command.exit_code)


if __name__ == "__main__":
    main()
