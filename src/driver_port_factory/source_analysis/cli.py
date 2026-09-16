from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..cli_support import CommandRegistry, command_registry
from ..codex.cli import run_codex_stage
from ..codex.contracts import CodexBackend
from ..composition import open_project
from ..migration.contracts import MigrationArtifact, MigrationStage
from .clang_backend import AnalyzerFamily
from .closure import SourceClosureService
from .contracts import SourceAnalysisStage
from .structured import StructuredCAnalysisService

SOURCE_CLOSURE_OBJECTIVE = (
    "Close the behaviorally required source set using the frozen compile commands. "
    "Return only one source-closure JSON object for the controller to verify."
)


def command_closure_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    handoff = project.load_json_artifact(MigrationStage.HANDOFF, MigrationArtifact.HANDOFF)
    handoff_ref = project.artifact(MigrationStage.HANDOFF, MigrationArtifact.HANDOFF)
    source = load_repository_acquisition(project).checkout(RepositoryRole.SOURCE)
    codex_result, rendered, response_path = run_codex_stage(
        project,
        SourceAnalysisStage.SOURCE_CLOSURE,
        objective=SOURCE_CLOSURE_OBJECTIVE,
        context={
            "migration_handoff": handoff_ref.to_dict(),
            "driver_identity": handoff["identity"],
            "source_repository": {
                "root": str((project.root / source.checkout_path).resolve()),
                "revision": source.resolved_commit,
                "read_only": True,
            },
            "source_paths": handoff["evidence"]["source_paths"],
            "source_test_paths": handoff["evidence"]["source_test_paths"],
            "known_gaps": handoff["evidence"]["known_gaps"],
            "knowledge": handoff["knowledge"],
        },
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    result = SourceClosureService().validate(
        project,
        closure_path=response_path,
    )
    print(
        json.dumps(
            {
                "job_id": codex_result.job_id,
                "prompt_sha256": rendered.digest,
                "response_path": str(response_path),
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
        "source-closure", help="discover, verify, and freeze the required C source closure"
    )
    closure_commands = command_registry(closure, dest="source_closure_command")
    run = closure_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_closure_run)

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
