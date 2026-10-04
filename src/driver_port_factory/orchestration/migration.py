from __future__ import annotations

from dataclasses import replace

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..control.contracts import ControlArtifact, ControlStage
from ..core.models import ActorRole, EvaluationMode, ProjectConfig, StageOwner, StageSpec
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..evaluation.contracts import EvaluationArtifact, EvaluationStage
from ..intake.contracts import IntakeArtifact, IntakeStage
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from ..migration.contracts import MigrationArtifact, MigrationStage
from ..sealing.contracts import SealingArtifact, SealingStage
from ..target_study.contracts import TargetStudyArtifact, TargetStudyStage
from .specification import StageRow, append_linear, stage_spec

MIGRATION_ROLES = (ActorRole.DEVELOPER, ActorRole.MIGRATION_OPERATOR)


def migration_workflow(config: ProjectConfig) -> tuple[StageSpec, ...]:
    specs = [
        stage_spec(
            ControlStage.PROJECT_INIT,
            "Freeze project identity and control configuration.",
            StageOwner.STATIC,
            None,
            (ControlArtifact.PROJECT_MANIFEST,),
            MIGRATION_ROLES,
        )
    ]
    previous = ControlStage.PROJECT_INIT
    if config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
        specs.append(
            stage_spec(
                EvaluationStage.BLIND_BINDING,
                "Import the prospective public bundle and curator commitment.",
                StageOwner.STATIC,
                previous,
                (EvaluationArtifact.PUBLIC_BUNDLE, EvaluationArtifact.CURATOR_COMMITMENT),
                MIGRATION_ROLES,
            )
        )
        previous = EvaluationStage.BLIND_BINDING

    previous = append_linear(specs, _migration_rows(config), previous, MIGRATION_ROLES)
    if config.enable_final_evidence_review:
        specs.append(
            stage_spec(
                MigrationStage.FINAL_EVIDENCE_REVIEW,
                "Independent AI checks functional completion against original requirements "
                "and actual evidence.",
                StageOwner.CODEX,
                previous,
                (MigrationArtifact.FINAL_EVIDENCE_REVIEW_REPORT,),
                MIGRATION_ROLES,
                prerequisites=(
                    MigrationStage.DRIVER_IMPLEMENTATION,
                    MigrationStage.CONTRACTS,
                    MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                    MigrationStage.ARTIFACT_PREPARATION,
                ),
            )
        )
        previous = MigrationStage.FINAL_EVIDENCE_REVIEW
    if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        if config.benchmark is not None:
            specs.append(
                stage_spec(
                    MigrationStage.BENCHMARK_VALIDATION,
                    "Execute the frozen benchmark and mechanically accept its complete results.",
                    StageOwner.STATIC,
                    previous,
                    (MigrationArtifact.BENCHMARK_REPORT,),
                    MIGRATION_ROLES,
                    auxiliary_outputs=(MigrationArtifact.BENCHMARK_ATTEMPT,),
                    prerequisites=(
                        MigrationStage.DRIVER_IMPLEMENTATION,
                        MigrationStage.ARTIFACT_PREPARATION,
                    ),
                )
            )
        return _data_dependencies(_unify(tuple(specs), config))
    specs.append(
        stage_spec(
            SealingStage.CANDIDATE_SEALING,
            "Seal an immutable candidate manifest and digest.",
            StageOwner.STATIC,
            previous,
            (
                SealingArtifact.CANDIDATE_MANIFEST,
                SealingArtifact.CANDIDATE_BUNDLE,
                SealingArtifact.CANDIDATE_LEDGER_EVENT,
                SealingArtifact.CANDIDATE_TIMESTAMP_RECEIPT,
                SealingArtifact.CANDIDATE_TRANSFER_RECORD,
            ),
            MIGRATION_ROLES,
        )
    )
    previous = SealingStage.CANDIDATE_SEALING
    if config.evaluation_mode is EvaluationMode.POST_HOC_SEALED_BLIND:
        specs.append(
            stage_spec(
                SealingStage.OPAQUE_DIGEST_EXPORT,
                "Export and externally anchor only the opaque candidate digest.",
                StageOwner.STATIC,
                previous,
                (SealingArtifact.CANDIDATE_DIGEST_ANCHOR,),
                MIGRATION_ROLES,
            )
        )
        previous = SealingStage.OPAQUE_DIGEST_EXPORT
    elif config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND:
        specs.append(
            stage_spec(
                SealingStage.CANDIDATE_TRANSFER,
                "Transfer the sealed candidate to the independent evaluator.",
                StageOwner.STATIC,
                previous,
                (
                    SealingArtifact.EVALUATOR_RECEIPT,
                    SealingArtifact.CANDIDATE_TRANSFER_RECORD,
                ),
                MIGRATION_ROLES,
            )
        )
        previous = SealingStage.CANDIDATE_TRANSFER
    specs.append(
        stage_spec(
            MigrationStage.COMPLETION_AUDIT,
            "Audit the sealed candidate chronology and final per-contract evidence read-only.",
            StageOwner.STATIC,
            previous,
            (MigrationArtifact.EVIDENCE_AUDIT,),
            MIGRATION_ROLES,
            prerequisites=(
                *(
                    (MigrationStage.FINAL_EVIDENCE_REVIEW,)
                    if config.enable_final_evidence_review
                    else ()
                ),
                MigrationStage.ARTIFACT_PREPARATION,
                MigrationStage.PUBLIC_QEMU_VALIDATION,
            ),
        )
    )
    return _data_dependencies(_unify(tuple(specs), config))


def _data_dependencies(specs):
    """Display order schedules work; these edges alone describe result invalidation."""
    changes = {
        EnvironmentStage.RECOVERY: (AcquisitionStage.REPOSITORY_ACQUISITION,),
        KnowledgeStage.KNOWLEDGE_BASE: (AcquisitionStage.EVIDENCE_CLOSURE,),
        TargetStudyStage.STUDY: (KnowledgeStage.KNOWLEDGE_BASE, EnvironmentStage.RECOVERY),
    }
    return tuple(
        replace(spec, dependencies=changes[spec.name]) if spec.name in changes else spec
        for spec in specs
    )


def _migration_rows(config: ProjectConfig) -> tuple[StageRow, ...]:
    return (
        StageRow(
            IntakeStage.REQUEST,
            "Persist the original request and required platform/driver fields.",
            StageOwner.STATIC,
            (IntakeArtifact.REQUEST_RECORD,),
        ),
        StageRow(
            IntakeStage.CANDIDATE_RESOLUTION,
            "Resolve canonical driver candidates using lightweight metadata only.",
            StageOwner.HYBRID,
            (IntakeArtifact.DRIVER_CANDIDATES,),
        ),
        StageRow(
            IntakeStage.SCOPE_CONFIRMATION,
            "Auto-confirm a unique scope or persist one consolidated user question.",
            StageOwner.HYBRID,
            (IntakeArtifact.SCOPE_CONFIRMATION,),
            (IntakeArtifact.CONFIRMATION_QUESTION,),
        ),
        StageRow(
            IntakeStage.ENVELOPE_FREEZE,
            "Freeze the canonical source entry, device, bus, included subset, and exclusions.",
            StageOwner.STATIC,
            (IntakeArtifact.MIGRATION_ENVELOPE, IntakeArtifact.IDENTITY_RECORD),
        ),
        StageRow(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            "Select and acquire source, target, and QEMU once; pin the downloaded commits.",
            StageOwner.HYBRID,
            (
                AcquisitionArtifact.REVISION_MANIFEST,
                AcquisitionArtifact.REPOSITORY_PLAN,
                AcquisitionArtifact.REPOSITORY_MANIFEST,
                AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION,
            ),
            (
                AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,
                AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,
            ),
            prerequisites=(IntakeStage.ENVELOPE_FREEZE,),
        ),
        StageRow(
            AcquisitionStage.EVIDENCE_CLOSURE,
            "Prepare scoped originals and explicit material gaps for route analysis.",
            StageOwner.HYBRID,
            (
                AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN,
                AcquisitionArtifact.MATERIALS_MANIFEST,
                AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY,
                AcquisitionArtifact.EVIDENCE_GAP_REGISTER,
                AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER,
            ),
            (
                AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL,
                AcquisitionArtifact.EVIDENCE_HTTP_CONTENT,
            ),
            prerequisites=(IntakeStage.ENVELOPE_FREEZE,),
        ),
        StageRow(
            EnvironmentStage.RECOVERY,
            "Validate the configured build/boot/check route without a driver capability survey.",
            StageOwner.HYBRID,
            (
                EnvironmentArtifact.INVENTORY,
                EnvironmentArtifact.MODE_CANDIDATES,
                EnvironmentArtifact.MODE_RECORD,
                EnvironmentArtifact.EXPERIMENT_READY_RUN,
                EnvironmentArtifact.EXPERIMENT_ROUTE,
            ),
            (
                (
                    EnvironmentArtifact.RECOVERY_ATTEMPT,
                    EnvironmentArtifact.PLATFORM_PROFILE,
                    EnvironmentArtifact.PLATFORM_VALIDATION,
                )
                if config.managed_platform
                else (EnvironmentArtifact.RECOVERY_ATTEMPT,)
            ),
        ),
        StageRow(
            KnowledgeStage.KNOWLEDGE_BASE,
            "Build or validate the evidence knowledge base and query contract.",
            StageOwner.STATIC,
            (
                KnowledgeArtifact.STATUS,
                KnowledgeArtifact.QUERY_CONTRACT,
                KnowledgeArtifact.GENERATED_SKILL,
                KnowledgeArtifact.READINESS_REPORT,
            ),
        ),
        StageRow(
            TargetStudyStage.STUDY,
            "Resolve source obligations, coarse route and critical premises in one joint analysis.",
            StageOwner.CODEX,
            (
                TargetStudyArtifact.REPORT,
                TargetStudyArtifact.ROUTE,
                TargetStudyArtifact.KNOWLEDGE_QUALITY,
            ),
            (TargetStudyArtifact.VALIDATION_ATTEMPT,),
        ),
        StageRow(
            MigrationStage.HANDOFF,
            "Freeze the verified bootstrap environment for downstream migration.",
            StageOwner.STATIC,
            (MigrationArtifact.HANDOFF,),
            prerequisites=(
                IntakeStage.ENVELOPE_FREEZE,
                AcquisitionStage.REPOSITORY_ACQUISITION,
                AcquisitionStage.EVIDENCE_CLOSURE,
                EnvironmentStage.RECOVERY,
                KnowledgeStage.KNOWLEDGE_BASE,
                *(
                    (EvaluationStage.BLIND_BINDING,)
                    if config.evaluation_mode is EvaluationMode.PROSPECTIVE_BLIND
                    else ()
                ),
            ),
        ),
        StageRow(
            MigrationStage.CONTRACTS,
            "Bind contracts and test oracles from the accepted joint analysis.",
            StageOwner.STATIC,
            (MigrationArtifact.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
            prerequisites=(
                MigrationStage.HANDOFF,
                KnowledgeStage.KNOWLEDGE_BASE,
                TargetStudyStage.STUDY,
                AcquisitionStage.REPOSITORY_ACQUISITION,
                AcquisitionStage.EVIDENCE_CLOSURE,
            ),
        ),
        *(
            (
                StageRow(
                    MigrationStage.ANALYSIS_REVIEW,
                    "Independently review analysis evidence, contracts and test provenance "
                    "before implementation.",
                    StageOwner.INDEPENDENT,
                    (MigrationArtifact.ANALYSIS_REVIEW_REPORT,),
                    prerequisites=(
                        TargetStudyStage.STUDY,
                        MigrationStage.HANDOFF,
                        AcquisitionStage.EVIDENCE_CLOSURE,
                        EnvironmentStage.RECOVERY,
                    ),
                ),
            )
            if (
                config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE
                and config.enable_analysis_review
            )
            else ()
        ),
        StageRow(
            MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
            "Implement and validate the minimal target framework interfaces "
            "required by the frozen migration contracts.",
            StageOwner.CODEX,
            (
                MigrationArtifact.TARGET_FRAMEWORK_BUNDLE,
                MigrationArtifact.TARGET_FRAMEWORK_REPORT,
                MigrationArtifact.TARGET_FRAMEWORK_CHANGE_INVENTORY,
            ),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.CONTRACTS,
                TargetStudyStage.STUDY,
                KnowledgeStage.KNOWLEDGE_BASE,
            ),
        ),
        StageRow(
            MigrationStage.DRIVER_IMPLEMENTATION,
            "Reconstruct the Rust driver, public tests, coverage, and minimal integration.",
            StageOwner.CODEX,
            (
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
                MigrationArtifact.COMPLIANCE_REPORT,
                MigrationArtifact.TARGET_CHANGE_INVENTORY,
            ),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.CONTRACTS,
                MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                TargetStudyStage.STUDY,
                KnowledgeStage.KNOWLEDGE_BASE,
            ),
        ),
        StageRow(
            MigrationStage.ARTIFACT_PREPARATION,
            "Build or inject the runtime artifact and prove its identity.",
            StageOwner.HYBRID,
            (MigrationArtifact.RUNTIME_ARTIFACT, MigrationArtifact.ARTIFACT_IDENTITY),
            (MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT, MigrationArtifact.RUNTIME_VARIANT),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationStage.TARGET_FRAMEWORK_ENABLEMENT,
                EnvironmentStage.RECOVERY,
            ),
        ),
        StageRow(
            MigrationStage.PUBLIC_QEMU_VALIDATION,
            "Run the public QEMU evidence ladder.",
            StageOwner.HYBRID,
            (MigrationArtifact.PUBLIC_QEMU_REPORT, MigrationArtifact.PUBLIC_QEMU_WORK_REPORT),
            (MigrationArtifact.PUBLIC_QEMU_ATTEMPT,),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.CONTRACTS,
                MigrationStage.DRIVER_IMPLEMENTATION,
                EnvironmentStage.RECOVERY,
                KnowledgeStage.KNOWLEDGE_BASE,
                AcquisitionStage.EVIDENCE_CLOSURE,
            ),
        ),
    )


def _unify(specs, config):
    """New runs have one implementation gate; historical workspaces retain their frozen DAG."""
    if not config.unified_implementation:
        return specs
    framework = next(s for s in specs if s.name is MigrationStage.TARGET_FRAMEWORK_ENABLEMENT)
    result = []
    for spec in specs:
        if spec is framework:
            continue
        dependencies = tuple(
            dict.fromkeys(
                MigrationStage.DRIVER_IMPLEMENTATION if dep is framework.name else dep
                for dep in spec.dependencies
            )
        )
        if spec.name is MigrationStage.DRIVER_IMPLEMENTATION:
            dependencies = tuple(
                dict.fromkeys(
                    (*framework.dependencies, *(d for d in dependencies if d is not spec.name))
                )
            )
            spec = replace(
                spec,
                description="Implement and integrate the driver, target adaptations "
                "and public tests.",
                required_outputs=(*framework.required_outputs, *spec.required_outputs),
            )
        result.append(replace(spec, dependencies=dependencies))
    return tuple(result)
