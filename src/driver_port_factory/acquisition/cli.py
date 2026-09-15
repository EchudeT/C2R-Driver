from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from ..core.models import WorkflowError
from .execution import EvidenceAcquirer
from .planning import AcquisitionPlanner
from .verification import AcquisitionVerifier


def command_plan(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    plan = AcquisitionPlanner().plan(
        project,
        source_url=arguments.source_url,
        source_ref=arguments.source_ref,
        target_url=arguments.target_url,
        target_ref=arguments.target_ref,
        qemu_url=arguments.qemu_url,
        qemu_ref=arguments.qemu_ref,
        registry_path=Path(arguments.registry).resolve() if arguments.registry else None,
    )
    print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = EvidenceAcquirer().acquire(project)
    print(
        json.dumps(
            {
                "status": result.status.value,
                "target_worktree": result.target_worktree,
                "source_identity_consistent": result.source_identity_consistent,
                "checkouts": [record.to_dict() for record in result.checkouts],
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_verify(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = AcquisitionVerifier().verify(project)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    if not result["valid"]:
        raise WorkflowError("one or more acquired repositories failed verification")


def register_commands(commands: CommandRegistry) -> None:
    acquire = commands.add_parser(
        "acquire", help="pin and acquire source, target, and QEMU repositories"
    )
    acquire_commands = command_registry(acquire, dest="acquire_command")
    plan = acquire_commands.add_parser("plan")
    plan.add_argument("path")
    plan.add_argument("--source-url")
    plan.add_argument("--source-ref")
    plan.add_argument("--target-url")
    plan.add_argument("--target-ref")
    plan.add_argument("--qemu-url")
    plan.add_argument("--qemu-ref")
    plan.add_argument("--registry")
    plan.set_defaults(handler=command_plan)
    run = acquire_commands.add_parser("run")
    run.add_argument("path")
    run.set_defaults(handler=command_run)
    verify = acquire_commands.add_parser("verify")
    verify.add_argument("path")
    verify.set_defaults(handler=command_verify)
