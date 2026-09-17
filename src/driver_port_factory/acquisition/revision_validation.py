from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ..codex.contracts import CodexArtifact
from ..core.contracts import ArtifactKey
from ..core.models import ArtifactRef, WorkflowError
from ..core.validation import BundleValidationContext
from ..intake.contracts import IntakeArtifact
from .commands import RepositoryCommandKind, RepositoryCommandRecord
from .contracts import AcquisitionArtifact
from .job import ArtifactOccurrence
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
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
    _validate_resolution_commands(context.project_root, plan)
    _validate_evidence_content(plan, context.artifacts)
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
    citations = envelope.proposal.compatibility_evidence
    if len(plan.compatibility_evidence) != len(citations):
        raise WorkflowError("repository plan changed compatibility evidence cardinality")
    for evidence, citation in zip(plan.compatibility_evidence, citations, strict=True):
        if (
            evidence.source_url,
            evidence.claim,
            evidence.excerpt,
            evidence.claim_kind,
            tuple((item.role, item.requested_ref) for item in evidence.bindings),
        ) != (
            citation.source_url,
            citation.claim,
            citation.excerpt,
            citation.claim_kind,
            tuple((item.role, item.requested_ref) for item in citation.bindings),
        ):
            raise WorkflowError("repository plan changed compatibility evidence citation")


def _validate_resolution_commands(root: Path, plan: RepositoryPlan) -> None:
    by_role = {role: [] for role in RepositoryRole}
    for command in plan.resolution_commands:
        command.verify_evidence(root)
        if Path(command.result.cwd).resolve() != root.resolve():
            raise WorkflowError("revision resolution command used an unexpected working directory")
        by_role[command.role].append(command)
    for repository in plan.repositories:
        records = by_role[repository.role]
        resolutions = [
            item for item in records if item.operation is RepositoryCommandKind.REVISION_RESOLUTION
        ]
        if not resolutions or not any(
            repository.resolved_commit in _stdout(command) for command in resolutions
        ):
            raise WorkflowError(
                f"{repository.role.value} revision lacks successful resolution evidence"
            )
        _validate_resolution_shape(repository, records)


def _validate_resolution_shape(
    repository: RepositorySpec,
    records: list[RepositoryCommandRecord],
) -> None:
    local = Path(repository.url)
    resolutions = [
        record
        for record in records
        if record.operation is RepositoryCommandKind.REVISION_RESOLUTION
    ]
    if local.is_absolute() and local.exists():
        expected = (
            "git",
            "-C",
            str(local.resolve()),
            "rev-parse",
            f"{repository.requested_ref}^{{commit}}",
        )
        if not any(record.result.argv == expected for record in resolutions):
            raise WorkflowError("local revision command does not bind its repository and ref")
        return
    if _FULL_COMMIT.fullmatch(repository.requested_ref):
        memberships = [
            record
            for record in records
            if record.operation is RepositoryCommandKind.COMMIT_MEMBERSHIP
        ]
        origins = [
            record
            for record in records
            if record.operation is RepositoryCommandKind.ORIGIN_CONFIGURATION
        ]
        if not memberships or not any(
            record.result.argv[-1] == repository.requested_ref for record in memberships
        ):
            raise WorkflowError("remote full commit lacks active membership proof")
        if not origins or not any(record.result.argv[-1] == repository.url for record in origins):
            raise WorkflowError("remote commit proof does not bind its claimed origin")
        return
    if not any(
        repository.url in record.result.argv
        and repository.requested_ref in record.result.argv
        and "ls-remote" in record.result.argv
        for record in resolutions
    ):
        raise WorkflowError("remote revision command does not bind its repository and ref")


def _stdout(command: RepositoryCommandRecord) -> str:
    return Path(command.result.stdout_path).read_text(encoding="utf-8", errors="replace")


def _validate_evidence_content(
    plan: RepositoryPlan,
    artifacts: tuple[tuple[ArtifactRef, bytes], ...],
) -> None:
    contents = [
        item
        for item in artifacts
        if item[0].kind == AcquisitionArtifact.REVISION_EVIDENCE_CONTENT.value
    ]
    expected = Counter((item.sha256, item.resolved_url) for item in plan.compatibility_evidence)
    actual = Counter((ref.digest, ref.source) for ref, _ in contents)
    if actual != expected:
        raise WorkflowError("revision evidence content differs from the computed citations")
    for evidence in plan.compatibility_evidence:
        matches = [data for ref, data in contents if ref.digest == evidence.sha256]
        if not matches or any(len(data) != evidence.size_bytes for data in matches):
            raise WorkflowError("revision evidence size differs from its computed record")


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
    if revision.compatibility_evidence != plan.compatibility_evidence:
        raise WorkflowError("revision manifest changed compatibility evidence")
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
