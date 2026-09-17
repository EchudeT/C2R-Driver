from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .service import TargetStudyService


def command_validate(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = TargetStudyService().validate(
        project,
        profile_json=Path(arguments.profile_json),
        api_table=Path(arguments.api_table),
        analogous_trace=Path(arguments.analogous_trace),
        change_plan=Path(arguments.change_plan),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "stage_status": result.stage_status.value,
                "report_path": result.report_path,
                "errors": list(result.errors),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def register_commands(commands: CommandRegistry) -> None:
    study = commands.add_parser(
        "target-study", help="validate the target profile and API evidence gate"
    )
    subcommands = command_registry(study, dest="target_study_command")
    validate = subcommands.add_parser("validate")
    validate.add_argument("path")
    validate.add_argument("--profile-json", required=True)
    validate.add_argument("--api-table", required=True)
    validate.add_argument("--analogous-trace", required=True)
    validate.add_argument("--change-plan", required=True)
    validate.set_defaults(handler=command_validate)
