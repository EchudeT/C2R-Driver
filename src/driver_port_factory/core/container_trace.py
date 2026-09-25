"""Read-only Docker process observations scoped to one harness execution."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from .models import utc_now
from .trace import exec_arguments, successful_execs


class ContainerTrace:
    def __init__(self, workspace: Path, output: Path):
        self.workspace = workspace.resolve()
        self.output = output
        self.docker = shutil.which("docker")
        self.records: list[dict] = []
        self.errors: list[str] = []
        self.stop = threading.Event()
        self.thread = None
        self.before: set[str] = set()
        self.seen: set[tuple] = set()

    def _call(self, *args: str) -> str:
        result = subprocess.run([self.docker, *args], capture_output=True, text=True, timeout=3)
        if result.returncode:
            raise RuntimeError(result.stderr.strip()[:400])
        return result.stdout

    def __enter__(self):
        if self.docker:
            try:
                # Include stopped containers: a previously existing run is not fresh evidence.
                self.before = set(self._call("ps", "-aq", "--no-trunc").split())
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                self.errors.append(str(error))
                self.docker = None
        if self.docker:
            self.thread = threading.Thread(target=self._watch, daemon=True)
            self.thread.start()
        return self

    def _watch(self):
        while not self.stop.is_set():
            try:
                for identifier in set(self._call("ps", "-q", "--no-trunc").split()) - self.before:
                    info = json.loads(self._call("inspect", identifier))[0]
                    mounts = [m for m in info.get("Mounts", []) if m.get("Type") == "bind"]
                    if not any(Path(m["Source"]).resolve() == self.workspace for m in mounts):
                        continue
                    top = self._call("top", identifier, "-eo", "pid,args")
                    for row in top.splitlines()[1:]:
                        try:
                            pid, command = row.strip().split(None, 1)
                            if not pid.isdigit():
                                continue
                            argv = shlex.split(command)
                        except ValueError:
                            continue
                        if not argv or not Path(argv[0]).name.startswith("qemu-system-"):
                            continue
                        key = (identifier, tuple(argv))
                        if key in self.seen:
                            continue
                        self.seen.add(key)
                        self.records.append({"container_id": identifier,
                            "image": info["Config"]["Image"], "image_id": info["Image"],
                            "mounts": mounts, "argv": argv, "top": top,
                            "observed_at": utc_now()})
            except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError, KeyError) as error:
                message = str(error)[:400]
                if message not in self.errors:
                    self.errors.append(message)
            self.stop.wait(.2)

    def __exit__(self, *args):
        self.stop.set()
        if self.thread:
            self.thread.join()
        self.output.write_text(json.dumps({"observations": self.records, "errors": self.errors}, indent=2))

    def executions(self, host_trace: Path) -> tuple[tuple[str, str], ...]:
        host = successful_execs(host_trace.read_text(errors="replace").splitlines())
        self._diagnose_docker_runs(host)
        result = []
        for record in self.records:
            # A fresh container and workspace mount alone are insufficient: the traced
            # harness must also have launched Docker with this exact image.
            if not any(Path(path).name == "docker" and '"run"' in line
                       and json.dumps(record["image"]) in line for path, line in host):
                continue
            argv = []
            for argument in record["argv"]:
                # QEMU embeds drive paths in comma-separated key/value options.
                # Translate only file values, never arbitrary option substrings.
                fields = argument.split(",")
                if ",," not in argument and any(field.startswith("file=/") for field in fields):
                    argument = ",".join(
                        "file=" + self._host_path(field[5:], record["mounts"])
                        if field.startswith("file=/") else field for field in fields
                    )
                else:
                    argument = self._host_path(argument, record["mounts"])
                argv.append(argument)
            result.append((record["argv"][0], "docker-observed " + json.dumps(argv)))
        # ``__exit__`` writes the observation file before the host exec trace is
        # available.  Rewrite it here after adding trace-derived diagnostics so
        # a rejected run cannot end with an empty observations/errors record.
        self._write_output()
        return tuple(result)

    def _write_output(self) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(
            json.dumps({"observations": self.records, "errors": self.errors}, indent=2)
        )

    def _diagnose_docker_runs(self, host: tuple[tuple[str, str], ...]) -> None:
        """Record deterministic reasons when Docker was invoked without our bind mount.

        The host ``strace`` can see the Docker client but not processes launched
        by the daemon in the container namespace.  A container without the
        current workspace mount is therefore never attributable to this run;
        report that exact cause instead of leaving an unexplained empty
        observation file.
        """

        for path, line in host:
            if Path(path).name != "docker":
                continue
            argv = exec_arguments(line)
            if not argv or "run" not in argv[1:]:
                continue
            run_index = argv.index("run", 1)
            run_args = argv[run_index + 1 :]
            if self._has_workspace_bind(run_args):
                continue
            rendered = json.dumps(argv, ensure_ascii=False)
            self._append_error(
                "docker run did not bind-mount the current execution workspace "
                f"{self.workspace}; container QEMU cannot be attributed to this run. "
                f"argv={rendered}"
            )

    def _append_error(self, message: str) -> None:
        if message not in self.errors:
            self.errors.append(message)

    def _has_workspace_bind(self, arguments: list[str]) -> bool:
        """Return whether Docker ``run`` arguments bind-mount this workspace."""

        def same_source(source: str) -> bool:
            try:
                return Path(source).expanduser().resolve() == self.workspace
            except (OSError, RuntimeError, ValueError):
                return False

        index = 0
        while index < len(arguments):
            argument = arguments[index]
            specification = None
            if argument in {"-v", "--volume"} and index + 1 < len(arguments):
                specification = arguments[index + 1]
                index += 2
            elif argument.startswith("-v") and len(argument) > 2:
                specification = argument[2:]
                index += 1
            elif argument.startswith("--volume="):
                specification = argument.split("=", 1)[1]
                index += 1
            elif argument == "--mount" and index + 1 < len(arguments):
                specification = arguments[index + 1]
                index += 2
            elif argument.startswith("--mount="):
                specification = argument.split("=", 1)[1]
                index += 1
            else:
                index += 1
            if not specification:
                continue
            if specification.startswith("type=bind,"):
                fields = dict(
                    item.split("=", 1)
                    for item in specification.split(",")
                    if "=" in item
                )
                if same_source(fields.get("source", fields.get("src", ""))):
                    return True
                continue
            source = specification.split(":", 1)[0]
            if same_source(source):
                return True
        return False

    @staticmethod
    def _host_path(argument: str, mounts: list[dict]) -> str:
        for mount in sorted(mounts, key=lambda m: len(m["Destination"]), reverse=True):
            destination = mount["Destination"].rstrip("/")
            if argument == destination or argument.startswith(destination + "/"):
                return str(Path(mount["Source"]) / argument[len(destination):].lstrip("/"))
        return argument
