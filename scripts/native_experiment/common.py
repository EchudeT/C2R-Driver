"""Method-independent pinned Docker runner and receipt helpers."""

import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE.parents[1] / "configs/experiments/native-drivers.json"


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def run(argv, log, timeout=1200):
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log.open("w") as stream:
        try:
            result = subprocess.run(
                argv, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout, check=False
            )
            code = result.returncode
        except subprocess.TimeoutExpired:
            code = 124
    receipt = {
        "argv": list(map(str, argv)),
        "exit_code": code,
        "seconds": round(time.time() - started, 3),
        "log": str(log),
        "sha256": sha(log),
    }
    write(log.with_suffix(".receipt.json"), receipt)
    return receipt


def docker(
    root, target, command, log, *, network=False, timeout=1200, workdir="kernel", artifact=None
):
    from driver_port_factory.platform.native_runner import container_argv

    name = "dpf-native-" + uuid.uuid4().hex[:12]
    argv = container_argv(
        read(root / "environment.json"),
        root,
        target,
        name,
        command,
        network=network,
        workdir=workdir,
        artifact=artifact,
    )
    try:
        return run(argv, log, timeout)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
