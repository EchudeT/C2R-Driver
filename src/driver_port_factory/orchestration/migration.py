from __future__ import annotations

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..control.contracts import ControlArtifact, ControlStage
from ..core.models import ActorRole, EvaluationMode, ProjectConfig, StageOwner, StageSpec
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..evaluation.contracts import EvaluationArtifact, EvaluationStage
from ..intake.contracts import IntakeArtifact, IntakeStage
from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
from ..migration.contracts import MigrationArtifact, MigrationStage
from ..sealing.contracts import SealingArtifact, SealingStage
from ..source_analysis.contracts import SourceAnalysisArtifact, SourceAnalysisStage
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
            "Diagnose public failures and run bounded narrow repair attempts.",
            StageOwner.HYBRID,
            previous,
            (MigrationArtifact.PUBLIC_REPAIR_REPORT,),
            MIGRATION_ROLES,
            auxiliary_outputs=(MigrationArtifact.PUBLIC_REPAIR_ATTEMPT,),
            accept_failed=True,
        )
    )
    if config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        specs.append(
            stage_spec(
                MigrationStage.COMPLETION_AUDIT,
                "Audit contract, implementation, test, and runtime coverage.",
                StageOwner.STATIC,
                MigrationStage.PUBLIC_REPAIR,
                (MigrationArtifact.EVIDENCE_AUDIT,),
                MIGRATION_ROLES,
            )
        )
        return tuple(specs)

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
    return tuple(specs)


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
            AcquisitionStage.REVISION_SELECTION,
            "Pin source, target, and QEMU revisions.",
            StageOwner.HYBRID,
            (
                AcquisitionArtifact.REVISION_MANIFEST,
                AcquisitionArtifact.REPOSITORY_PLAN,
                AcquisitionArtifact.REVISION_EVIDENCE_CONTENT,
            ),
            (AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,),
            (AcquisitionArtifact.REVISION_EVIDENCE_CONTENT,),
        ),
        StageRow(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            "Acquire immutable source, target, and QEMU repository baselines.",
            StageOwner.STATIC,
            (
                AcquisitionArtifact.REPOSITORY_MANIFEST,
                AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION,
            ),
            (AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT,),
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
                AcquisitionStage.REVISION_SELECTION,
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
            StageOwner.HYBRID,
            (
                KnowledgeArtifact.STATUS,
                KnowledgeArtifact.QUERY_CONTRACT,
                KnowledgeArtifact.GENERATED_SKILL,
                KnowledgeArtifact.READINESS_REPORT,
                KnowledgeArtifact.TARGET_PROBE_RESULTS,
            ),
            (KnowledgeArtifact.PROBE_ATTEMPT,),
        ),
        StageRow(
            TargetStudyStage.STUDY,
            "Build the target profile, API evidence table, and analogous call chain.",
            StageOwner.CODEX,
            (
                TargetStudyArtifact.PROFILE,
                TargetStudyArtifact.STRUCTURED_PROFILE,
                TargetStudyArtifact.API_EVIDENCE,
                TargetStudyArtifact.ANALOGOUS_DRIVER_TRACE,
                TargetStudyArtifact.CHANGE_PLAN,
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
                AcquisitionStage.REVISION_SELECTION,
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
            SourceAnalysisStage.SOURCE_CLOSURE,
            "Close the behaviorally required C dependency set.",
            StageOwner.HYBRID,
            (
                SourceAnalysisArtifact.SOURCE_CLOSURE,
                SourceAnalysisArtifact.SOURCE_CLOSURE_REPORT,
                SourceAnalysisArtifact.COMPILE_MANIFEST,
                SourceAnalysisArtifact.COMPILATION_DATABASE,
                SourceAnalysisArtifact.MATERIALS_MANIFEST,
                SourceAnalysisArtifact.KNOWLEDGE_REVISION,
            ),
            (SourceAnalysisArtifact.SOURCE_CLOSURE_VALIDATION_ATTEMPT,),
            prerequisites=(
                AcquisitionStage.EVIDENCE_CLOSURE,
                AcquisitionStage.REPOSITORY_ACQUISITION,
                KnowledgeStage.KNOWLEDGE_BASE,
            ),
        ),
        StageRow(
            SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
            "Extract typed AST, CFG, layout, call, global, and effect facts.",
            StageOwner.STATIC,
            (
                SourceAnalysisArtifact.STRUCTURED_C_FACTS,
                SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_REPORT,
                SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT,
                SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
            ),
            (SourceAnalysisArtifact.STRUCTURED_C_ANALYSIS_ATTEMPT,),
            (
                SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT,
                SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
            ),
        ),
        StageRow(
            MigrationStage.CONTRACTS,
            "Convert evidence into hardware, platform, safety, and lifecycle obligations.",
            StageOwner.HYBRID,
            (MigrationArtifact.CONTRACTS,),
            prerequisites=(
                MigrationStage.HANDOFF,
                KnowledgeStage.KNOWLEDGE_BASE,
                TargetStudyStage.STUDY,
                SourceAnalysisStage.SOURCE_CLOSURE,
            ),
        ),
        StageRow(
            MigrationStage.TEST_ADAPTATION,
            "Triage source tests and preserve portable device scenarios and oracles.",
            StageOwner.HYBRID,
            (MigrationArtifact.TEST_PORT_MATRIX,),
            prerequisites=(
                MigrationStage.HANDOFF,
                KnowledgeStage.KNOWLEDGE_BASE,
                TargetStudyStage.STUDY,
                SourceAnalysisStage.SOURCE_CLOSURE,
            ),
        ),
        StageRow(
            MigrationStage.DRIVER_IMPLEMENTATION,
            "Reconstruct the Rust driver, public tests, coverage, and minimal integration.",
            StageOwner.CODEX,
            (
                MigrationArtifact.IMPLEMENTATION_BUNDLE,
                MigrationArtifact.TRANSLATION_COVERAGE,
                MigrationArtifact.TARGET_CHANGE_INVENTORY,
            ),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.CONTRACTS,
                SourceAnalysisStage.SOURCE_CLOSURE,
                SourceAnalysisStage.STRUCTURED_C_ANALYSIS,
                TargetStudyStage.STUDY,
                KnowledgeStage.KNOWLEDGE_BASE,
            ),
        ),
        StageRow(
            MigrationStage.TARGET_COMPLIANCE,
            "Review target API, style, safety, lifecycle, and integration rules.",
            StageOwner.HYBRID,
            (MigrationArtifact.COMPLIANCE_REPORT,),
            prerequisites=(
                MigrationStage.HANDOFF,
                KnowledgeStage.KNOWLEDGE_BASE,
                TargetStudyStage.STUDY,
                SourceAnalysisStage.SOURCE_CLOSURE,
            ),
        ),
        StageRow(
            MigrationStage.ARTIFACT_PREPARATION,
            "Build or inject the runtime artifact and prove its identity.",
            StageOwner.HYBRID,
            (MigrationArtifact.RUNTIME_ARTIFACT, MigrationArtifact.ARTIFACT_IDENTITY),
            (MigrationArtifact.ARTIFACT_PREPARATION_ATTEMPT,),
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
            (MigrationArtifact.PUBLIC_QEMU_REPORT,),
            (MigrationArtifact.PUBLIC_QEMU_ATTEMPT,),
            prerequisites=(
                MigrationStage.HANDOFF,
                MigrationStage.CONTRACTS,
                MigrationStage.TEST_ADAPTATION,
                MigrationStage.DRIVER_IMPLEMENTATION,
                MigrationStage.TARGET_COMPLIANCE,
                EnvironmentStage.RECOVERY,
                KnowledgeStage.KNOWLEDGE_BASE,
                SourceAnalysisStage.SOURCE_CLOSURE,
            ),
        ),
    )
