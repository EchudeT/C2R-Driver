from __future__ import annotations

import argparse
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..composition import ARTIFACT_VALIDATORS, initialize_project, open_project
from ..core.models import (
    ActorRole,
    ArtifactDirection,
    EvaluationMode,
    FileArtifact,
    ProjectConfig,
    StageStatus,
    WorkflowError,
)
from .contracts import LedgerVerificationStatus


def command_init(arguments: argparse.Namespace) -> None:
    root = Path(arguments.path).resolve()
    config = ProjectConfig(
        project_id=arguments.project_id or root.name,
        source_platform=arguments.source,
        target_platform=arguments.target,
        driver_name=arguments.driver,
        evaluation_mode=arguments.mode,
        actor_role=arguments.role,
        skill_root=str(Path(arguments.skill_root).resolve()) if arguments.skill_root else None,
        prompt_pack=str(Path(arguments.prompt_pack).resolve()) if arguments.prompt_pack else None,
    )
    initialize_project(root, config)
    print(root)


def command_status(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    print(
        f"project={project.config.project_id} role={project.config.actor_role.value} "
        f"mode={project.config.evaluation_mode.value}"
    )
    for stage in project.stages():
        dependencies = ",".join(dependency.value for dependency in stage.dependencies) or "-"
        print(
            f"{stage.position + 1:02d} {stage.status.value:17} {stage.owner.value:11} "
            f"{stage.name.value:28} deps={dependencies}"
        )


def command_stage_start(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    project.start(stage)
    print(f"started {stage.value}")


def command_stage_complete(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    project.complete(stage, arguments.outcome, message=arguments.message)
    print(f"{stage.value} -> {arguments.outcome.value}")


def command_artifact_add(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    kind = ARTIFACT_VALIDATORS.parse(arguments.kind)
    digest = project.record_artifact(
        stage,
        FileArtifact(kind, Path(arguments.file)),
        direction=arguments.direction,
    )
    print(digest)


def command_ledger_verify(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    valid = project.verify_event_chain()
    result = LedgerVerificationStatus.PASS if valid else LedgerVerificationStatus.FAIL
    print(result.value)
    if not valid:
        raise WorkflowError("event ledger verification failed")


def register_commands(commands: CommandRegistry) -> None:
    init = commands.add_parser("init", help="initialize a role-specific project workspace")
    init.add_argument("path")
    init.add_argument("--project-id")
    init.add_argument("--source", required=True)
    init.add_argument("--target", required=True)
    init.add_argument("--driver", required=True)
    init.add_argument("--mode", type=EvaluationMode, choices=list(EvaluationMode), required=True)
    init.add_argument("--role", type=ActorRole, choices=list(ActorRole), required=True)
    init.add_argument("--skill-root")
    init.add_argument("--prompt-pack", help="editable prompt-pack directory used by default")
    init.set_defaults(handler=command_init)

    status = commands.add_parser("status", help="show the stage DAG and current status")
    status.add_argument("path")
    status.set_defaults(handler=command_status)

    stage = commands.add_parser("stage", help="manually drive a stage")
    stage_commands = command_registry(stage, dest="stage_command")
    start = stage_commands.add_parser("start")
    start.add_argument("path")
    start.add_argument("stage")
    start.set_defaults(handler=command_stage_start)
    complete = stage_commands.add_parser("complete")
    complete.add_argument("path")
    complete.add_argument("stage")
    complete.add_argument(
        "--outcome",
        type=StageStatus,
        required=True,
        choices=[
            StageStatus.FAIL,
            StageStatus.BLOCKED,
            StageStatus.INCONCLUSIVE,
            StageStatus.NOT_APPLICABLE,
        ],
    )
    complete.add_argument("--message")
    complete.set_defaults(handler=command_stage_complete)

    artifact = commands.add_parser("artifact", help="register immutable stage artifacts")
    artifact_commands = command_registry(artifact, dest="artifact_command")
    add = artifact_commands.add_parser("add")
    add.add_argument("path")
    add.add_argument("stage")
    add.add_argument("kind")
    add.add_argument("file")
    add.add_argument(
        "--direction",
        type=ArtifactDirection,
        choices=list(ArtifactDirection),
        default=ArtifactDirection.OUTPUT,
    )
    add.set_defaults(handler=command_artifact_add)

    ledger = commands.add_parser("ledger", help="audit the hash-chained event log")
    ledger_commands = command_registry(ledger, dest="ledger_command")
    verify = ledger_commands.add_parser("verify")
    verify.add_argument("path")
    verify.set_defaults(handler=command_ledger_verify)
