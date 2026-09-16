from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..codex.cli import run_codex_stage
from ..codex.contracts import CodexBackend
from ..composition import open_project
from ..core.contracts import ArtifactKey, StageKey
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from ..target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .contract_set import MigrationContractService, MigrationContractSet
from .contracts import MigrationArtifact, MigrationStage
from .handoff import MigrationHandoff

MIGRATION_CONTRACTS_OBJECTIVE = (
    "Build the Phase 4 evidence-backed migration contract set. Return only schema_version=1 "
    "JSON with contracts and gaps; each contract must contain id, requirement, four evidence "
    "lanes, source_function_refs, rust_design_intent, verification, evidence_status, and "
    "execution_status."
)


def _prompt_artifact(project, stage: StageKey, kind: ArtifactKey) -> dict[str, object]:
    reference = project.artifact(stage, kind)
    return {
        **reference.to_dict(),
        "path": str(project.artifacts.path_for_digest(reference.digest)),
    }


def command_handoff(arguments: argparse.Namespace) -> None:
    record = MigrationHandoff().create(open_project(Path(arguments.path)))
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2))


def command_contracts_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    inputs = {
        kind.value: _prompt_artifact(project, stage, kind)
        for stage, kind in (
            (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
            (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
            (
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            ),
        )
    }
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.CONTRACTS,
        objective=MIGRATION_CONTRACTS_OBJECTIVE,
        context={"frozen_inputs": inputs},
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    MigrationContractService().finalize(project, MigrationContractSet.read(response_path))
    print(
        json.dumps(
            {
                "job_id": codex_result.job_id,
                "prompt_sha256": rendered.digest,
                "response_path": str(response_path),
                "status": project.stage(MigrationStage.CONTRACTS).status.value,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def register_commands(commands: CommandRegistry) -> None:
    migration = commands.add_parser("migration", help="run controlled migration transitions")
    subcommands = command_registry(migration, dest="migration_command")
    handoff = subcommands.add_parser("handoff")
    handoff.add_argument("path")
    handoff.set_defaults(handler=command_handoff)

    contracts = commands.add_parser(
        "migration-contracts", help="generate and freeze evidence-backed migration contracts"
    )
    contract_commands = command_registry(contracts, dest="migration_contracts_command")
    run = contract_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_contracts_run)
