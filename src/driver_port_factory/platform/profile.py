"""Explicit Asterinas x86_64 ISO adapter. No acceleration/image fallback."""

import hashlib
import json
import platform
from pathlib import Path

from ..core.models import WorkflowError
from .configuration import OVMF_PATH


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def adapter_identity():
    return {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(Path(__file__).parent.glob("*.py"))
    }


def asterinas(image, image_id, revision, accelerator, *, machine="q35"):
    if machine not in {"pc", "q35"}:
        raise WorkflowError("Unsupported explicit QEMU machine")
    if accelerator not in {"kvm", "tcg"}:
        raise WorkflowError("Select kvm or tcg explicitly; no fallback")
    if platform.machine() != "x86_64":
        raise WorkflowError("This adapter supports x86_64 hosts only")
    return {
        "schema_version": 1,
        "adapter": "asterinas-x86_64-iso",
        "target_revision": revision,
        "host_architecture": platform.machine(),
        "image": image,
        "image_id": image_id,
        "accelerator": accelerator,
        "build_network": "host",
        "runtime_network": "none",
        "build_argv": ["make", "kernel", "INITRAMFS=on", "LOG_LEVEL=info", "CONSOLE=ttyS0"],
        "artifact": "target/osdk/asterinas-osdk-bin.iso",
        "environment": {"VDSO_LIBRARY_DIR": "/root/linux_vdso"},
        "qemu": "qemu-system-x86_64",
        "qemu_args": [
            "-machine",
            f"{machine},kernel-irqchip=split" if accelerator == "kvm" else machine,
            "-accel",
            accelerator,
            "-cpu",
            "host,+x2apic" if accelerator == "kvm" else "max,+x2apic",
            "-smp",
            "1",
            "-m",
            "2G",
            "-no-reboot",
            "-display",
            "none",
            "-monitor",
            "none",
            "-serial",
            "stdio",
            "-boot",
            "d",
            "-bios",
            OVMF_PATH,
            "-device",
            "isa-debug-exit,iobase=0xf4,iosize=0x04",
        ],
        "ready_text": "# ",
        "adapter_identity": adapter_identity(),
    }


def container_argv(profile, worktree, name, command, *, build, cache_key, artifact=None):
    if "native" in profile:
        from .native_runner import container_argv as native_argv

        native = profile["native"]
        return native_argv(
            native["environment"],
            native["root"],
            worktree,
            name,
            command,
            network=build or native["driver"] == "virtio-net",
            artifact=artifact,
        )
    argv = [
        "docker",
        "run",
        "--rm",
        "--pull=never",
        "--name",
        name,
        "-i",
        "--network",
        profile["build_network"] if build else profile["runtime_network"],
        "--mount",
        f"type=bind,source={worktree},target={worktree}",
        "-w",
        str(worktree),
    ]
    if artifact is not None and not Path(artifact).is_relative_to(worktree):
        argv.extend(["--mount", f"type=bind,source={artifact},target={artifact},readonly"])
    if build:
        # Docker initializes new named volumes from image contents. Never bind an empty
        # host directory over /root/.cargo: that hides the installed tool binaries.
        for directory in ("cargo", "rustup"):
            argv.extend(
                [
                    "--mount",
                    f"type=volume,source=dpf-{cache_key}-{directory},target=/root/.{directory}",
                ]
            )
        for key, value in profile["environment"].items():
            argv.extend(["-e", f"{key}={value}"])
    elif profile["accelerator"] == "kvm":
        argv.extend(["--device", "/dev/kvm"])
    return [*argv, profile["image"], *command]
