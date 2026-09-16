from __future__ import annotations

from collections.abc import Hashable, Mapping, Sized
from typing import TypeVar

from ..core.models import WorkflowError
from .accounting import CoverageEntry, EvidenceGap, RetrievalAttempt, reason_for_attempts
from .closure import EvidenceClosurePlan
from .facets import (
    SOURCE_DRIVER_ENTRY,
    EvidenceFacet,
    EvidenceLane,
    FacetDisposition,
    RetrievalOutcome,
)
from .locators import EvidenceLocator
from .material import MaterialRecord


def validate_evidence_accounting(
    plan: EvidenceClosurePlan,
    materials: tuple[MaterialRecord, ...],
    coverage: tuple[CoverageEntry, ...],
    gaps: tuple[EvidenceGap, ...],
    attempts: tuple[RetrievalAttempt, ...],
) -> None:
    coverage_by_facet = {entry.facet: entry for entry in coverage}
    gaps_by_id = {gap.identifier: gap for gap in gaps}
    attempts_by_id = {attempt.identifier: attempt for attempt in attempts}
    materials_by_id = {material.identifier: material for material in materials}
    _require_unique(coverage_by_facet, coverage, "coverage facets")
    _require_unique(gaps_by_id, gaps, "gap identifiers")
    _require_unique(attempts_by_id, attempts, "retrieval attempt identifiers")
    _require_unique(materials_by_id, materials, "material identifiers")
    if {facet.lane for facet in coverage_by_facet} != set(EvidenceLane):
        raise WorkflowError("evidence coverage must contain all six evidence domains")
    _validate_attempt_plan(plan, attempts_by_id)
    controlled_ids, gap_ids = _validate_coverage_references(
        coverage_by_facet,
        gaps_by_id,
        attempts_by_id,
        materials_by_id,
        attempts,
    )
    if controlled_ids != set(materials_by_id):
        raise WorkflowError("materials manifest contains orphan controlled content")
    if gap_ids != set(gaps_by_id):
        raise WorkflowError("evidence gap register contains orphan gaps")
    retrieved = {
        identifier
        for attempt in attempts
        if attempt.outcome is RetrievalOutcome.RETRIEVED
        for identifier in attempt.material_ids
    }
    if retrieved != set(materials_by_id):
        raise WorkflowError("retrieval ledger does not account for every controlled material")
    if coverage_by_facet[SOURCE_DRIVER_ENTRY].disposition is not FacetDisposition.CONTROLLED:
        raise WorkflowError("source driver entry cannot be an explicit gap")


def _validate_coverage_references(
    coverage: dict[EvidenceFacet, CoverageEntry],
    gaps: dict[str, EvidenceGap],
    attempts: dict[str, RetrievalAttempt],
    materials: dict[str, MaterialRecord],
    ordered_attempts: tuple[RetrievalAttempt, ...],
) -> tuple[set[str], set[str]]:
    controlled_ids: set[str] = set()
    gap_ids: set[str] = set()
    for facet, entry in coverage.items():
        for identifier in entry.material_ids:
            record = materials.get(identifier)
            if record is None or record.facet != facet:
                raise WorkflowError("coverage references missing or mismatched material")
            controlled_ids.add(identifier)
        if entry.disposition is FacetDisposition.CONTROLLED:
            continue
        referenced_attempt_ids: set[str] = set()
        for gap_id in entry.gap_ids:
            gap = gaps.get(gap_id)
            if gap is None or gap.facet != facet:
                raise WorkflowError("coverage references missing or mismatched evidence gap")
            gap_attempts = tuple(attempts[item] for item in gap.retrieval_attempt_ids)
            if any(attempt.facet != facet for attempt in gap_attempts):
                raise WorkflowError("evidence gap references a mismatched retrieval attempt")
            if gap.reason is not reason_for_attempts(gap_attempts):
                raise WorkflowError("evidence gap reason differs from retrieval outcomes")
            referenced_attempt_ids.update(gap.retrieval_attempt_ids)
            gap_ids.add(gap.identifier)
        failed_attempt_ids = {
            item.identifier
            for item in ordered_attempts
            if item.facet == facet and item.outcome is not RetrievalOutcome.RETRIEVED
        }
        if referenced_attempt_ids != failed_attempt_ids:
            raise WorkflowError("evidence gap does not account for every planned retrieval")
    return controlled_ids, gap_ids


def _validate_attempt_plan(
    plan: EvidenceClosurePlan,
    attempts: dict[str, RetrievalAttempt],
) -> None:
    expected: dict[str, tuple[EvidenceFacet, EvidenceLocator]] = {}
    for proposed in plan.proposal.facets:
        for index, locator in enumerate(proposed.locators, 1):
            identifier = RetrievalAttempt.planned_identifier(proposed.facet, index)
            expected[identifier] = (proposed.facet, locator)
    if set(attempts) != set(expected):
        raise WorkflowError("retrieval ledger differs from the proposal locator plan")
    for identifier, (facet, locator) in expected.items():
        attempt = attempts[identifier]
        if attempt.facet != facet or attempt.locator != locator:
            raise WorkflowError("retrieval attempt changed its planned facet or locator")


Key = TypeVar("Key", bound=Hashable)
Value = TypeVar("Value")


def _require_unique(index: Mapping[Key, Value], records: Sized, label: str) -> None:
    if len(index) != len(records):
        raise WorkflowError(f"{label} must be unique")
