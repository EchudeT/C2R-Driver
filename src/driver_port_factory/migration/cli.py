from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..cli_support import CommandRegistry, command_registry
from ..codex.cli import run_codex_stage
from ..codex.contracts import CodexBackend
from ..composition import open_project
from ..core.contracts import ArtifactKey, StageKey
from ..core.models import WorkflowError
from ..core.project import Project
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
from ..target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .artifact_preparation import ArtifactPreparationPlan, ArtifactPreparationService
from .completion_audit import CompletionAuditService
from .compliance import ComplianceReport, ComplianceService
from .contract_set import MigrationContractService, MigrationContractSet
from .contracts import MigrationArtifact, MigrationStage
from .handoff import MigrationHandoff
from .implementation import DriverImplementationService, ImplementationResponse
from .public_qemu import PublicQemuPlan, PublicQemuService
from .public_repair import PublicRepairPlan, PublicRepairService
from .test_matrix import TestSelectionMatrix, TestSelectionService

MIGRATION_CONTRACTS_OBJECTIVE = (
    "Build the Phase 4 evidence-backed migration contract set. Return only schema_version=1 "
    "JSON with contracts and gaps; each contract must contain id, requirement, four evidence "
    "lanes, source_function_refs, rust_design_intent, verification, evidence_status, and "
    "execution_status."
)
TEST_ADAPTATION_OBJECTIVE = (
    "Build the Phase 5 public developer test-selection matrix. Return only schema_version=1 "
    "JSON with tests. Each test requires test_id, origin, source_test, primary_class, "
    "disposition, rationale, evidence, contract_ids, original_command, setup, stimulus, oracle, "
    "boundary_cases, negative_paths, cleanup, source_only_assertions_removed, adapter, "
    "limitations, expected_result, execution_status, and public_developer_evidence."
)
DRIVER_IMPLEMENTATION_OBJECTIVE = (
    "Implement Phase 6 in this writable target worktree in one pass. Reconstruct the complete "
    "Rust driver from the frozen contracts and structured C facts, adapt retained public tests, "
    "and make only necessity-backed minimal integration changes. Do not build, run QEMU, seal a "
    "candidate, or access private tests. Return only schema_version=1 JSON with files (path, role, "
    "sha256), coverage, target_changes, target_symbols, and unsafe_obligations. Each coverage "
    "record selects its complete structured source scope with domain and unit_ids; do not return "
    "individual source_facts or source_spans because the controller derives them from the frozen "
    "semantic indexes. Use an empty unit_ids list only for TEST_ASSERTION coverage."
)
TARGET_COMPLIANCE_OBJECTIVE = (
    "Perform the Phase 7 target compliance review once, using the frozen implementation and "
    "pinned target originals. Do not edit files, compile, run QEMU, or seal a candidate. Return "
    "only schema_version=1 JSON with status, areas, apis, target_changes, and execution. Every "
    "review has status, repair_target, summary, implementation_paths, target_evidence, "
    "unsafe_obligation_ids, and details; cite target-original chunk_id and record_id. Cover all "
    "required target rules, APIs, unsafe obligations, and pre-existing target changes. Set "
    "compile and runtime to NOT_RUN. A violation or evidence gap must not claim PASS and must "
    "identify IMPLEMENTATION or KNOWLEDGE repair."
)
TARGET_COMPLIANCE_AND_ARTIFACT_OBJECTIVE = (
    f"{TARGET_COMPLIANCE_OBJECTIVE} Return one JSON object with compliance_report containing that "
    "deliverable and artifact_preparation_plan containing the executable Phase 8 preparation "
    "plan derived from the same pinned target evidence."
)
PUBLIC_QEMU_OBJECTIVE = (
    "Freeze the public Phase 8 QEMU evidence plan for the current runtime artifact. Return only "
    "schema_version=1 JSON with runs and the complete ordered ladder. Bind every run to the "
    "provided artifact, implementation, packaged-test and environment-route identities; cover "
    "every QEMU contract and retained/adapted public test. Each run defines qemu, stimulus and "
    "external checker argv without shell syntax, device/topology/CPU/memory/backend, predeclared "
    "oracle, cleanup, controls and pinned QEMU-original evidence. Use EXECUTE, "
    "FROZEN_PREREQUISITE, NOT_APPLICABLE or BLOCKED honestly; do not include private tests."
)
PUBLIC_REPAIR_OBJECTIVE = (
    "Perform exactly one bounded Phase 9 diagnosis against the frozen failed public run. Query "
    "the local knowledge base and inspect each cited original before deciding. Do not edit files, "
    "build, run QEMU, seal a candidate, or use private evidence. Return only schema_version=1 JSON "
    "with attribution, action, failure_run_ids, contract_ids, test_ids, changed_paths (path and "
    "implementation role), a minimal UTF-8 unified patch, pinned evidence, rationale, and "
    "temporary_diagnostics_removed. Use APPLY only for DRIVER_TRANSLATION, ADAPTED_TEST, "
    "HARNESS_PACKAGING, SOURCE_ASSUMPTION, or a necessity-backed existing TARGET_API_PLATFORM "
    "path; otherwise use BLOCKED with an empty patch and changed_paths."
)


def _prompt_artifact(project: Project, stage: StageKey, kind: ArtifactKey) -> dict[str, object]:
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


def command_test_adaptation_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    inputs = {
        kind.value: _prompt_artifact(project, stage, kind)
        for stage, kind in (
            (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
            (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
            (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
        )
    }
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.TEST_ADAPTATION,
        objective=TEST_ADAPTATION_OBJECTIVE,
        context={"frozen_inputs": inputs},
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    TestSelectionService().finalize(project, TestSelectionMatrix.read(response_path))
    print(
        json.dumps(
            {
                "job_id": codex_result.job_id,
                "prompt_sha256": rendered.digest,
                "response_path": str(response_path),
                "status": project.stage(MigrationStage.TEST_ADAPTATION).status.value,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_driver_implementation_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    facts = project.load_json_artifact(
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
        SourceAnalysisArtifact.STRUCTURED_C_FACTS,
    )
    inputs = {
        kind.value: _prompt_artifact(project, stage, kind)
        for stage, kind in (
            (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
            (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
            (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
            (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.SOURCE_CLOSURE),
            (
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
            ),
        )
    }
    semantic_indexes = [
        {
            "unit_id": unit["unit_id"],
            "path": str((project.root / unit["semantic_index"]["path"]).resolve()),
            "sha256": unit["semantic_index"]["sha256"],
        }
        for unit in facts["units"]
    ]
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.DRIVER_IMPLEMENTATION,
        objective=DRIVER_IMPLEMENTATION_OBJECTIVE,
        context={"frozen_inputs": inputs, "semantic_indexes": semantic_indexes},
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    DriverImplementationService().finalize(project, ImplementationResponse.read(response_path))
    print(
        json.dumps(
            {
                "job_id": codex_result.job_id,
                "prompt_sha256": rendered.digest,
                "response_path": str(response_path),
                "status": project.stage(MigrationStage.DRIVER_IMPLEMENTATION).status.value,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_target_compliance_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    inputs = {
        kind.value: _prompt_artifact(project, stage, kind)
        for stage, kind in (
            (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
            (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
            (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.TRANSLATION_COVERAGE),
            (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.TARGET_CHANGE_INVENTORY),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.GENERATED_SKILL),
            (TargetStudyStage.STUDY, TargetStudyArtifact.STRUCTURED_PROFILE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.API_EVIDENCE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE),
            (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
        )
    }
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.TARGET_COMPLIANCE,
        objective=TARGET_COMPLIANCE_AND_ARTIFACT_OBJECTIVE,
        context={"frozen_inputs": inputs},
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    try:
        response = json.loads(response_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("Codex compliance response is not UTF-8 JSON") from error
    if not isinstance(response, dict) or set(response) != {
        MigrationArtifact.COMPLIANCE_REPORT.value,
        MigrationArtifact.ARTIFACT_PREPARATION_PLAN.value,
    }:
        raise WorkflowError("Codex compliance response has the wrong documents")
    ComplianceService().finalize(
        project,
        ComplianceReport.from_dict(response[MigrationArtifact.COMPLIANCE_REPORT.value]),
        ArtifactPreparationPlan.from_dict(
            response[MigrationArtifact.ARTIFACT_PREPARATION_PLAN.value]
        ),
    )
    print(
        json.dumps(
            {
                "job_id": codex_result.job_id,
                "prompt_sha256": rendered.digest,
                "response_path": str(response_path),
                "status": project.stage(MigrationStage.TARGET_COMPLIANCE).status.value,
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
    )


def command_artifact_preparation_run(arguments: argparse.Namespace) -> None:
    result = ArtifactPreparationService().run(
        open_project(Path(arguments.path)), Path(arguments.plan)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def command_public_qemu_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    inputs = {
        kind.value: _prompt_artifact(project, stage, kind)
        for stage, kind in (
            (MigrationStage.HANDOFF, MigrationArtifact.HANDOFF),
            (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
            (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
            (MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE),
            (MigrationStage.TARGET_COMPLIANCE, MigrationArtifact.COMPLIANCE_REPORT),
            (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT),
            (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
            (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_READY_RUN),
            (EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE),
            (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
            (SourceAnalysisStage.SOURCE_CLOSURE, SourceAnalysisArtifact.MATERIALS_MANIFEST),
        )
    }
    artifact = project.artifact(
        MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.RUNTIME_ARTIFACT
    )
    identity = project.load_json_artifact(
        MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY
    )
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.PUBLIC_QEMU_VALIDATION,
        objective=PUBLIC_QEMU_OBJECTIVE,
        context={
            "frozen_inputs": inputs,
            "runtime_artifact_path": str(project.artifacts.path_for_digest(artifact.digest)),
            "runtime_artifact_sha256": artifact.digest,
            "implementation_sha256": identity["inputs"][
                MigrationArtifact.IMPLEMENTATION_BUNDLE.value
            ]["digest"],
            "packaged_test_sha256": identity["packaged_test_artifact"]["sha256"],
        },
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    result = PublicQemuService().run(project, PublicQemuPlan.read(response_path))
    result.update(job_id=codex_result.job_id, prompt_sha256=rendered.digest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def command_public_repair_run(arguments: argparse.Namespace) -> None:
    project = open_project(Path(arguments.path))
    service = PublicRepairService()
    prepared = service.prepare(project)
    if prepared is None:
        result = service.finalize_not_applicable(project)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return
    source_ref, failure = prepared
    bundle = project.load_json_artifact(
        MigrationStage.DRIVER_IMPLEMENTATION, MigrationArtifact.IMPLEMENTATION_BUNDLE
    )
    codex_result, rendered, response_path = run_codex_stage(
        project,
        MigrationStage.PUBLIC_REPAIR,
        objective=PUBLIC_REPAIR_OBJECTIVE,
        context={
            "failed_attempt": source_ref,
            "failed_evidence": failure,
            "implementation_files": [
                {"path": item["path"], "role": item["role"]} for item in bundle["files"]
            ],
            "frozen_inputs": {
                kind.value: _prompt_artifact(project, stage, kind)
                for stage, kind in (
                    (MigrationStage.CONTRACTS, MigrationArtifact.CONTRACTS),
                    (MigrationStage.TEST_ADAPTATION, MigrationArtifact.TEST_PORT_MATRIX),
                    (
                        MigrationStage.DRIVER_IMPLEMENTATION,
                        MigrationArtifact.IMPLEMENTATION_BUNDLE,
                    ),
                    (MigrationStage.ARTIFACT_PREPARATION, MigrationArtifact.ARTIFACT_IDENTITY),
                    (KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT),
                    (TargetStudyStage.STUDY, TargetStudyArtifact.CHANGE_PLAN),
                )
            },
        },
        backend=arguments.backend,
        codex_bin=arguments.codex_bin,
        model=arguments.model,
    )
    result = service.run(project, PublicRepairPlan.read(response_path), source_ref, failure)
    result.update(job_id=codex_result.job_id, prompt_sha256=rendered.digest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))


def command_completion_audit_run(arguments: argparse.Namespace) -> None:
    audit = CompletionAuditService().run(open_project(Path(arguments.path)))
    print(json.dumps(audit["summary"], ensure_ascii=False, sort_keys=True, indent=2))


def register_commands(commands: CommandRegistry) -> None:  # noqa: PLR0915
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

    adaptation = commands.add_parser(
        "test-adaptation", help="classify and freeze public source-test adaptations"
    )
    adaptation_commands = command_registry(adaptation, dest="test_adaptation_command")
    run = adaptation_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_test_adaptation_run)

    implementation = commands.add_parser(
        "driver-implementation", help="implement and freeze the Rust driver and public tests"
    )
    implementation_commands = command_registry(implementation, dest="driver_implementation_command")
    run = implementation_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_driver_implementation_run)

    compliance = commands.add_parser(
        "target-compliance", help="review and freeze target-platform compliance"
    )
    compliance_commands = command_registry(compliance, dest="target_compliance_command")
    run = compliance_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_target_compliance_run)

    preparation = commands.add_parser(
        "artifact-preparation", help="build or inject and freeze the current driver artifact"
    )
    preparation_commands = command_registry(preparation, dest="artifact_preparation_command")
    run = preparation_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument("--plan", required=True)
    run.set_defaults(handler=command_artifact_preparation_run)

    qemu = commands.add_parser(
        "public-qemu-validation", help="execute and freeze the public QEMU evidence ladder"
    )
    qemu_commands = command_registry(qemu, dest="public_qemu_validation_command")
    run = qemu_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_public_qemu_run)

    repair = commands.add_parser(
        "public-repair", help="attribute one public failure and run one bounded repair attempt"
    )
    repair_commands = command_registry(repair, dest="public_repair_command")
    run = repair_commands.add_parser("run")
    run.add_argument("path")
    run.add_argument(
        "--backend", type=CodexBackend, choices=list(CodexBackend), default=CodexBackend.EXEC
    )
    run.add_argument("--codex-bin", default="codex")
    run.add_argument("--model")
    run.set_defaults(handler=command_public_repair_run)

    audit = commands.add_parser(
        "completion-audit", help="freeze the final evidence and lineage audit"
    )
    audit_commands = command_registry(audit, dest="completion_audit_command")
    run = audit_commands.add_parser("run")
    run.add_argument("path")
    run.set_defaults(handler=command_completion_audit_run)
