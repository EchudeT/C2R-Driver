"""Method-independent pinned Docker runner and receipt helpers."""

import hashlib
import json
import os
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


def docker(root, target, command, log, *, network=False, timeout=1200, workdir="kernel"):
    profile = read(root / "environment.json")
    name = "dpf-native-" + uuid.uuid4().hex[:12]
    argv = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "--hostname",
        "dpf-native",
        "--device",
        "/dev/kvm",
        "--network",
        "bridge" if network else "none",
        "-v",
        f"{target}:/root/asterinas",
        "-v",
        f"{root / 'shared'}:/dpf-shared:ro",
        "-v",
        f"{HERE}:/dpf-runner:ro",
        "-v",
        f"{HERE.parents[1] / 'src/driver_port_factory/platform'}:/dpf-platform:ro",
        "-v",
        "dpf-native-cargo-registry:/root/.cargo/registry",
        "-v",
        "dpf-native-cargo-git:/root/.cargo/git",
        "-w",
        "/root/asterinas/" + workdir,
    ]
    if network:
        argv += ["--device", "/dev/net/tun", "--cap-add", "NET_ADMIN"]
    for key, value in {
        "CONSOLE": "ttyS0",
        "MEM": "2G",
        "SMP": "1",
        "OVMF": "on",
        "CARGO_BUILD_JOBS": "4",
        "CARGO_INCREMENTAL": "0",
        "CARGO_PROFILE_DEV_DEBUG": "0",
        "RUSTUP_TOOLCHAIN": "nightly-2026-07-21",
        "OSDK_LOCAL_DEV": "1",
        "NETDEV": "tap" if network else "user",
        "VDSO_LIBRARY_DIR": "/root/linux_vdso",
    }.items():
        argv += ["-e", f"{key}={value}"]
    argv += [
        profile["image_id"],
        "bash",
        "-lc",
        (
            "export PATH=/dpf-shared/sdk:"
            "/root/.rustup/toolchains/nightly-2026-07-21-x86_64-unknown-linux-gnu/bin:$PATH; "
            '"$@"; result=$?; '
            f'chown -R {os.getuid()}:{os.getgid()} /root/asterinas; exit "$result"'
        ),
        "native",
        *command,
    ]
    try:
        return run(argv, log, timeout)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
