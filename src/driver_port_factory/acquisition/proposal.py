from __future__ import annotations

import json
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass

from ..codex.contracts import CodexArtifact, CodexOutputError
from ..core.models import (
    GeneratedArtifact,
    StageStatus,
    WorkflowError,
)
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .contracts import AcquisitionArtifact, AcquisitionStage
from .facet_policy import validate_locator_authority
from .facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    EvidenceLane,
    FacetDisposition,
    GapReason,
    MaterialRedistribution,
    parse_facet,
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
    GitBlobLocator,
    MaterialPolicy,
    parse_locator,
)
from .parsing import exact_object, http_url, relative_path, schema_version
from .repository_role import RepositoryRole

_EXTERNAL_REFERENCE_MAX_BYTES = 16 * 1024 * 1024
_STATIC_GIT_POLICY = MaterialPolicy(
    "review-required",
    MaterialRedistribution.UNKNOWN,
    True,
)

@dataclass(frozen=True, slots=True)
class ProposalImport:
    occurrence: ArtifactOccurrence
    job_result: ArtifactOccurrence


@dataclass(frozen=True, slots=True)
class GapDeclaration:
    reason: GapReason | None
    impact: str
    repair_trigger: str

    @classmethod
    def from_dict(cls, value: object) -> GapDeclaration:
        candidate = exact_object(
            value,
            required={"impact", "repair_trigger"},
            optional={"reason"},
            label="explicit evidence gap",
        )
        reason = None
        if "reason" in candidate:
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
        value = {
            "impact": self.impact,
            "repair_trigger": self.repair_trigger,
        }
        if self.reason is not None:
            value["reason"] = self.reason.value
        return value


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
        if not raw_locators:
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
        missing_lanes = sorted(
            set(EvidenceLane) - {item.facet.lane for item in facets},
            key=lambda lane: lane.value,
        )
        if duplicates or missing_lanes:
            raise WorkflowError(
                "evidence proposal must cover all six evidence domains without duplicate facets: "
                f"duplicates={duplicates}, missing_lanes={[lane.value for lane in missing_lanes]}"
            )
        source = next((item for item in facets if item.facet == SOURCE_DRIVER_ENTRY), None)
        if source is None:
            raise WorkflowError("evidence proposal requires the frozen source driver entry")
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


def normalize_codex_evidence_selection(
    value: object,
    *,
    migration_envelope_sha256: str,
    repository_manifest_sha256: str,
    source_driver_path: str,
    bind_document: Callable[[object], EvidenceLocator] | None = None,
) -> EvidenceDiscoveryProposal:
    """Convert semantic Codex choices into the controller-owned proposal contract."""

    candidate = exact_object(
        value,
        required={"facets"},
        optional={"schema_version"},
        label="Codex evidence selection",
    )
    if "schema_version" in candidate and candidate["schema_version"] != 1:
        raise WorkflowError("Codex evidence selection schema_version must be 1")
    raw_facets = candidate["facets"]
    if not isinstance(raw_facets, list):
        raise WorkflowError("Codex evidence selection facets must be a list")
    facets = [_static_source_entry(source_driver_path)]
    errors = []
    for index, raw_facet in enumerate(raw_facets):
        try:
            normalized = _normalize_codex_facet(raw_facet, bind_document=bind_document)
            normalized = FacetProposal.from_dict(normalized.to_dict())
        except WorkflowError as error:
            errors.append(f"facets[{index}]: {error}")
            continue
        if normalized.facet == SOURCE_DRIVER_ENTRY:
            continue
        facets.append(normalized)
    if errors:
        raise WorkflowError("Fix all invalid evidence facets in one response:\n" + "\n".join(errors))
    return EvidenceDiscoveryProposal.from_dict(
        {
            "schema_version": 1,
            "migration_envelope_sha256": migration_envelope_sha256,
            "repository_manifest_sha256": repository_manifest_sha256,
            "facets": [facet.to_dict() for facet in facets],
        }
    )


def _static_source_entry(source_driver_path: str) -> FacetProposal:
    return FacetProposal(
        SOURCE_DRIVER_ENTRY,
        FacetDisposition.CONTROLLED,
        "source driver entry frozen by the confirmed migration envelope",
        (
            GitBlobLocator(
                RepositoryRole.SOURCE,
                relative_path(source_driver_path, "frozen source driver path"),
                _STATIC_GIT_POLICY,
            ),
        ),
        None,
    )


def _normalize_codex_facet(value: object, *, bind_document=None) -> FacetProposal:
    candidate = exact_object(
        value,
        required={"lane", "facet", "rationale"},
        optional={"repository_paths", "external_urls", "external_documents", "gap"},
        label="Codex evidence facet",
    )
    facet = parse_facet(candidate["lane"], candidate["facet"])
    repository_paths = candidate.get("repository_paths", [])
    external_urls = candidate.get("external_urls", [])
    if not isinstance(repository_paths, list):
        raise WorkflowError("Codex evidence repository_paths must be a list")
    if not isinstance(external_urls, list):
        raise WorkflowError("Codex evidence external_urls must be a list")
    locators: list[EvidenceLocator] = [
        _static_git_locator(item) for item in repository_paths
    ]
    documents = candidate.get("external_documents", [])
    if not isinstance(documents, list):
        raise WorkflowError("external_documents must be a list")
    if documents:
        if bind_document is None:
            raise WorkflowError("external documents require controller retrieval")
        locators.extend(bind_document(document) for document in documents)
    locators.extend(
        ExternalReferenceLocator(
            http_url(url, "Codex evidence external URL"),
            _EXTERNAL_REFERENCE_MAX_BYTES,
        )
        for url in external_urls
    )
    gap = GapDeclaration.from_dict(candidate["gap"]) if "gap" in candidate else None
    if gap is None:
        if not locators:
            raise WorkflowError("controlled evidence requires repository_paths or external_documents")
        if external_urls:
            raise WorkflowError("controlled Codex evidence must use frozen repository paths")
        disposition = FacetDisposition.CONTROLLED
    else:
        if not locators:
            raise WorkflowError(
                "Codex evidence gap requires a repository path or external URL that was checked"
            )
        disposition = FacetDisposition.EXPLICIT_GAP
    return FacetProposal(
        facet,
        disposition,
        nonempty(candidate["rationale"], "Codex evidence facet rationale"),
        tuple(locators),
        gap,
    )


def _static_git_locator(value: object) -> GitBlobLocator:
    candidate = exact_object(
        value,
        required={"repository", "path"},
        label="Codex evidence repository path",
    )
    try:
        repository = RepositoryRole(candidate["repository"])
    except (TypeError, ValueError) as error:
        raise WorkflowError(
            "Codex evidence repository must be source, target, or qemu"
        ) from error
    return GitBlobLocator(
        repository,
        relative_path(candidate["path"], "Codex evidence repository path"),
        _STATIC_GIT_POLICY,
    )


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
        envelope_ref = project.artifact(
            IntakeStage.ENVELOPE_FREEZE, IntakeArtifact.MIGRATION_ENVELOPE
        )
        repository_ref = project.artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION,
            AcquisitionArtifact.REPOSITORY_MANIFEST,
        )
        try:
            raw_proposal = json.loads(project.artifacts.read(job))
            if isinstance(raw_proposal, dict) and {
                "migration_envelope_sha256",
                "repository_manifest_sha256",
            } <= set(raw_proposal):
                proposal = EvidenceDiscoveryProposal.from_dict(raw_proposal)
            else:
                envelope_document = project.load_json_artifact(
                    IntakeStage.ENVELOPE_FREEZE,
                    IntakeArtifact.MIGRATION_ENVELOPE,
                )
                proposal = normalize_codex_evidence_selection(
                    raw_proposal,
                    migration_envelope_sha256=envelope_ref.digest,
                    repository_manifest_sha256=repository_ref.digest,
                    source_driver_path=envelope_document[
                        "source_driver_entry_or_repository_hint"
                    ],
                    bind_document=self._document_binder(project),
                )
        except (UnicodeDecodeError, json.JSONDecodeError, WorkflowError) as error:
            raise CodexOutputError(f"invalid Codex evidence proposal: {error}") from error
        if proposal.migration_envelope_sha256 != envelope_ref.digest:
            raise CodexOutputError("evidence proposal migration envelope digest is stale")
        if proposal.repository_manifest_sha256 != repository_ref.digest:
            raise CodexOutputError("evidence proposal repository manifest digest is stale")
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

    @staticmethod
    def _document_binder(project: Project):
        from .document_binding import ExternalDocumentBinder
        return ExternalDocumentBinder(project).bind


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
