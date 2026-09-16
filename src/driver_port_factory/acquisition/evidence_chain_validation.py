from __future__ import annotations

import json
from typing import Protocol

from ..codex.contracts import CodexArtifact
from ..core.contracts import ArtifactKey
from ..core.models import ArtifactRef, WorkflowError
from .closure import EvidenceClosurePlan
from .contracts import AcquisitionArtifact
from .proposal import ProposalEnvelope
from .repository_manifest import RepositoryAcquisition
from .revision_manifest import RepositoryPlan, RevisionManifest


def validate_frozen_evidence_chain(
    *,
    envelope_digest: str,
    repository_plan_digest: str,
    repository_manifest_digest: str,
    repository_plan: RepositoryPlan,
    revision: RevisionManifest,
    acquisition: RepositoryAcquisition,
    closure: EvidenceClosurePlan,
) -> None:
    if not all(
        digest == envelope_digest
        for digest in (
            repository_plan.migration_envelope_digest,
            revision.migration_envelope_sha256,
            acquisition.migration_envelope_sha256,
            closure.migration_envelope_sha256,
            closure.proposal.migration_envelope_sha256,
        )
    ):
        raise WorkflowError("evidence closure frozen envelope chain is inconsistent")
    if revision.repositories != repository_plan.repositories:
        raise WorkflowError("evidence closure revision and repository plan differ")
    if acquisition.repository_plan_sha256 != repository_plan_digest:
        raise WorkflowError("evidence closure repository manifest changed its plan binding")
    if (
        closure.repository_manifest_sha256 != repository_manifest_digest
        or closure.proposal.repository_manifest_sha256 != repository_manifest_digest
    ):
        raise WorkflowError("evidence closure changed its repository occurrence binding")


def validate_proposal_binding(
    plan: EvidenceClosurePlan,
    auxiliary: tuple[tuple[ArtifactRef, bytes], ...],
) -> None:
    proposal_ref, proposal_data = exact_occurrence(
        auxiliary,
        AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL,
        plan.proposal_occurrence,
    )
    proposal = ProposalEnvelope.from_dict(json.loads(proposal_data))
    if plan.job_result != proposal.job_result:
        raise WorkflowError("evidence closure changed the proposal's Codex job binding")
    if plan.proposal != proposal.proposal:
        raise WorkflowError("evidence closure plan differs from its controlled proposal")
    exact_occurrence(auxiliary, CodexArtifact.JOB_RESULT, plan.job_result)
    if proposal_ref.digest != plan.proposal_occurrence.digest:
        raise WorkflowError("evidence proposal occurrence digest mismatch")


class OccurrenceIdentity(Protocol):
    digest: str
    ordinal: int


def exact_occurrence(
    artifacts: tuple[tuple[ArtifactRef, bytes], ...],
    kind: ArtifactKey,
    occurrence: OccurrenceIdentity,
) -> tuple[ArtifactRef, bytes]:
    matches = [
        item
        for item in artifacts
        if item[0].kind == kind.value
        and item[0].digest == occurrence.digest
        and item[0].ordinal == occurrence.ordinal
    ]
    if len(matches) != 1:
        raise WorkflowError(f"bundle cannot resolve exact {kind.value} occurrence")
    return matches[0]
