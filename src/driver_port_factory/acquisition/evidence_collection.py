from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError, utc_now
from .accounting import CoverageEntry, EvidenceGap, RetrievalAttempt, reason_for_attempts
from .facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    FacetDisposition,
    RetrievalOutcome,
)
from .locators import EvidenceLocator
from .material import GitBlobOrigin, MaterialRecord
from .proposal import EvidenceDiscoveryProposal, FacetProposal
from .repository_role import RepositoryRole
from .retrieval import EvidenceRetriever, material_identifier
from .retrieval_result import RetrievalFailure


@dataclass(frozen=True, slots=True)
class FacetRetrieval:
    materials: tuple[MaterialRecord, ...]
    attempts: tuple[RetrievalAttempt, ...]


@dataclass(frozen=True, slots=True)
class CollectedEvidence:
    materials: tuple[MaterialRecord, ...]
    coverage: tuple[CoverageEntry, ...]
    gaps: tuple[EvidenceGap, ...]
    attempts: tuple[RetrievalAttempt, ...]


class EvidenceCollector:
    """Collect planned evidence and produce typed, per-facet accounting."""

    def collect(
        self,
        proposal: EvidenceDiscoveryProposal,
        retriever: EvidenceRetriever,
        *,
        expected_source_entry: str,
    ) -> CollectedEvidence:
        materials: list[MaterialRecord] = []
        coverage: list[CoverageEntry] = []
        gaps: list[EvidenceGap] = []
        attempts: list[RetrievalAttempt] = []
        for item in proposal.facets:
            retrieval = self._retrieve_facet(retriever, item.facet, item.locators)
            attempts.extend(retrieval.attempts)
            materials.extend(retrieval.materials)
            entry, facet_gaps = self._account_facet(item, retrieval)
            coverage.append(entry)
            gaps.extend(facet_gaps)
        self._source_entry(materials, expected_source_entry)
        return CollectedEvidence(
            tuple(materials),
            tuple(sorted(coverage, key=lambda item: item.facet.sort_key)),
            tuple(sorted(gaps, key=lambda item: item.identifier)),
            tuple(attempts),
        )

    @staticmethod
    def _retrieve_facet(
        retriever: EvidenceRetriever,
        facet: EvidenceFacet,
        locators: tuple[EvidenceLocator, ...],
    ) -> FacetRetrieval:
        materials: list[MaterialRecord] = []
        attempts: list[RetrievalAttempt] = []
        for index, locator in enumerate(locators, 1):
            identifier = material_identifier(facet, locator)
            attempt_id = RetrievalAttempt.planned_identifier(facet, index)
            try:
                retrieved = retriever.retrieve(facet, locator, material_id=identifier)
                materials.append(retrieved.record)
                outcome = RetrievalOutcome.RETRIEVED
                detail = retrieved.detail
                material_ids = (identifier,)
                content_refs = retrieved.content_refs
            except RetrievalFailure as error:
                outcome = error.outcome
                detail = error.detail
                material_ids = ()
                content_refs = error.content_refs
            attempts.append(
                RetrievalAttempt(
                    attempt_id,
                    facet,
                    locator,
                    outcome,
                    detail,
                    material_ids,
                    content_refs,
                    utc_now(),
                )
            )
        return FacetRetrieval(tuple(materials), tuple(attempts))

    @staticmethod
    def _account_facet(
        proposal: FacetProposal,
        retrieval: FacetRetrieval,
    ) -> tuple[CoverageEntry, tuple[EvidenceGap, ...]]:
        if proposal.disposition is FacetDisposition.CONTROLLED:
            if not retrieval.materials:
                raise WorkflowError(
                    f"controlled facet {proposal.facet.lane.value}/"
                    f"{proposal.facet.name.value} retrieved no material"
                )
            return (
                CoverageEntry.controlled(
                    proposal.facet,
                    tuple(record.identifier for record in retrieval.materials),
                ),
                (),
            )
        if retrieval.materials:
            raise WorkflowError(
                f"facet {proposal.facet.lane.value}/{proposal.facet.name.value} was proposed "
                "as a gap but controlled content was retrieved"
            )
        if proposal.gap is None:
            raise WorkflowError("explicit gap proposal is missing its declaration")
        derived_reason = reason_for_attempts(retrieval.attempts)
        if proposal.gap.reason is not derived_reason:
            raise WorkflowError(
                f"declared gap reason {proposal.gap.reason.value} does not match "
                f"retrieval outcome {derived_reason.value}"
            )
        gap_id = f"gap.{proposal.facet.lane.value}.{proposal.facet.name.value}"
        gap = EvidenceGap(
            gap_id,
            proposal.facet,
            derived_reason,
            proposal.gap.impact,
            proposal.gap.repair_trigger,
            tuple(attempt.identifier for attempt in retrieval.attempts),
        )
        return CoverageEntry.gap(proposal.facet, (gap_id,)), (gap,)

    @staticmethod
    def _source_entry(
        materials: list[MaterialRecord],
        expected_path: str,
    ) -> MaterialRecord:
        source = [record for record in materials if record.facet == SOURCE_DRIVER_ENTRY]
        if len(source) != 1:
            raise WorkflowError("source driver entry must resolve to exactly one material")
        origin = source[0].origin
        if (
            not isinstance(origin, GitBlobOrigin)
            or origin.repository is not RepositoryRole.SOURCE
            or origin.path != expected_path
        ):
            raise WorkflowError("controlled source entry does not match the frozen envelope")
        return source[0]
