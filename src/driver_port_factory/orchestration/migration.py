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
    specs.append(
        stage_spec(
            MigrationStage.PUBLIC_REPAIR,
            "Independent AI checks functional completion against original requirements and actual evidence.",
            StageOwner.CODEX,
            previous,
            (MigrationArtifact.PUBLIC_REPAIR_REPORT,),
            MIGRATION_ROLES,
            prerequisites=(MigrationStage.DRIVER_IMPLEMENTATION, MigrationStage.CONTRACTS),
        )
    )
    if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        return _data_dependencies(tuple(specs))
    specs.append(
        stage_spec(
            SealingStage.CANDIDATE_SEALING,
            "Seal an immutable candidate manifest and digest.",
            StageOwner.STATIC,
            MigrationStage.PUBLIC_REPAIR,
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
        )
    )
    return _data_dependencies(tuple(specs))


def _data_dependencies(specs):
    """Display order schedules work; these edges alone describe result invalidation."""
    changes = {
        EnvironmentStage.RECOVERY: (AcquisitionStage.REPOSITORY_ACQUISITION,),
        KnowledgeStage.KNOWLEDGE_BASE: (AcquisitionStage.EVIDENCE_CLOSURE,),
        TargetStudyStage.STUDY: (KnowledgeStage.KNOWLEDGE_BASE, EnvironmentStage.RECOVERY),
    }
    return tuple(replace(spec, dependencies=changes[spec.name])
                 if spec.name in changes else spec for spec in specs)


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
            (AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,
             AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,),
            prerequisites=(IntakeStage.ENVELOPE_FREEZE,),
        ),
        StageRow(
            AcquisitionStage.EVIDENCE_CLOSURE,
            "Close every required evidence facet with controlled content or an audited gap.",
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
            prerequisites=(
                IntakeStage.ENVELOPE_FREEZE,
            ),
        ),
        StageRow(
            EnvironmentStage.RECOVERY,
            "Establish artifact mode and a concrete executable experiment route.",
            StageOwner.HYBRID,
            (
                EnvironmentArtifact.INVENTORY,
                EnvironmentArtifact.MODE_CANDIDATES,
                EnvironmentArtifact.MODE_RECORD,
                EnvironmentArtifact.EXPERIMENT_READY_RUN,
                EnvironmentArtifact.EXPERIMENT_ROUTE,
            ),
            (EnvironmentArtifact.RECOVERY_ATTEMPT,),
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
            "Build the target profile, API evidence table, and analogous call chain.",
            StageOwner.CODEX,
            (
                TargetStudyArtifact.REPORT,
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
            "Analyze source behavior, design migration contracts and plan tests in one worker task.",
            StageOwner.HYBRID,
            (MigrationArtifact.CONTRACTS, MigrationArtifact.TEST_PORT_MATRIX),
            prerequisites=(
                MigrationStage.HANDOFF,
                KnowledgeStage.KNOWLEDGE_BASE,
                TargetStudyStage.STUDY,
                AcquisitionStage.REPOSITORY_ACQUISITION,
                AcquisitionStage.EVIDENCE_CLOSURE,
            ),
        ),
        *((StageRow(
            MigrationStage.ANALYSIS_REVIEW,
            "Independently review analysis evidence, contracts and test provenance before implementation.",
            StageOwner.INDEPENDENT,
            (MigrationArtifact.ANALYSIS_REVIEW_REPORT,),
            prerequisites=(TargetStudyStage.STUDY, MigrationStage.HANDOFF,
                           AcquisitionStage.EVIDENCE_CLOSURE,
                           EnvironmentStage.RECOVERY),
        ),) if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE else ()),
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
