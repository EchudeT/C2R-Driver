from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .service import IntakeService


def command_analyze(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = IntakeService().analyze(
        project,
        raw_request=arguments.request,
        catalog_paths=tuple(Path(path).resolve() for path in (arguments.catalog or ())),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "selected_candidate_id": result.selected_candidate_id,
                "question": result.question,
                "candidates": [candidate.to_dict() for candidate in result.candidates],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_answer(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    answer_text = arguments.answer or (
        f"Selected candidate {arguments.candidate_id}"
        if arguments.candidate_id
        else "Confirmed manually supplied driver identity"
    )
    result = IntakeService().answer(
        project,
        candidate_id=arguments.candidate_id,
        answer_text=answer_text,
        canonical_name=arguments.canonical_name,
        source_path=arguments.source_path,
        device_family=arguments.device_family,
        bus=arguments.bus,
        intended_subset=tuple(arguments.intended_subset or ()),
        excluded_variants=tuple(arguments.exclude or ()),
    )
    print(
        json.dumps(
            {
                "status": result.status.value,
                "selected_candidate_id": result.selected_candidate_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def command_show(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    print(json.dumps(IntakeService().show(project), ensure_ascii=False, sort_keys=True, indent=2))


def register_commands(commands: CommandRegistry) -> None:
    intake = commands.add_parser("intake", help="analyze and freeze the requested driver scope")
    intake_commands = command_registry(intake, dest="intake_command")
    analyze = intake_commands.add_parser("analyze")
    analyze.add_argument("path")
    analyze.add_argument("--request", required=True)
    analyze.add_argument(
        "--catalog",
        action="append",
        help="versioned lightweight metadata catalog JSON; may be repeated",
    )
    analyze.set_defaults(handler=command_analyze)
    answer = intake_commands.add_parser("answer")
    answer.add_argument("path")
    answer.add_argument("--candidate-id")
    answer.add_argument("--answer")
    answer.add_argument("--canonical-name")
    answer.add_argument("--source-path")
    answer.add_argument("--device-family")
    answer.add_argument("--bus")
    answer.add_argument("--intended-subset", action="append")
    answer.add_argument("--exclude", action="append")
    answer.set_defaults(handler=command_answer)
    show = intake_commands.add_parser("show")
    show.add_argument("path")
    show.set_defaults(handler=command_show)
