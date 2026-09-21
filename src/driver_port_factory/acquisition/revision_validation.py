from __future__ import annotations

import json
import re

from ..codex.contracts import CodexArtifact
from ..core.contracts import ArtifactKey
from ..core.models import ArtifactRef, WorkflowError
from ..core.validation import BundleValidationContext
from ..intake.contracts import IntakeArtifact
from .contracts import AcquisitionArtifact
from .job import ArtifactOccurrence
from .repository_role import RepositoryRole
from .revision_manifest import RepositoryPlan, RevisionManifest
from .revision_proposal import RevisionProposalEnvelope

_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


def validate_revision_bundle(context: BundleValidationContext) -> None:
    envelope_ref, _ = context.one_dependency(IntakeArtifact.MIGRATION_ENVELOPE)
    plan_ref, plan_data = context.one_current(AcquisitionArtifact.REPOSITORY_PLAN)
    _, revision_data = context.one_current(AcquisitionArtifact.REVISION_MANIFEST)
    plan = RepositoryPlan.from_dict(json.loads(plan_data))
    revision = RevisionManifest.from_dict(json.loads(revision_data))
    _, proposal_data = _exact_occurrence(
        context.current_stage_artifacts,
        AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,
        plan.revision_proposal,
    )
    proposal = RevisionProposalEnvelope.from_dict(json.loads(proposal_data))
    if plan.migration_envelope_digest != envelope_ref.digest:
        raise WorkflowError("repository plan does not bind its real migration envelope dependency")
    _validate_plan_proposal(plan, proposal, context.current_stage_artifacts)
    _validate_manifest(revision, plan, envelope_ref.digest)
    if plan_ref.digest == plan.revision_proposal.digest:
        raise WorkflowError("repository plan cannot alias its auxiliary revision proposal")


def _validate_plan_proposal(
    plan: RepositoryPlan,
    envelope: RevisionProposalEnvelope,
    auxiliary: tuple[tuple[ArtifactRef, bytes], ...],
) -> None:
    if plan.selection_job != envelope.job_result:
        raise WorkflowError("repository plan changed the revision proposal job binding")
    _exact_occurrence(
        auxiliary,
        CodexArtifact.JOB_RESULT,
        ArtifactOccurrence(envelope.job_result.digest, envelope.job_result.ordinal),
    )
    if plan.migration_envelope_digest != envelope.proposal.migration_envelope_sha256:
        raise WorkflowError("repository plan changed the revision proposal envelope binding")
    proposed = {item.role: item for item in envelope.proposal.repositories}
    if set(proposed) != set(RepositoryRole):
        raise WorkflowError("revision proposal repository roles are incomplete")
    for repository in plan.repositories:
        candidate = proposed[repository.role]
        if (
            repository.platform,
            repository.url,
            repository.requested_ref,
            repository.selection_rule,
        ) != (
            candidate.platform,
            candidate.url,
            candidate.requested_ref,
            candidate.selection_rule,
        ):
            raise WorkflowError("repository plan differs from its controlled revision proposal")








def _validate_manifest(
    revision: RevisionManifest,
    plan: RepositoryPlan,
    envelope_digest: str,
) -> None:
    if revision.migration_envelope_sha256 != envelope_digest:
        raise WorkflowError("revision manifest does not bind its real migration envelope")
    if revision.revision_proposal != plan.revision_proposal:
        raise WorkflowError("revision manifest changed the proposal occurrence")
    if revision.selection_job != plan.selection_job:
        raise WorkflowError("revision manifest changed the selection job occurrence")
    if revision.repositories != plan.repositories:
        raise WorkflowError("revision manifest differs from the repository plan")


def _exact_occurrence(
    artifacts: tuple[tuple[ArtifactRef, bytes], ...],
    kind: ArtifactKey,
    occurrence: ArtifactOccurrence,
) -> tuple[ArtifactRef, bytes]:
    matches = [
        item
        for item in artifacts
        if item[0].kind == kind.value
        and item[0].digest == occurrence.digest
        and item[0].ordinal == occurrence.ordinal
    ]
    if len(matches) != 1:
        raise WorkflowError(f"revision bundle cannot resolve exact {kind.value} occurrence")
    return matches[0]
