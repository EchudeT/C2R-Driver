from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import open_project
from .clang_backend import AnalyzerFamily
from .closure import SourceClosureService
from .structured import StructuredCAnalysisService


def command_closure_validate(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = SourceClosureService().validate(
        project,
        closure_path=Path(arguments.closure),
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


def command_structured_analyze(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    result = StructuredCAnalysisService().analyze(
        project,
        analyzer=arguments.analyzer,
        analyzer_family=arguments.analyzer_family,
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
    closure = commands.add_parser(
        "source-closure", help="validate and freeze the behaviorally required C source closure"
    )
    closure_commands = command_registry(closure, dest="source_closure_command")
    validate = closure_commands.add_parser("validate")
    validate.add_argument("path")
    validate.add_argument("--closure", required=True)
    validate.set_defaults(handler=command_closure_validate)

    structured = commands.add_parser(
        "structured-c", help="extract typed Clang/LLVM semantic facts from the frozen C closure"
    )
    structured_commands = command_registry(structured, dest="structured_c_command")
    analyze = structured_commands.add_parser("analyze")
    analyze.add_argument("path")
    analyze.add_argument("--analyzer", default="clang")
    analyze.add_argument(
        "--analyzer-family",
        type=AnalyzerFamily,
        choices=list(AnalyzerFamily),
        default=AnalyzerFamily.CLANG_LLVM,
    )
    analyze.set_defaults(handler=command_structured_analyze)
