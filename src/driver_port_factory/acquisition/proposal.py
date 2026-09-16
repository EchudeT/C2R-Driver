from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass

from ..codex.contracts import CodexArtifact
from ..core.models import (
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .contracts import AcquisitionArtifact, AcquisitionStage
from .facet_policy import empty_locator_inventory_allowed, validate_locator_authority
from .facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    FacetDisposition,
    GapReason,
    parse_facet,
    required_facets,
)
from .job import (
    ArtifactOccurrence,
    JobResultBinding,
    exact_occurrence,
    nonempty,
    ordinal,
    sha256,
)
from .locators import (
    EvidenceLocator,
    ExternalReferenceLocator,
    parse_locator,
)
from .parsing import exact_object, schema_version


@dataclass(frozen=True, slots=True)
class ProposalImport:
    occurrence: ArtifactOccurrence
    job_result: ArtifactOccurrence


@dataclass(frozen=True, slots=True)
class GapDeclaration:
    reason: GapReason
    impact: str
    repair_trigger: str

    @classmethod
    def from_dict(cls, value: object) -> GapDeclaration:
        candidate = exact_object(
            value,
            required={"reason", "impact", "repair_trigger"},
            label="explicit evidence gap",
        )
        try:
            reason = GapReason(candidate["reason"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("explicit evidence gap has an invalid typed reason") from error
        return cls(
            reason,
            nonempty(candidate["impact"], "gap impact"),
            nonempty(candidate["repair_trigger"], "gap repair trigger"),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "reason": self.reason.value,
            "impact": self.impact,
            "repair_trigger": self.repair_trigger,
        }


@dataclass(frozen=True, slots=True)
class FacetProposal:
    facet: EvidenceFacet
    disposition: FacetDisposition
    rationale: str
    locators: tuple[EvidenceLocator, ...]
    gap: GapDeclaration | None

    @classmethod
    def from_dict(cls, value: object) -> FacetProposal:
        candidate = exact_object(
            value,
            required={"lane", "facet", "disposition", "rationale", "locators"},
            optional={"gap"},
            label="evidence facet proposal",
        )
        facet = parse_facet(candidate["lane"], candidate["facet"])
        try:
            disposition = FacetDisposition(candidate["disposition"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("evidence facet has an invalid disposition") from error
        raw_locators = candidate["locators"]
        if not isinstance(raw_locators, list):
            raise WorkflowError("evidence facet locators must be a list")
        if not raw_locators and not empty_locator_inventory_allowed(facet):
            raise WorkflowError("evidence facet proposal requires retrieval locators")
        locators = tuple(parse_locator(locator) for locator in raw_locators)
        for locator in locators:
            validate_locator_authority(facet, locator)
        if disposition is FacetDisposition.CONTROLLED and any(
            isinstance(locator, ExternalReferenceLocator) for locator in locators
        ):
            raise WorkflowError("controlled evidence requires content-bound locators")
        gap = GapDeclaration.from_dict(candidate["gap"]) if "gap" in candidate else None
        if disposition is FacetDisposition.EXPLICIT_GAP and gap is None:
            raise WorkflowError("explicit evidence gap requires gap accounting")
        if disposition is FacetDisposition.CONTROLLED and gap is not None:
            raise WorkflowError("controlled evidence facet must not declare a gap")
        return cls(
            facet,
            disposition,
            nonempty(candidate["rationale"], "evidence facet rationale"),
            locators,
            gap,
        )

    def to_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            **self.facet.to_dict(),
            "disposition": self.disposition.value,
            "rationale": self.rationale,
            "locators": [locator.to_dict() for locator in self.locators],
        }
        if self.gap is not None:
            value["gap"] = self.gap.to_dict()
        return value


@dataclass(frozen=True, slots=True)
class EvidenceDiscoveryProposal:
    migration_envelope_sha256: str
    repository_manifest_sha256: str
    facets: tuple[FacetProposal, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> EvidenceDiscoveryProposal:
        candidate = exact_object(
            value,
            required={
                "schema_version",
                "migration_envelope_sha256",
                "repository_manifest_sha256",
                "facets",
            },
            label="evidence proposal",
        )
        schema_version(candidate, "evidence proposal")
        envelope = sha256(candidate["migration_envelope_sha256"], "migration envelope")
        repository = sha256(candidate["repository_manifest_sha256"], "repository manifest")
        raw_facets = candidate["facets"]
        if not isinstance(raw_facets, list):
            raise WorkflowError("evidence proposal facets must be a list")
        facets = tuple(FacetProposal.from_dict(item) for item in raw_facets)
        counts = Counter(item.facet for item in facets)
        duplicates = sorted(facet.sort_key for facet, count in counts.items() if count != 1)
        expected = set(required_facets())
        actual = set(counts)
        missing = sorted(facet.sort_key for facet in expected - actual)
        unexpected = sorted(facet.sort_key for facet in actual - expected)
        if duplicates or missing or unexpected:
            raise WorkflowError(
                "evidence proposal must contain every required facet exactly once: "
                f"duplicates={duplicates}, missing={missing}, unexpected={unexpected}"
            )
        source = next(item for item in facets if item.facet == SOURCE_DRIVER_ENTRY)
        if source.disposition is not FacetDisposition.CONTROLLED:
            raise WorkflowError("source driver entry cannot be an explicit evidence gap")
        return cls(
            envelope,
            repository,
            tuple(sorted(facets, key=lambda item: item.facet.sort_key)),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "migration_envelope_sha256": self.migration_envelope_sha256,
            "repository_manifest_sha256": self.repository_manifest_sha256,
            "facets": [item.to_dict() for item in self.facets],
        }


@dataclass(frozen=True, slots=True)
class ProposalEnvelope:
    job_result: JobResultBinding
    proposal: EvidenceDiscoveryProposal
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> ProposalEnvelope:
        candidate = exact_object(
            value,
            required={"schema_version", "job_result", "proposal"},
            label="controlled evidence proposal wrapper",
        )
        schema_version(candidate, "controlled evidence proposal wrapper")
        return cls(
            JobResultBinding.from_dict(candidate["job_result"]),
            EvidenceDiscoveryProposal.from_dict(candidate["proposal"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "job_result": self.job_result.to_dict(),
            "proposal": self.proposal.to_dict(),
        }


class EvidenceProposalImporter:
    def import_job_result(
        self,
        project: Project,
        *,
        job_digest: str,
        job_ordinal: int,
    ) -> ProposalImport:
        if project.stage(AcquisitionStage.EVIDENCE_CLOSURE).status is not StageStatus.RUNNING:
            raise WorkflowError("evidence_closure must be RUNNING before importing a proposal")
        job = exact_occurrence(
            project,
            stage=AcquisitionStage.EVIDENCE_CLOSURE,
            kind=CodexArtifact.JOB_RESULT,
            occurrence=ArtifactOccurrence(job_digest, job_ordinal),
        )
        try:
            proposal = EvidenceDiscoveryProposal.from_dict(json.loads(project.artifacts.read(job)))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError("Codex evidence proposal is not UTF-8 JSON") from error
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        repository_ref = project.artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            AcquisitionArtifact.REPOSITORY_MANIFEST,
        )
        if proposal.migration_envelope_sha256 != envelope_ref.digest:
            raise WorkflowError("evidence proposal migration envelope digest is stale")
        if proposal.repository_manifest_sha256 != repository_ref.digest:
            raise WorkflowError("evidence proposal repository manifest digest is stale")
        binding = JobResultBinding(job.digest, ordinal(job.ordinal), job.source)
        imported = project.record_artifact(
            AcquisitionStage.EVIDENCE_CLOSURE,
            GeneratedArtifact(
                AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL,
                _json_bytes(ProposalEnvelope(binding, proposal).to_dict()),
                f"codex-job-result:{binding.ordinal}:{binding.digest}",
            ),
        )
        return ProposalImport(
            ArtifactOccurrence(imported.digest, ordinal(imported.ordinal)),
            ArtifactOccurrence(binding.digest, binding.ordinal),
        )


def load_proposal_occurrence(
    project: Project,
    occurrence: ArtifactOccurrence,
) -> ProposalEnvelope:
    ref = exact_occurrence(
        project,
        stage=AcquisitionStage.EVIDENCE_CLOSURE,
        kind=AcquisitionArtifact.EVIDENCE_DISCOVERY_PROPOSAL,
        occurrence=occurrence,
    )
    try:
        return ProposalEnvelope.from_dict(json.loads(project.artifacts.read(ref)))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError("controlled evidence proposal is not UTF-8 JSON") from error


def _json_bytes(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
