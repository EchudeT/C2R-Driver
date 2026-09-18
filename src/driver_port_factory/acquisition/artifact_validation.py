from __future__ import annotations

from collections import Counter
from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, nonempty
from .closure import EvidenceClosurePlan
from .closure_artifacts import (
    EvidenceCoverageInventory,
    EvidenceGapRegister,
    EvidenceMaterialsManifest,
    EvidenceRetrievalLedger,
)
from .contracts import AcquisitionArtifact, RepositoryAcquisitionAttemptOutcome
from .proposal import ProposalEnvelope
from .repository_manifest import RepositoryAcquisition
from .repository_role import RepositoryRole
from .revision_manifest import RepositoryPlan, RevisionManifest
from .revision_proposal import RevisionProposalEnvelope
from .source_identity import SourceIdentityRecord


def _revision_manifest(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REVISION_MANIFEST.value)
    RevisionManifest.from_dict(value)


def _repository_plan(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REPOSITORY_PLAN.value)
    plan = RepositoryPlan.from_dict(value)
    roles = Counter(repository.role for repository in plan.repositories)
    if roles != Counter({role: 1 for role in RepositoryRole}):
        raise WorkflowError("repository_plan requires one source, target, and QEMU repository")


def _revision_selection_proposal(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REVISION_SELECTION_PROPOSAL.value)
    RevisionProposalEnvelope.from_dict(value)


def _repository_manifest(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REPOSITORY_MANIFEST.value)
    acquisition = RepositoryAcquisition.from_dict(value)
    roles = Counter(checkout.role for checkout in acquisition.checkouts)
    if roles != Counter({role: 1 for role in RepositoryRole}):
        raise WorkflowError("repository_manifest requires exactly three checkout records")
    for checkout in acquisition.checkouts:
        if not checkout.clean:
            raise WorkflowError(f"{checkout.role.value} repository baseline must be clean")


def _repository_attempt(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT.value)
    try:
        outcome = RepositoryAcquisitionAttemptOutcome(value.get("status"))
    except (TypeError, ValueError) as error:
        raise WorkflowError("repository acquisition attempt has an invalid status") from error
    if outcome is not RepositoryAcquisitionAttemptOutcome.FAIL:
        raise WorkflowError("repository acquisition attempt must preserve a failure")
    if not value.get("failure_type") or not value.get("message"):
        raise WorkflowError("repository acquisition attempt requires failure details")


def _source_identity(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION.value)
    SourceIdentityRecord.from_dict(value)


def _discovery_proposal(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL.value)
    ProposalEnvelope.from_dict(value)


def _closure_plan(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN.value)
    EvidenceClosurePlan.from_dict(value)


def _materials_manifest(data: bytes) -> None:
    EvidenceMaterialsManifest.from_bytes(data)


def _coverage_inventory(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY.value)
    EvidenceCoverageInventory.from_dict(value)


def _gap_register(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.EVIDENCE_GAP_REGISTER.value)
    EvidenceGapRegister.from_dict(value)


def _retrieval_ledger(data: bytes) -> None:
    value = json_object(data, AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER.value)
    EvidenceRetrievalLedger.from_dict(value)


VALIDATORS = MappingProxyType[AcquisitionArtifact, ArtifactValidator](
    {
        AcquisitionArtifact.REVISION_SELECTION_PROPOSAL: _revision_selection_proposal,
        AcquisitionArtifact.REVISION_MANIFEST: _revision_manifest,
        AcquisitionArtifact.REPOSITORY_PLAN: _repository_plan,
        AcquisitionArtifact.REPOSITORY_MANIFEST: _repository_manifest,
        AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT: _repository_attempt,
        AcquisitionArtifact.SOURCE_IDENTITY_VERIFICATION: _source_identity,
        AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL: _discovery_proposal,
        AcquisitionArtifact.EVIDENCE_CLOSURE_PLAN: _closure_plan,
        AcquisitionArtifact.MATERIALS_MANIFEST: _materials_manifest,
        AcquisitionArtifact.EVIDENCE_COVERAGE_INVENTORY: _coverage_inventory,
        AcquisitionArtifact.EVIDENCE_GAP_REGISTER: _gap_register,
        AcquisitionArtifact.EVIDENCE_RETRIEVAL_LEDGER: _retrieval_ledger,
        AcquisitionArtifact.EVIDENCE_HTTP_CONTENT: nonempty,
    }
)
