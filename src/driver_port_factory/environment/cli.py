from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .execution import ExperimentExecutor
from .inventory import EnvironmentInspector
from .planning import ExperimentPlanRegistrar


def command_inspect(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = EnvironmentInspector().inspect(project)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def command_plan(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    plan = ExperimentPlanRegistrar().register(project, Path(arguments.file))
    print(json.dumps(plan.to_dict(), ensure_ascii=False, sort_keys=True, indent=2))


def command_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = ExperimentExecutor().run(project, arguments.route_id)
    print(
        json.dumps(
            {
                "route_id": result.route_id,
                "readiness": result.readiness.value,
                "stage_status": result.stage_status.value,
                "attempt_path": result.attempt_path,
                "message": result.message,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def register_commands(commands: CommandRegistry) -> None:
    environment = commands.add_parser(
        "environment", help="discover and execute an EXPERIMENT_READY route"
    )
    environment_commands = command_registry(environment, dest="environment_command")
    inspect = environment_commands.add_parser("inspect")
    inspect.add_argument("path")
    inspect.set_defaults(handler=command_inspect)
    plan = environment_commands.add_parser("plan")
    plan.add_argument("path")
    plan.add_argument("--file", required=True)
    plan.set_defaults(handler=command_plan)
    run = environment_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument("--route-id", required=True)
    run.set_defaults(handler=command_run)
