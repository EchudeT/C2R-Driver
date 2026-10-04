#!/usr/bin/env python3
"""Export one committed task with its public runner and a fresh, isolated Git history."""

import argparse
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract(prefix, destination):
    process = subprocess.Popen(
        ["git", "-C", str(ROOT), "archive", "HEAD", prefix], stdout=subprocess.PIPE
    )
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                parts = Path(member.name).parts
                prefix_parts = Path(prefix).parts
                if member.isdir() and prefix_parts[: len(parts)] == parts:
                    continue
                if parts[: len(prefix_parts)] != prefix_parts:
                    raise ValueError("Archive path does not match the selected task")
                relative = parts[len(prefix_parts) :]
                if not relative:
                    continue
                if prefix.startswith("tasks/") and relative == ("source",):
                    continue
                member.name = str(Path(*relative))
                archive.extract(member, destination, filter="data")
                # The safe tar filter may normalize link text; retain upstream Git bytes.
                if member.issym():
                    link = destination / member.name
                    link.unlink()
                    link.symlink_to(member.linkname)
        if process.wait():
            raise RuntimeError("Committed task export failed")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("driver", choices=["virtio-rng", "virtio-blk", "virtio-net", "nvme-pci"])
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if destination.is_relative_to(ROOT):
        parser.error("Export outside the distribution checkout so workers see only their task")
    destination.mkdir(parents=True, exist_ok=False)
    extract("tasks/" + args.driver, destination)
    # The distribution shares one Linux tree; the worker receives the complete tree.
    extract("source/linux", destination / "source")
    for path in ["scripts", "src", "configs"]:
        extract(path, destination / ".environment" / path)
    subprocess.run(["git", "init", "-q", "-b", "main", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "add", "."], check=True)
    # Include every upstream file, even paths matched by inherited ignore rules.
    subprocess.run(["git", "-C", str(destination), "add", "-f", "source"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "user.name=Experiment preparation",
            "-c",
            "user.email=experiment@localhost",
            "commit",
            "-qm",
            "Isolated single-driver experiment input",
        ],
        check=True,
    )
    print("Exported one task with no remote or distribution Git history:", destination)
    print(
        "Run ./prepare.sh (or ./prepare.sh --reuse /path/to/shared-runtime), then use Codex + goal."
    )


if __name__ == "__main__":
    main()
