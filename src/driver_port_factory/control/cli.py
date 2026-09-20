from __future__ import annotations

import argparse
import json
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
from .statistics import cost_text, duration, project_statistics
from .runtime import controller_run, controller_status


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
    stats = project_statistics(project, pricing_model=arguments.pricing_model,
                               pricing_tier=arguments.pricing_tier)
    stats["controller"] = controller_status(project)
    if arguments.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return
    print(
        f"project={project.config.project_id} role={project.config.actor_role.value} "
        f"mode={project.config.evaluation_mode.value}"
    )
    print(f"controller={stats['controller']['state']} "
          f"{stats['controller'].get('note', '')}")
    from ..core.phases import GROUPS, phase
    stages = project.stages()
    for group in GROUPS:
        members = [s for s in stages if phase(s.name) == group]
        if members:
            done = all(s.status.value in {"PASS", "NOT_APPLICABLE"} for s in members)
            print(f"phase={group} state={'COMPLETE' if done else 'INCOMPLETE'}")
    for stage, row in zip(project.stages(), stats["stages"]):
        dependencies = ",".join(dependency.value for dependency in stage.dependencies) or "-"
        print(
            f"{stage.position + 1:02d} {stage.status.value:17} {stage.owner.value:11} "
            f"{stage.name.value:28} time={duration(row['elapsed_seconds'])} "
            f"attempts={row['attempts']} deps={dependencies}"
        )
        if stage.status.value in {"READY", "RUNNING"}:
            repair = project.retry_feedback(stage.name)
            if repair:
                print(f"   repair={repair['status']} root={repair.get('repair_root', repair['stage'])} "
                      f"trigger={repair['trigger']} (evidence refresh; existing work retained)")
        if row["codex_calls"]:
            usage = row["usage"]
            print(f"   codex={row['codex_calls']} input={usage['input_tokens']:,} "
                  f"cached={usage['cached_input_tokens']:,} output={usage['output_tokens']:,} "
                  f"USD~{cost_text(row)} "
                  f"unknown_usage={row['unknown_usage_calls']} unpriced={row['unpriced_calls']}")
    total = stats["totals"]
    print(f"TOTAL time={duration(total['elapsed_seconds'])} "
          f"waiting={duration(total['waiting_seconds'])} codex={total['codex_calls']} "
          f"input={total['usage']['input_tokens']:,} output={total['usage']['output_tokens']:,} "
          f"USD~{cost_text(total)} "
          f"unpriced={total['unpriced_calls']}")
    print(stats["note"])
    print(f"Prices ({stats['price_date']}): {stats['price_source']}")


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


def command_stage_reopen(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    with controller_run(project):
        project.reopen_blocked(stage, reason=arguments.reason)
    print(f"{stage.value} -> READY; resume port with the existing worker session")


def command_artifact_add(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    stage = project.workflow.parse_stage(arguments.stage)
    kind = ARTIFACT_VALIDATORS.parse(arguments.kind)
    occurrence = project.record_artifact(
        stage,
        FileArtifact(kind, Path(arguments.file)),
        direction=arguments.direction,
    )
    print(occurrence.digest)


def command_ledger_verify(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    valid = project.verify_event_chain()
    result = LedgerVerificationStatus.PASS if valid else LedgerVerificationStatus.FAIL
    print(result.value)
    if not valid:
        raise WorkflowError("event ledger verification failed")


def register_commands(commands: CommandRegistry) -> None:
    upgrade = commands.add_parser("upgrade-protocol", help="upgrade an idle pre-analysis run in place")
    upgrade.add_argument("path")
    def perform_upgrade(arguments):
        from .protocol_upgrade import upgrade
        print(json.dumps(upgrade(arguments.path)))
    upgrade.set_defaults(handler=perform_upgrade)
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
    status.add_argument("--json", action="store_true", help="machine-readable stage/call statistics")
    status.add_argument("--pricing-model", help="explicit model for historical calls missing model metadata")
    status.add_argument("--pricing-tier", choices=("standard", "fast", "flex", "batch"),
                        help="override service tier for the public-price estimate")
    status.set_defaults(handler=command_status)

    stage = commands.add_parser("stage", help="manually drive a stage")
    stage_commands = command_registry(stage, dest="stage_command")
    reopen = stage_commands.add_parser("reopen", help="operator: reopen a resolved BLOCKED stage")
    reopen.add_argument("path")
    reopen.add_argument("stage")
    reopen.add_argument("--reason", required=True)
    reopen.set_defaults(handler=command_stage_reopen)
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
