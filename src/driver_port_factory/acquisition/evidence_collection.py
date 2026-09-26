from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from ..codex.contracts import CodexOutputError
from ..core.models import WorkflowError, utc_now
from .accounting import CoverageEntry, EvidenceGap, RetrievalAttempt, reason_for_attempts
from .facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    FacetDisposition,
    GapReason,
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
        errors: list[str] = []
        for item in proposal.facets:
            retrieval = self._retrieve_facet(retriever, item.facet, item.locators)
            attempts.extend(retrieval.attempts)
            materials.extend(retrieval.materials)
            try:
                entry, facet_gaps = self._account_facet(item, retrieval)
            except CodexOutputError as error:
                errors.append(str(error))
                continue
            coverage.append(entry)
            gaps.extend(facet_gaps)
        if errors:
            raise CodexOutputError("Repair all invalid evidence selections together:\n" + "\n".join(errors))
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
                raise CodexOutputError(
                    f"controlled facet {proposal.facet.lane.value}/"
                    f"{proposal.facet.name} retrieved no material: "
                    + "; ".join(attempt.detail for attempt in retrieval.attempts)
                )
            return (
                CoverageEntry.controlled(
                    proposal.facet,
                    tuple(record.identifier for record in retrieval.materials),
                ),
                (),
            )
        if proposal.gap is None:
            raise WorkflowError("explicit gap proposal is missing its declaration")
        failed_attempts = tuple(
            attempt
            for attempt in retrieval.attempts
            if attempt.outcome is not RetrievalOutcome.RETRIEVED
        )
        derived_reason = (
            reason_for_attempts(failed_attempts)
            if failed_attempts
            else GapReason.UNAVAILABLE_PUBLIC_EVIDENCE
        )
        if proposal.gap.reason is not None and proposal.gap.reason is not derived_reason:
            raise WorkflowError(
                f"declared gap reason {proposal.gap.reason.value} does not match "
                f"retrieval outcome {derived_reason.value}"
            )
        gap_id = f"gap.{proposal.facet.lane.value}.{proposal.facet.name}"
        gap = EvidenceGap(
            gap_id,
            proposal.facet,
            derived_reason,
            proposal.gap.impact,
            proposal.gap.repair_trigger,
            tuple(attempt.identifier for attempt in retrieval.attempts),
        )
        return (
            CoverageEntry.gap(
                proposal.facet,
                (gap_id,),
                tuple(record.identifier for record in retrieval.materials),
            ),
            (gap,),
        )

    @staticmethod
    def _source_entry(
        materials: list[MaterialRecord],
        expected_path: str,
    ) -> MaterialRecord:
        source = [record for record in materials if record.facet == SOURCE_DRIVER_ENTRY]
        if not source:
            raise WorkflowError("source driver entry must resolve to at least one material")
        expected = PurePosixPath(expected_path)
        for record in source:
            origin = record.origin
            if (
                not isinstance(origin, GitBlobOrigin)
                or origin.repository is not RepositoryRole.SOURCE
                or not (
                    origin.path == expected_path
                    or expected in PurePosixPath(origin.path).parents
                )
            ):
                raise WorkflowError("controlled source entry does not match the frozen envelope")
        # A directory-scoped source entry is represented by all of its
        # controller-expanded tracked files; a file-scoped entry has one.
        return source[0]
