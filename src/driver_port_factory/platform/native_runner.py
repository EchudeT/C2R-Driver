"""Shared native-suite container route for operator controls, DPF and Codex."""

import os
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
RUNNER = REPOSITORY / "scripts/native_experiment"
BUILD = [
    "cargo",
    "osdk",
    "build",
    "--initramfs=/root/asterinas/target/native-tests/initramfs.cpio.gz",
    "--kcmd-args=console=ttyS0",
    "--kcmd-args=earlycon",
    "--kcmd-args=loglevel=error",
]


def container_argv(
    environment, root, target, name, command, *, network=False, workdir="kernel", artifact=None
):
    root, target = Path(root).resolve(), Path(target).resolve()
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
        f"{target}:{target}",
        "-v",
        f"{root / 'shared'}:/dpf-shared:ro",
        "-v",
        f"{RUNNER}:/dpf-runner:ro",
        "-v",
        f"{Path(__file__).parent}:/dpf-platform:ro",
        "-v",
        "dpf-native-cargo-registry:/root/.cargo/registry",
        "-v",
        "dpf-native-cargo-git:/root/.cargo/git",
        "-w",
        "/root/asterinas/" + workdir,
    ]
    if artifact is not None and not Path(artifact).resolve().is_relative_to(target):
        argv += ["-v", f"{artifact}:{artifact}:ro"]
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
    return [
        *argv,
        environment["image_id"],
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


def setup(root, target):
    """Fresh disposable test media; toolchain and SDK remain shared."""
    import subprocess

    pairs = [(root / "shared/initramfs.cpio.gz", target / "target/native-tests/initramfs.cpio.gz")]
    pairs += [
        (p, target / "test/initramfs/build" / p.name) for p in (root / "shared/disks").glob("*.img")
    ]
    for source, destination in pairs:
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["cp", "--sparse=always", "--reflink=auto", str(source), str(destination)], check=True
        )


def verify_inputs(root, target):
    """The upstream test sources and packaged binaries stay operator owned."""
    import hashlib
    import json

    def sha(path):
        with path.open("rb") as stream:
            return hashlib.file_digest(stream, "sha256").hexdigest()

    profile = json.loads((root / "environment.json").read_text())
    expected_files = {
        root / "shared/sdk/cargo-osdk": profile["sdk_sha256"],
        root / "shared/initramfs.cpio.gz": profile["initramfs_sha256"],
        **{
            target / relative: expected
            for relative, expected in json.loads(
                (root / "shared/oracle-sources.json").read_text()
            ).items()
        },
    }
    for path, expected in expected_files.items():
        if not path.is_file() or sha(path) != expected:
            raise ValueError("Frozen native SDK, test or boot source changed: " + str(path))


def runtime_identity():
    import hashlib

    files = {
        "container": Path(__file__),
        "guest": Path(__file__).with_name("guest.py"),
        "network_peer": Path(__file__).with_name("network_peer.py"),
        "runtime": RUNNER / "runtime.py",
        "oracle": RUNNER / "oracle.py",
    }
    return {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}
