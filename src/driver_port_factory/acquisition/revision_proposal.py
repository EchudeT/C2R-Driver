from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..codex.contracts import CodexArtifact
from ..core.models import GeneratedArtifact, StageStatus, WorkflowError
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .contracts import AcquisitionArtifact, AcquisitionStage
from .job import (
    ArtifactOccurrence,
    JobResultBinding,
    exact_occurrence,
    nonempty,
    ordinal,
)
from .parsing import exact_object, schema_version
from .repository_role import RepositoryRole
from .repository_spec import RepositorySpec
from .revision_compatibility import CompatibilityClaimKind, CompatibilityEvidence

_FULL_COMMIT = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")
_RELEASE_TAG = re.compile(r"(?:refs/tags/)?[vV]?\d+(?:\.\d+)+(?:[-._][A-Za-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class RepositoryCandidate:
    role: RepositoryRole
    platform: str
    url: str
    requested_ref: str
    selection_rule: str

    @classmethod
    def from_dict(cls, value: object) -> RepositoryCandidate:
        candidate = exact_object(
            value,
            required={"role", "platform", "url", "requested_ref", "selection_rule"},
            label="revision repository candidate",
        )
        try:
            role = RepositoryRole(candidate["role"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("revision repository candidate has invalid role") from error
        reference = nonempty(candidate["requested_ref"], "revision candidate ref")
        if not (_FULL_COMMIT.fullmatch(reference) or _RELEASE_TAG.fullmatch(reference)):
            raise WorkflowError(
                "revision candidate must use a release tag or a full commit, not a floating ref"
            )
        return cls(
            role,
            nonempty(candidate["platform"], "revision candidate platform"),
            _repository_url(candidate["url"]),
            reference,
            nonempty(candidate["selection_rule"], "revision selection rule"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "role": self.role.value,
            "platform": self.platform,
            "url": self.url,
            "requested_ref": self.requested_ref,
            "selection_rule": self.selection_rule,
        }


@dataclass(frozen=True, slots=True)
class ProposedRevisionBinding:
    role: RepositoryRole
    requested_ref: str

    @classmethod
    def from_dict(cls, value: object) -> ProposedRevisionBinding:
        if not isinstance(value, dict) or set(value) != {"role", "requested_ref"}:
            raise WorkflowError("compatibility binding has an invalid structure")
        return cls(
            RepositoryRole(value.get("role")),
            nonempty(value.get("requested_ref"), "compatibility binding ref"),
        )

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role.value, "requested_ref": self.requested_ref}


@dataclass(frozen=True, slots=True)
class CompatibilityCitation:
    source_url: str
    claim: str
    excerpt: str
    claim_kind: CompatibilityClaimKind
    bindings: tuple[ProposedRevisionBinding, ...]
    max_bytes: int

    @classmethod
    def from_dict(cls, value: object) -> CompatibilityCitation:
        candidate = exact_object(
            value,
            required={"source_url", "claim", "excerpt", "claim_kind", "bindings"},
            optional={"max_bytes"},
            label="compatibility citation",
        )
        bindings = candidate["bindings"]
        if not isinstance(bindings, list) or not bindings:
            raise WorkflowError("compatibility citation bindings must be non-empty")
        try:
            parsed_bindings = tuple(ProposedRevisionBinding.from_dict(item) for item in bindings)
        except (TypeError, ValueError) as error:
            raise WorkflowError("compatibility citation has an invalid binding") from error
        if len({binding.role for binding in parsed_bindings}) != len(parsed_bindings):
            raise WorkflowError("compatibility citation binding roles must be unique")
        max_bytes = candidate.get("max_bytes", 4 * 1024 * 1024)
        if not isinstance(max_bytes, int) or not 0 < max_bytes <= 16 * 1024 * 1024:
            raise WorkflowError("compatibility citation max_bytes is outside the controlled range")
        source_url = nonempty(candidate["source_url"], "compatibility citation URL")
        parsed_url = urlparse(source_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise WorkflowError("compatibility citation must use HTTP or HTTPS")
        try:
            claim_kind = CompatibilityClaimKind(candidate["claim_kind"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("compatibility citation has an invalid claim kind") from error
        return cls(
            source_url,
            nonempty(candidate["claim"], "compatibility claim"),
            nonempty(candidate["excerpt"], "compatibility evidence excerpt"),
            claim_kind,
            parsed_bindings,
            max_bytes,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "source_url": self.source_url,
            "claim": self.claim,
            "excerpt": self.excerpt,
            "claim_kind": self.claim_kind.value,
            "bindings": [binding.to_dict() for binding in self.bindings],
            "max_bytes": self.max_bytes,
        }


@dataclass(frozen=True, slots=True)
class RevisionSelectionProposal:
    migration_envelope_sha256: str
    repositories: tuple[RepositoryCandidate, ...]
    compatibility_evidence: tuple[CompatibilityCitation, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> RevisionSelectionProposal:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "migration_envelope_sha256",
                "repositories",
                "compatibility_evidence",
            },
            label="revision selection proposal",
        )
        schema_version(candidate, "revision selection proposal")
        raw_repositories = candidate["repositories"]
        raw_evidence = candidate["compatibility_evidence"]
        if not isinstance(raw_repositories, list):
            raise WorkflowError("revision selection proposal requires repositories")
        if not isinstance(raw_evidence, list):
            raise WorkflowError("revision selection proposal requires compatibility evidence")
        repositories = tuple(RepositoryCandidate.from_dict(item) for item in raw_repositories)
        if Counter(item.role for item in repositories) != Counter(
            {role: 1 for role in RepositoryRole}
        ):
            raise WorkflowError("revision proposal requires one source, target, and QEMU candidate")
        evidence = tuple(CompatibilityCitation.from_dict(item) for item in raw_evidence)
        validate_proposed_compatibility_evidence(evidence, repositories)
        return cls(
            _sha256(candidate["migration_envelope_sha256"]),
            tuple(sorted(repositories, key=lambda item: item.role.sequence)),
            evidence,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "repositories": [repository.to_dict() for repository in self.repositories],
            "compatibility_evidence": [item.to_dict() for item in self.compatibility_evidence],
        }


@dataclass(frozen=True, slots=True)
class RevisionProposalEnvelope:
    job_result: JobResultBinding
    proposal: RevisionSelectionProposal
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> RevisionProposalEnvelope:
        candidate = exact_object(
            value,
            required={"schema_version", "job_result", "proposal"},
            label="controlled revision proposal wrapper",
        )
        schema_version(candidate, "controlled revision proposal wrapper")
        return cls(
            JobResultBinding.from_dict(candidate["job_result"]),
            RevisionSelectionProposal.from_dict(candidate["proposal"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job_result": self.job_result.to_dict(),
            "proposal": self.proposal.to_dict(),
        }


class RevisionProposalImporter:
    def import_job_result(
        self,
        project: Project,
        *,
        job_digest: str,
        job_ordinal: int,
    ) -> ArtifactOccurrence:
        if project.stage(AcquisitionStage.REVISION_SELECTION).status is not StageStatus.RUNNING:
            raise WorkflowError("revision_selection must be RUNNING before proposal import")
        job = exact_occurrence(
            project,
            stage=AcquisitionStage.REVISION_SELECTION,
            kind=CodexArtifact.JOB_RESULT,
            occurrence=ArtifactOccurrence(job_digest, job_ordinal),
        )
        try:
            proposal = RevisionSelectionProposal.from_dict(json.loads(project.artifacts.read(job)))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex revision proposal is not UTF-8 JSON") from error
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        if proposal.migration_envelope_sha256 != envelope_ref.digest:
            raise WorkflowError("revision proposal migration envelope digest is stale")
        binding = JobResultBinding(job.digest, ordinal(job.ordinal), job.source)
        data = (
            json.dumps(
                RevisionProposalEnvelope(binding, proposal).to_dict(),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            ).encode("utf-8")
            + b"\n"
        )
        imported = project.record_artifact(
            AcquisitionStage.REVISION_SELECTION,
            GeneratedArtifact(
                AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,
                data,
                f"codex-job-result:{binding.ordinal}:{binding.digest}",
            ),
        )
        return ArtifactOccurrence(imported.digest, ordinal(imported.ordinal))


def load_revision_proposal(
    project: Project, occurrence: ArtifactOccurrence
) -> RevisionProposalEnvelope:
    ref = exact_occurrence(
        project,
        stage=AcquisitionStage.REVISION_SELECTION,
        kind=AcquisitionArtifact.REVISION_SELECTION_PROPOSAL,
        occurrence=occurrence,
    )
    try:
        return RevisionProposalEnvelope.from_dict(json.loads(project.artifacts.read(ref)))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("controlled revision proposal is not UTF-8 JSON") from error


def validate_proposed_compatibility_evidence(
    evidence: tuple[CompatibilityCitation, ...],
    repositories: tuple[RepositoryCandidate, ...],
) -> None:
    expected = {(item.role, item.requested_ref) for item in repositories}
    maintenance = tuple(
        item for item in evidence if item.claim_kind is CompatibilityClaimKind.MAINTENANCE
    )
    covered = {
        (binding.role, binding.requested_ref) for item in maintenance for binding in item.bindings
    }
    if covered != expected or any(len(item.bindings) != 1 for item in maintenance):
        raise WorkflowError("revision evidence requires maintenance for every proposed ref")
    if not any(
        {(binding.role, binding.requested_ref) for binding in item.bindings} == expected
        for item in evidence
        if item.claim_kind is CompatibilityClaimKind.CROSS_REPOSITORY
    ):
        raise WorkflowError("revision evidence must bind one cross-platform compatibility claim")


def validate_resolved_compatibility_evidence(
    evidence: tuple[CompatibilityEvidence, ...],
    repositories: tuple[RepositorySpec, ...],
) -> None:
    expected = {(item.role, item.requested_ref, item.resolved_commit) for item in repositories}
    maintenance = tuple(
        item for item in evidence if item.claim_kind is CompatibilityClaimKind.MAINTENANCE
    )
    covered = {
        (binding.role, binding.requested_ref, binding.resolved_commit)
        for item in maintenance
        for binding in item.bindings
    }
    if covered != expected or any(len(item.bindings) != 1 for item in maintenance):
        raise WorkflowError("resolved evidence lacks maintenance for every repository commit")
    if not any(
        {
            (binding.role, binding.requested_ref, binding.resolved_commit)
            for binding in item.bindings
        }
        == expected
        for item in evidence
        if item.claim_kind is CompatibilityClaimKind.CROSS_REPOSITORY
    ):
        raise WorkflowError("resolved evidence lacks a cross-platform compatibility binding")


def _repository_url(value: object) -> str:
    url = nonempty(value, "revision candidate URL")
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https", "ssh", "git"} and parsed.netloc:
        return url
    path = Path(url)
    if path.is_absolute() and path.exists():
        return str(path.resolve())
    raise WorkflowError("revision candidate URL must be a remote URL or existing absolute path")


def _sha256(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise WorkflowError("revision proposal envelope digest must be lowercase SHA256")
    return value
