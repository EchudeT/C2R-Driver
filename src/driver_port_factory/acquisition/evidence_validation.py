from __future__ import annotations

import json

from ..core.models import WorkflowError
from ..core.validation import BundleValidationContext
from ..intake.contracts import IntakeArtifact
from .closure import EvidenceClosurePlan
from .closure_artifacts import (
    EvidenceClosureBundle,
    EvidenceCoverageInventory,
    EvidenceGapRegister,
    EvidenceMaterialsManifest,
    EvidenceRetrievalLedger,
)
from .contracts import AcquisitionArtifact
from .evidence_accounting_validation import validate_evidence_accounting
from .evidence_chain_validation import (
    validate_frozen_evidence_chain,
    validate_proposal_binding,
)
from .frozen_checkout_validation import verify_git_checkout, verify_lock
from .http_content_validation import validate_http_content_occurrences
from .material_integrity_validation import validate_material_integrity
from .repository_manifest import RepositoryAcquisition
from .revision_manifest import RepositoryPlan, RevisionManifest


def validate_evidence_closure_bundle(context: BundleValidationContext) -> None:
    envelope_ref, _ = context.one_dependency(IntakeArtifact.MIGRATION_ENVELOPE)
    repository_plan_ref, repository_plan_data = context.one_dependency(
        AcquisitionArtifact.REPOSITORY_PLAN
    )
    _, revision_data = context.one_dependency(AcquisitionArtifact.REVISION_MANIFEST)
    repository_ref, repository_data = context.one_dependency(
        AcquisitionArtifact.REPOSITORY_MANIFEST
    )
    _, plan_data = context.one_current(AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN)
    _, materials_data = context.one_current(AcquisitionArtifact.MATERIALS_MANIFEST)
    _, coverage_data = context.one_current(AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY)
    _, gaps_data = context.one_current(AcquisitionArtifact.EVIDENCE_GAP_REGISTER)
    _, ledger_data = context.one_current(AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER)

    repository_plan = RepositoryPlan.from_dict(_json(repository_plan_data))
    revision = RevisionManifest.from_dict(_json(revision_data))
    acquisition = RepositoryAcquisition.from_dict(_json(repository_data))
    closure = EvidenceClosureBundle(
        EvidenceClosurePlan.from_dict(_json(plan_data)),
        EvidenceMaterialsManifest.from_bytes(materials_data),
        EvidenceCoverageInventory.from_dict(_json(coverage_data)),
        EvidenceGapRegister.from_dict(_json(gaps_data)),
        EvidenceRetrievalLedger.from_dict(_json(ledger_data)),
    )

    validate_frozen_evidence_chain(
        envelope_digest=envelope_ref.digest,
        repository_plan_digest=repository_plan_ref.digest,
        repository_manifest_digest=repository_ref.digest,
        repository_plan=repository_plan,
        revision=revision,
        acquisition=acquisition,
        closure=closure.plan,
    )
    for checkout in acquisition.checkouts:
        verify_lock(context.project_root, checkout)
        verify_git_checkout(context.project_root, checkout)
    validate_proposal_binding(closure.plan, context.current_stage_artifacts)
    validate_evidence_accounting(
        closure.plan,
        closure.materials.records,
        closure.coverage.facets,
        closure.gaps.gaps,
        closure.ledger.attempts,
    )
    validate_http_content_occurrences(
        context,
        closure.materials.records,
        closure.ledger.attempts,
    )
    validate_material_integrity(
        context.project_root,
        acquisition,
        closure.materials.records,
        closure.ledger.attempts,
    )


def _json(data: bytes) -> object:
    try:
        return json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("evidence closure artifact is not UTF-8 JSON") from error
