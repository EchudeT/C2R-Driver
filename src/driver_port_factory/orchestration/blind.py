from __future__ import annotations

from ..control.contracts import ControlArtifact, ControlStage
from ..core.models import ActorRole, EvaluationMode, ProjectConfig, StageOwner, StageSpec
from ..evaluation.contracts import EvaluationArtifact, EvaluationStage
from ..sealing.contracts import SealingArtifact
from .specification import StageRow, append_linear, stage_spec


def curator_workflow(config: ProjectConfig) -> tuple[StageSpec, ...]:
    roles = (ActorRole.CURATOR,)
    specs = [
        stage_spec(
            ControlStage.PROJECT_INIT,
            "Freeze curator project configuration.",
            StageOwner.STATIC,
            None,
            (ControlArtifact.PROJECT_MANIFEST,),
            roles,
        )
    ]
    previous = ControlStage.PROJECT_INIT
    if config.evaluation_mode is EvaluationMode.POST_HOC_SEALED_BLIND:
        specs.append(
            stage_spec(
                EvaluationStage.OPAQUE_CANDIDATE_ACCEPTANCE,
                "Accept and anchor an opaque candidate digest before candidate inspection.",
                StageOwner.STATIC,
                previous,
                (SealingArtifact.CANDIDATE_DIGEST_ANCHOR,),
                roles,
            )
        )
        previous = EvaluationStage.OPAQUE_CANDIDATE_ACCEPTANCE
    elif config.evaluation_mode is not EvaluationMode.PROSPECTIVE_BLIND:
        raise ValueError("curator role requires a blind evaluation mode")
    append_linear(
        specs,
        (
            StageRow(
                EvaluationStage.DRIVER_SPLIT,
                "Freeze driver family and task splits.",
                StageOwner.HYBRID,
                (EvaluationArtifact.SPLIT_MANIFEST,),
            ),
            StageRow(
                EvaluationStage.CONTRACT_FREEZE,
                "Freeze evidence-backed PMC and private PEA.",
                StageOwner.CODEX,
                (EvaluationArtifact.PUBLIC_CONTRACT, EvaluationArtifact.PRIVATE_ASSERTIONS),
            ),
            StageRow(
                EvaluationStage.REFERENCE_CALIBRATION,
                "Validate assertions against the C reference and mutations.",
                StageOwner.HYBRID,
                (EvaluationArtifact.REFERENCE_CALIBRATION,),
            ),
            StageRow(
                EvaluationStage.PRIVATE_BUNDLE_SEALING,
                "Freeze thresholds, generators, harness, and commitment.",
                StageOwner.STATIC,
                (EvaluationArtifact.PRIVATE_BUNDLE_COMMITMENT,),
            ),
            StageRow(
                EvaluationStage.PUBLIC_BUNDLE_EXPORT,
                "Export only the public task bundle.",
                StageOwner.STATIC,
                (EvaluationArtifact.PUBLIC_BUNDLE,),
            ),
        ),
        previous,
        roles,
    )
    return tuple(specs)


def evaluator_workflow(config: ProjectConfig) -> tuple[StageSpec, ...]:
    del config
    roles = (ActorRole.EVALUATOR,)
    specs = [
        stage_spec(
            ControlStage.PROJECT_INIT,
            "Freeze evaluator project configuration.",
            StageOwner.STATIC,
            None,
            (ControlArtifact.PROJECT_MANIFEST,),
            roles,
        )
    ]
    append_linear(
        specs,
        (
            StageRow(
                EvaluationStage.EVALUATION_INPUTS,
                "Accept candidate, commitment, chronology, and environment identity.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.EVALUATION_INPUT_MANIFEST,),
            ),
            StageRow(
                EvaluationStage.ISOLATION_GATE,
                "Verify role, material, credential, and feedback boundaries.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.ISOLATION_REPORT,),
            ),
            StageRow(
                EvaluationStage.REPRODUCIBLE_BUILD,
                "Build the exact sealed candidate from frozen inputs.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.REPRODUCIBLE_BUILD_REPORT,),
            ),
            StageRow(
                EvaluationStage.MANDATORY_CONTRACTS,
                "Run frozen mandatory contract gates.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.CONTRACT_REPORT,),
            ),
            StageRow(
                EvaluationStage.EXTERNAL_FUNCTIONALITY,
                "Run external black-box functionality tests.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.FUNCTIONALITY_REPORT,),
            ),
            StageRow(
                EvaluationStage.DIFFERENTIAL_EXECUTION,
                "Compare C reference and Rust candidate behavior.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.DIFFERENTIAL_REPORT,),
            ),
            StageRow(
                EvaluationStage.FAULT_INJECTION,
                "Run frozen fault schedules.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.FAULT_REPORT,),
            ),
            StageRow(
                EvaluationStage.MUTATION_ADEQUACY,
                "Measure private assertion mutation sensitivity.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.MUTATION_REPORT,),
            ),
            StageRow(
                EvaluationStage.STRESS,
                "Run frozen stress and concurrency tests.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.STRESS_REPORT,),
            ),
            StageRow(
                EvaluationStage.PERFORMANCE,
                "Run frozen performance thresholds.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.PERFORMANCE_REPORT,),
            ),
            StageRow(
                EvaluationStage.HARDWARE_SUBSET,
                "Run the preselected real-hardware subset.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.HARDWARE_REPORT,),
            ),
            StageRow(
                EvaluationStage.EVALUATION_REPORT,
                "Seal complete results without candidate repair.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.EVALUATION_REPORT,),
            ),
        ),
        ControlStage.PROJECT_INIT,
        roles,
    )
    return tuple(specs)


def auditor_workflow(config: ProjectConfig) -> tuple[StageSpec, ...]:
    del config
    roles = (ActorRole.AUDITOR,)
    specs = [
        stage_spec(
            ControlStage.PROJECT_INIT,
            "Freeze read-only auditor project configuration.",
            StageOwner.STATIC,
            None,
            (ControlArtifact.PROJECT_MANIFEST,),
            roles,
        )
    ]
    append_linear(
        specs,
        (
            StageRow(
                EvaluationStage.AUDIT_INPUTS,
                "Import manifests, commitments, ledgers, and claims read-only.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.AUDIT_INPUT_MANIFEST,),
            ),
            StageRow(
                EvaluationStage.INDEPENDENCE_AUDIT,
                "Verify actor, context, workspace, material, and feedback separation.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.INDEPENDENCE_REPORT,),
            ),
            StageRow(
                EvaluationStage.COMMITMENT_AUDIT,
                "Verify chronology, commitments, bundle digests, and candidate identity.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.COMMITMENT_REPORT,),
            ),
            StageRow(
                EvaluationStage.CLAIM_AUDIT,
                "Check reported claims against frozen modes and observed evidence.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.CLAIM_REPORT,),
            ),
            StageRow(
                EvaluationStage.AUDIT_REPORT,
                "Seal verified and missing independence evidence without changing bundles.",
                StageOwner.INDEPENDENT,
                (EvaluationArtifact.AUDIT_REPORT,),
            ),
        ),
        ControlStage.PROJECT_INIT,
        roles,
    )
    return tuple(specs)
