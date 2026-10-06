#!/usr/bin/python3.11
"""Filesystem namespace launcher. It exposes no host home, answer trees or Docker socket."""

import argparse
import json
import os
from pathlib import Path


def namespace_command(manifest, command):
    argv = [
        manifest["bwrap"],
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--tmpfs",
        "/tmp",
    ]
    # A private /dev must still expose the configured QEMU devices. Docker uses
    # the host device through the broker; platform prerequisite checks run here.
    for device in ("/dev/kvm", "/dev/net/tun"):
        if Path(device).is_char_device():
            argv += ["--dev-bind", device, device]
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    for path in (
        "/etc/ssl",
        "/etc/resolv.conf",
        "/etc/hosts",
        "/etc/nsswitch.conf",
        "/etc/passwd",
        "/etc/group",
        "/etc/localtime",
        "/etc/ld.so.cache",
    ):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    for path in manifest["readonly"]:
        argv += ["--ro-bind", path, path]
    for path in manifest["writable"]:
        argv += ["--bind", path, path]
    argv += [
        "--ro-bind",
        manifest["docker_proxy"],
        "/worker-bin/docker",
        "--ro-bind",
        manifest["broker"],
        manifest["broker"],
        "--setenv",
        "C2R_DOCKER_BROKER",
        manifest["broker"],
        "--setenv",
        "HOME",
        manifest["home"],
        "--setenv",
        "CODEX_HOME",
        manifest["home"],
        "--setenv",
        "PATH",
        "/worker-bin:/usr/bin:/bin",
        "--chdir",
        manifest["cwd"],
        "--",
        *command,
    ]
    return argv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    manifest = json.loads(args.manifest.read_text())
    argv = namespace_command(manifest, command)
    os.execv(argv[0], argv)


if __name__ == "__main__":
    main()
