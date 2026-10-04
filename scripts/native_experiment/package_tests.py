"""Package unmodified upstream regression files; no replacement test assertions."""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path


def main():
    target = Path("/root/asterinas")
    task = json.loads(Path("/dpf-shared/package-task.json").read_text())
    build = target / "target/native-tests"
    root = build / "rootfs"
    base = Path("/nix/store/mk0lg4gpd6v1nmmyb1qsq5lin9ra3v7z-initramfs")
    if not base.is_dir():
        raise RuntimeError("Configured container initramfs base is absent")
    shutil.copytree(base, root, symlinks=True)
    for path in root.rglob("*"):
        if not path.is_symlink():
            path.chmod(path.stat().st_mode | 0o200 | (0o100 if path.is_dir() else 0))
    root.chmod(0o755)
    shutil.copyfile(target / "test/initramfs/src/init", root / "init")
    (root / "init").chmod(0o755)
    manifest = []
    benchmarks = task.get("native_benchmarks", [])
    if benchmarks:
        # Test the configured TAP route inside this disposable network namespace.
        subprocess.run(["ip", "tuntap", "add", "dev", "dpl-probe", "mode", "tap"], check=True)
        subprocess.run(["ip", "link", "del", "dpl-probe"], check=True)
        executable = Path(shutil.which("iperf3") or "/missing-iperf3").resolve(strict=True)
        package = executable.parent.parent
        closure = subprocess.check_output(["nix-store", "-qR", str(package)], text=True)
        for entry in closure.splitlines():
            destination = root / entry.lstrip("/")
            if not destination.exists():
                shutil.copytree(entry, destination, symlinks=True)
        binary = root / "benchmark/bin/iperf3"
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.unlink(missing_ok=True)
        binary.symlink_to(executable)
        for relative in ["common/bench_runner.sh", *(b + "/run.sh" for b in benchmarks)]:
            source = target / "test/initramfs/src/benchmark" / relative
            destination = root / "benchmark" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(0o755)
            manifest.append(
                {
                    "source": str(source.relative_to(target)),
                    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                }
            )
    for source in task["native_tests"]:
        relative = Path(source).relative_to("test/initramfs/src/regression").with_suffix("")
        output = root / "test" / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        output.unlink(missing_ok=True)
        subprocess.run(
            [
                "gcc",
                "-static",
                "-O2",
                "-pthread",
                "-D__asterinas__",
                str(target / source),
                "-o",
                str(output),
            ],
            check=True,
        )
        manifest.append(
            {
                "source": source,
                "source_sha256": hashlib.sha256((target / source).read_bytes()).hexdigest(),
                "binary": str(output.relative_to(root)),
                "binary_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        )
    image = build / "initramfs.cpio.gz"
    subprocess.run(
        [
            "bash",
            "-o",
            "pipefail",
            "-c",
            'find . -print0 | cpio --null -o --format=newc --quiet | gzip -1 > "$1"',
            "pack",
            str(image),
        ],
        cwd=root,
        check=True,
    )
    # Same filesystems/sizes as upstream Makefile, sparse allocation saves host storage.
    disks = target / "test/initramfs/build"
    disks.mkdir(parents=True, exist_ok=True)
    for name, size, filesystem in [
        ("ext2.img", 2 << 30, "ext2"),
        ("exfat.img", 512 << 20, "exfat"),
        ("ltp_dev.img", 512 << 20, "ext2"),
        ("nvme0n1.img", 256 << 20, "ext2"),
    ]:
        disk = disks / name
        with disk.open("wb") as stream:
            stream.truncate(size)
        argv = (
            ["mkfs.exfat", str(disk)]
            if filesystem == "exfat"
            else ["mke2fs", "-q", "-t", "ext2", "-F", str(disk)]
        )
        subprocess.run(argv, check=True)
    metadata = {
        "tests": manifest,
        "base_initramfs": str(base),
        "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
        "compiler": subprocess.check_output(["gcc", "--version"], text=True).splitlines()[0],
        "vdso_directory": os.environ.get("VDSO_LIBRARY_DIR"),
    }
    (build / "manifest.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(json.dumps(metadata))


if __name__ == "__main__":
    main()
