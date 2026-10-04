#!/usr/bin/env python3
"""Prepare/check the same upstream-native experiment for DPF and plain Codex."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "native_experiment"))
from check import check
from common import read
from prepare import create_hollow, create_trial, prepare, publish


def show_status(root):
    for name, task in read(root / "config.json")["drivers"].items():
        if not task.get("enabled", True):
            print(name, "EXCLUDED", task["reason"])
            continue
        rows = []
        for label in ["positive", "negative"]:
            p = root / "operator" / name / (label + ".json")
            rows.append(label + "=" + (read(p)["status"] if p.exists() else "NOT_RUN"))
        print(name, *rows, "published=" + str((root / "seeds" / name).exists()))


def run_check(args, root, parser):
    if args.action == "positive":
        target = root / "operator/reference"
    elif args.action == "negative":
        target = root / "operator/hollow" / args.driver / "target"
    else:
        target = root / "trials" / args.method / args.driver / "target"
    output = (
        root / "operator" / args.driver
        if args.action != "check"
        else root / "results" / args.method / args.driver
    )
    if (output / (args.action + ".json")).exists():
        parser.error("Receipt already exists; preserve it and use a new trial for another run")
    if args.target is not None:
        target = args.target.resolve()
    result = check(root, args.driver, target, output, args.action)
    if result["status"] not in ("PASS", "NEGATIVE_CONTROL_PASS"):
        sys.exit(1)


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
        ],
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--target", type=Path)
    parser.add_argument("--qemu", type=Path)
    parser.add_argument("--driver")
    parser.add_argument("--method", choices=["dpf", "codex"], default="codex")
    args = parser.parse_args()
    root = args.root.resolve()
    if args.action == "prepare":
        if not all((args.source, args.target, args.qemu)):
            parser.error("prepare requires --source, --target and --qemu repositories")
        prepare(root, args.source.resolve(), args.target.resolve(), args.qemu.resolve())
    elif args.action == "status":
        show_status(root)
    elif not args.driver:
        parser.error("--driver is required")
    elif args.action == "trial":
        create_trial(root, args.driver, args.method)
    elif args.action == "hollow":
        create_hollow(root, args.driver)
    elif args.action == "publish":
        if not args.source:
            parser.error("publish requires --source")
        publish(root, args.driver, args.source.resolve())
    else:
        run_check(args, root, parser)


if __name__ == "__main__":
    main()
