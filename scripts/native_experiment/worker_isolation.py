"""Prepare a minimal model view and private Docker transport, shared by both methods."""

import shutil
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

from common import HERE, write
from docker_broker import DockerBroker


def code_paths(factory):
    return [
        factory / path
        for path in ("src", "scripts", "configs", "skill", ".venv", "README.md", "pyproject.toml")
        if (factory / path).exists()
    ]


def codex_paths(binary):
    executable = Path(shutil.which(binary) or binary).resolve()
    if executable.suffix == ".js":
        # The npm distribution needs its executable package and platform binary package.
        return executable, [executable.parents[2]]
    return executable, [executable]


def prepare_view(protocol, trial, directory, method, bwrap, broker_path):
    factory, root = Path(protocol["factory"]), Path(protocol["root"])
    executable, runtime = codex_paths(protocol["codex_bin"])
    home = directory / "model-home"
    home.mkdir(exist_ok=True)
    # Never inherit host skills, memories, plugins or credentials from ~/.codex.
    (home / "config.toml").touch(exist_ok=True)
    readonly = [root / "shared", Path(protocol["qemu"]), *runtime]
    if method == "dpf":
        readonly += code_paths(factory)
        readonly += [
            root / "seeds" / protocol["driver"],
            root / "environment.json",
            root / "config.json",
            trial / "repository-pins.json",
        ]
        writable = [trial / "run", home]
        (trial / "run").mkdir(exist_ok=True)
        cwd = trial / "run"
    else:
        readonly += [trial / "source", trial / "PROMPT.md"]
        writable = [trial / "target", home]
        cwd = trial
    manifest = {
        "method": method,
        "bwrap": str(bwrap),
        "cwd": str(cwd),
        "home": str(home),
        "readonly": list(map(str, readonly)),
        "writable": list(map(str, writable)),
        "broker": str(broker_path),
        "docker_proxy": str(HERE / "docker_proxy.py"),
        "codex": str(executable),
    }
    write(directory / "namespace.json", manifest)
    return manifest


@contextmanager
def isolated_worker(protocol, trial, directory, method):
    factory = Path(protocol["factory"])
    bwrap = shutil.which("bwrap") or str(factory / ".local/bin/bwrap")
    if not Path(bwrap).is_file():
        raise ValueError("Install bubblewrap; no unrestricted worker fallback is allowed")
    with (
        tempfile.TemporaryDirectory(prefix="c2r-worker-") as socket_directory,
        broker_worker(
            protocol, trial, directory, method, bwrap, Path(socket_directory) / "docker.sock"
        ) as prefix,
    ):
        yield prefix


@contextmanager
def broker_worker(protocol, trial, directory, method, bwrap, broker_path):
    factory = Path(protocol["factory"])
    manifest = prepare_view(protocol, trial, directory, method, bwrap, broker_path)
    docker_readonly = [Path(protocol["root"]) / "shared"]
    docker_readonly.append(Path(protocol["qemu"]))
    if method == "codex":
        docker_readonly.append(trial / "source")
    if method == "dpf":
        docker_readonly += [
            factory / "scripts/native_experiment",
            factory / "src/driver_port_factory/platform",
        ]
    broker = DockerBroker(
        broker_path,
        protocol["task"]["environment"]["image_id"],
        [trial / ("run" if method == "dpf" else "target")],
        docker_readonly,
    )
    thread = threading.Thread(target=broker.serve_forever, daemon=True)
    thread.start()
    try:
        yield [
            sys.executable,
            str(HERE / "worker_sandbox.py"),
            "--manifest",
            str(directory / "namespace.json"),
            "--",
            manifest["codex"],
        ]
    finally:
        broker.shutdown()
        broker.server_close()
        thread.join(timeout=5)
        import subprocess

        containers = broker.known()
        if containers:
            subprocess.run(
                [broker.docker, "rm", "-f", *containers], capture_output=True, check=False
            )
