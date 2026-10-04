#!/usr/bin/env python3
"""Prepare/check the same upstream-native experiment for DPF and plain Codex."""

import argparse
import os
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "native_experiment"))
from check import check, runtime, verify_inputs
from common import docker, read
from prepare import create_hollow, create_trial, prepare, publish, setup_assets

from driver_port_factory.platform.native_runner import BUILD


def trial_name(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,48}", value):
        raise argparse.ArgumentTypeError(
            "Use a short trial name containing letters, digits, - or _"
        )
    return value


def show_status(root):
    for name, task in read(root / "config.json")["drivers"].items():
        if not task.get("enabled", True):
            print(name, "EXCLUDED", task["reason"])
            continue
        rows = []
        for label in ["positive", "negative"]:
            p = root / "operator" / name / (label + ".json")
            rows.append(label + "=" + (read(p)["status"] if p.exists() else "NOT_RUN"))
        audit = root / "operator" / name / "seed-audit.json"
        published = (root / "seeds" / name / "task.json").is_file() and audit.is_file()
        published = published and read(audit).get("status") == "PASS"
        print(name, *rows, "published=" + str(published))


def run_check(args, root, parser):
    if args.action == "positive":
        target = root / "operator/reference"
    elif args.action == "negative":
        target = root / "operator/hollow" / args.driver / "target"
    else:
        target = root / "trials" / args.method / args.driver / args.trial / "target"
    output = (
        root / "operator" / args.driver
        if args.action != "check"
        else root / "results" / args.method / args.driver / args.trial
    )
    if args.action == "check":
        output = output / uuid.uuid4().hex[:12]
    if (output / (args.action + ".json")).exists():
        parser.error("Receipt already exists; preserve it and use a new trial for another run")
    if args.target is not None:
        target = args.target.resolve()
    result = check(root, args.driver, target, output, args.action)
    if result["status"] not in ("PASS", "NEGATIVE_CONTROL_PASS"):
        sys.exit(1)


def clean(args, root, parser):
    import shutil

    if not args.target:
        parser.error("clean requires an explicit --target worktree")
    target = args.target.resolve()
    if not target.is_relative_to(root):
        parser.error("clean only handles worktrees under this suite")
    for name in ["debug", "x86_64-unknown-none"]:
        path = target / "target" / name
        if path.is_dir():
            shutil.rmtree(path)
            print("Removed rebuildable compiler intermediates:", path)
    paths = [target / "target/native-tests/rootfs"]
    paths += [p for p in (target / "target/osdk").glob("*") if p.name != "asterinas-osdk-bin.iso"]
    for path in paths:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            # Packaged Nix closures have read-only directories, even after chown.
            for directory, _, _ in os.walk(path, followlinks=False):
                os.chmod(directory, 0o700)
            shutil.rmtree(path)


def development(args, root, parser):
    target = (
        args.target or root / "trials" / args.method / args.driver / args.trial / "target"
    ).resolve()
    output = root / "results" / args.method / args.driver / uuid.uuid4().hex[:12]
    verify_inputs(root, target)
    if args.action == "build":
        setup_assets(root, target)
        result = docker(root, target, BUILD, output / "build.log", network=True)
        print(result)
        sys.exit(result["exit_code"])
    task = read(root / "config.json")["drivers"][args.driver]
    if args.case not in [*task["tests"], *task["benchmarks"]]:
        parser.error("--case must select one original test or benchmark from config.json")
    result = runtime(root, target, args.case, output / "case")
    print(result)
    sys.exit(0 if result["passed"] else 1)


def initialize(args, root, parser):
    if not all((args.source, args.target, args.qemu)):
        parser.error("prepare requires --source, --target and --qemu repositories")
    prepare(
        root,
        args.source.resolve(),
        args.target.resolve(),
        args.qemu.resolve(),
        args.sdk.resolve() if args.sdk else None,
    )


def validate_adapter(args, root, parser):
    import subprocess

    if args.factory:
        factory = args.factory.resolve()
        subprocess.run(
            [
                str(factory / ".venv/bin/python"),
                str(factory / "scripts/native-experiment.py"),
                "validate-adapter",
                "--root",
                str(root),
            ],
            check=True,
        )
        return
    if not (
        Path(__file__).resolve().parents[1] / "src/driver_port_factory/platform/executor.py"
    ).exists():
        parser.error("validate-adapter requires --factory pointing to the installed DPF checkout")
    from validate_adapter import validate

    validate(root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "positive",
            "hollow",
            "negative",
            "publish",
            "trial",
            "check",
            "status",
            "build",
            "case",
            "clean",
            "validate-adapter",
        ],
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--sdk", type=Path, help="Reuse the verified SDK binary by exact hash")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--qemu", type=Path)
    parser.add_argument(
        "--factory", type=Path, help="DPF repository, required for external environment releases"
    )
    parser.add_argument("--driver")
    parser.add_argument("--case")
    parser.add_argument("--trial", default="01", type=trial_name)
    parser.add_argument("--method", choices=["dpf", "codex"], default="codex")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        initialize(args, root, parser)
    elif args.action == "status":
        show_status(root)
    elif args.action == "validate-adapter":
        validate_adapter(args, root, parser)
    elif args.action == "clean":
        clean(args, root, parser)
    elif not args.driver:
        parser.error("--driver is required")
    elif args.action == "trial":
        if args.qemu is None:
            parser.error("trial requires --qemu (the existing pinned QEMU source repository)")
        create_trial(root, args.driver, args.method, args.qemu, args.factory, args.trial)
    elif args.action == "hollow":
        create_hollow(root, args.driver)
    elif args.action == "publish":
        if not args.source:
            parser.error("publish requires --source")
        publish(root, args.driver, args.source.resolve())
    elif args.action in {"build", "case"}:
        development(args, root, parser)
    else:
        run_check(args, root, parser)


if __name__ == "__main__":
    main()
