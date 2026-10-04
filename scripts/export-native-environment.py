#!/usr/bin/env python3
"""Export the small, standalone environment repository; never copy experimental data."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--validation-summary", type=Path, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve()
    if not (destination / ".git").is_dir():
        parser.error(
            "destination must be the dedicated Git checkout, not a trial or data directory"
        )
    sources = [ROOT / "scripts/native-experiment.py"]
    sources += sorted((ROOT / "scripts/native_experiment").glob("*.py"))
    sources += [ROOT / "scripts/native_experiment/README.md"]
    sources += [
        ROOT / "src/driver_port_factory/platform" / name
        for name in ["native_runner.py", "guest.py", "network_peer.py"]
    ]
    sources += [ROOT / "configs/experiments/native-drivers.json"]
    sources += [
        ROOT / "configs/drivers" / f"linux-{name}.catalog.json"
        for name in ["virtio-rng", "virtio-blk", "virtio-net", "nvme-pci", "virtio-vsock"]
    ]
    sources += [
        ROOT / "docs/experiments/NATIVE_DRIVER_SUITE.zh-CN.md",
        ROOT / "docs/audits/NATIVE_ENVIRONMENT_20261004.md",
    ]
    files = {}
    for source in sources:
        relative = source.relative_to(ROOT)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        files[str(relative)] = hashlib.sha256(source.read_bytes()).hexdigest()
    shutil.copyfile(
        ROOT / "scripts/native_experiment/environment-README.md", destination / "README.md"
    )
    (destination / "validation").mkdir(exist_ok=True)
    shutil.copyfile(args.validation_summary, destination / "validation/local-20261004.json")
    for relative in [
        "src/driver_port_factory/__init__.py",
        "src/driver_port_factory/platform/__init__.py",
    ]:
        (destination / relative).write_text('"""Standalone runtime subset; no DPF controller."""\n')
    (destination / ".gitignore").write_text(
        "__pycache__/\n*.py[cod]\n.venv/\n.pytest_cache/\n.ruff_cache/\n"
        "/experiments/\n/data/\n/runs/\n*.iso\n*.img\n*.cpio*\n*.vhdx\n.env\nauth.json\n"
    )
    commit = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    (destination / "PROVENANCE.json").write_text(
        json.dumps(
            {
                "source_repository": "https://github.com/EchudeT/C2R-Driver",
                "source_commit": commit,
                "files_sha256": files,
                "scope": (
                    "Shared native environment runtime only; "
                    "no upstream driver implementation or build data"
                ),
            },
            indent=2,
        )
        + "\n"
    )
    print("Exported source/config/docs only:", destination)


if __name__ == "__main__":
    main()
