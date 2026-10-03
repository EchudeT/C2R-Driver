"""Platform commands share the project role and execution boundary."""

import json
import signal
from pathlib import Path

from ..cli_support import command_registry
from ..composition import open_project
from . import service


def _cancel(signum, _frame):
    # Unwind executor cleanup instead of abandoning a running Docker container.
    raise SystemExit(128 + signum)


def command(args):
    previous = signal.signal(signal.SIGTERM, _cancel)
    try:
        _command(args)
    finally:
        signal.signal(signal.SIGTERM, previous)


def _command(args):
    # Execution writes receipts and artifacts: retain full project integrity checks.
    project = open_project(Path(args.path), read_only=args.platform_action == "status")
    if args.platform_action == "prepare":
        value = service.prepare(project, args.image, args.accelerator)
    elif args.platform_action == "verify":
        value = service.verify(project)
    elif args.platform_action == "build":
        value = service.build(project)
    elif args.platform_action == "format":
        from .formatting import run

        value = run(project, args.package, write=args.write)
    elif args.platform_action == "presence":
        value = service.presence(project)
    elif args.platform_action == "run-case":
        value = service.run_case(project, Path(args.case))
    else:
        value = service.context(project)
    print(json.dumps(value, indent=2))


def register(commands):
    root = commands.add_parser("platform", help="managed baseline/build/guest execution")
    subs = command_registry(root, dest="platform_action")
    for name in ("prepare", "verify", "build", "format", "run-case", "presence", "status"):
        parser = subs.add_parser(name)
        parser.add_argument("path")
        parser.set_defaults(handler=command)
        if name == "prepare":
            parser.add_argument("--image", required=True)
            parser.add_argument("--accelerator", choices=("kvm", "tcg"), required=True)
        if name == "run-case":
            parser.add_argument("case")
        if name == "format":
            parser.add_argument("--package", action="append", required=True)
            parser.add_argument(
                "--write", action="store_true", help="apply formatting; default check only"
            )
