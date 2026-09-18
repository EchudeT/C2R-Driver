"""Read-only Docker process observations scoped to one harness execution."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import threading
from pathlib import Path

from .models import utc_now
from .trace import successful_execs


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
                    top = self._call("top", identifier, "-eo", "args")
                    for row in top.splitlines()[1:]:
                        try:
                            argv = shlex.split(row)
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
        result = []
        for record in self.records:
            # A fresh container and workspace mount alone are insufficient: the traced
            # harness must also have launched Docker with this exact image.
            if not any(Path(path).name == "docker" and '"run"' in line
                       and json.dumps(record["image"]) in line for path, line in host):
                continue
            argv = []
            for argument in record["argv"]:
                for mount in sorted(record["mounts"], key=lambda m: len(m["Destination"]), reverse=True):
                    destination = mount["Destination"].rstrip("/")
                    if argument == destination or argument.startswith(destination + "/"):
                        argument = str(Path(mount["Source"]) / argument[len(destination):].lstrip("/"))
                        break
                argv.append(argument)
            result.append((record["argv"][0], "docker-observed " + json.dumps(argv)))
        return tuple(result)
