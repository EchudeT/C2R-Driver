from __future__ import annotations

from dataclasses import dataclass

from ..core.models import WorkflowError
from .facets import (
    EvidenceFacet,
    FacetDisposition,
    GapReason,
    RetrievalOutcome,
    parse_facet,
)
from .locators import EvidenceLocator, parse_locator
from .material import EvidenceContentRef
from .parsing import exact_object


@dataclass(frozen=True, slots=True)
class RetrievalAttempt:
    identifier: str
    facet: EvidenceFacet
    locator: EvidenceLocator
    outcome: RetrievalOutcome
    detail: str
    material_ids: tuple[str, ...]
    content_refs: tuple[EvidenceContentRef, ...]
    observed_at: str

    @staticmethod
    def planned_identifier(facet: EvidenceFacet, ordinal: int) -> str:
        if ordinal <= 0:
            raise WorkflowError("retrieval attempt ordinal must be positive")
        return f"{facet.lane.value}.{facet.name.value}.attempt.{ordinal}"

    @classmethod
    def from_dict(cls, value: object) -> RetrievalAttempt:
        candidate = exact_object(
            value,
            required={
                "id",
                "lane",
                "facet",
                "locator",
                "outcome",
                "detail",
                "material_ids",
                "content_refs",
                "observed_at",
            },
            label="retrieval attempt",
        )
        try:
            outcome = RetrievalOutcome(candidate["outcome"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("retrieval attempt has an invalid outcome") from error
        material_ids = _identifiers(candidate["material_ids"], "retrieval material IDs")
        content_refs = _content_refs(candidate["content_refs"])
        if outcome is RetrievalOutcome.RETRIEVED and not material_ids:
            raise WorkflowError("successful retrieval must identify controlled material")
        if outcome is not RetrievalOutcome.RETRIEVED and material_ids:
            raise WorkflowError("failed retrieval cannot identify controlled material")
        return cls(
            _identifier(candidate["id"], "retrieval attempt ID"),
            parse_facet(candidate["lane"], candidate["facet"]),
            parse_locator(candidate["locator"]),
            outcome,
            _nonempty(candidate["detail"], "retrieval detail"),
            material_ids,
            content_refs,
            _nonempty(candidate["observed_at"], "retrieval timestamp"),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            **self.facet.to_dict(),
            "locator": self.locator.to_dict(),
            "outcome": self.outcome.value,
            "detail": self.detail,
            "material_ids": list(self.material_ids),
            "content_refs": [reference.to_dict() for reference in self.content_refs],
            "observed_at": self.observed_at,
        }


@dataclass(frozen=True, slots=True)
class EvidenceGap:
    identifier: str
    facet: EvidenceFacet
    reason: GapReason
    impact: str
    repair_trigger: str
    retrieval_attempt_ids: tuple[str, ...]

    @classmethod
    def from_dict(cls, value: object) -> EvidenceGap:
        candidate = exact_object(
            value,
            required={
                "id",
                "lane",
                "facet",
                "reason",
                "impact",
                "repair_trigger",
                "retrieval_attempt_ids",
            },
            label="evidence gap",
        )
        try:
            reason = GapReason(candidate["reason"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("evidence gap has an invalid reason") from error
        attempts = _identifiers(candidate["retrieval_attempt_ids"], "gap attempt IDs")
        if not attempts:
            raise WorkflowError("evidence gap requires actual retrieval attempts")
        return cls(
            _identifier(candidate["id"], "evidence gap ID"),
            parse_facet(candidate["lane"], candidate["facet"]),
            reason,
            _nonempty(candidate["impact"], "evidence gap impact"),
            _nonempty(candidate["repair_trigger"], "evidence gap repair trigger"),
            attempts,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.identifier,
            **self.facet.to_dict(),
            "reason": self.reason.value,
            "impact": self.impact,
            "repair_trigger": self.repair_trigger,
            "retrieval_attempt_ids": list(self.retrieval_attempt_ids),
        }


@dataclass(frozen=True, slots=True)
class CoverageEntry:
    facet: EvidenceFacet
    disposition: FacetDisposition
    material_ids: tuple[str, ...]
    gap_ids: tuple[str, ...]

    @classmethod
    def controlled(cls, facet: EvidenceFacet, material_ids: tuple[str, ...]) -> CoverageEntry:
        if not material_ids:
            raise WorkflowError("controlled facet requires material")
        return cls(facet, FacetDisposition.CONTROLLED, material_ids, ())

    @classmethod
    def gap(
        cls,
        facet: EvidenceFacet,
        gap_ids: tuple[str, ...],
        material_ids: tuple[str, ...] = (),
    ) -> CoverageEntry:
        return cls(
            facet,
            FacetDisposition.EXPLICIT_GAP,
            material_ids,
            tuple(_identifier(gap_id, "gap ID") for gap_id in gap_ids),
        )

    @classmethod
    def from_dict(cls, value: object) -> CoverageEntry:
        candidate = exact_object(
            value,
            required={"lane", "facet", "disposition", "material_ids", "gap_ids"},
            label="coverage entry",
        )
        facet = parse_facet(candidate["lane"], candidate["facet"])
        try:
            disposition = FacetDisposition(candidate["disposition"])
        except (TypeError, ValueError) as error:
            raise WorkflowError("coverage entry has an invalid disposition") from error
        materials = _identifiers(candidate["material_ids"], "coverage material IDs")
        gap_ids = _identifiers(candidate["gap_ids"], "coverage gap IDs")
        if disposition is FacetDisposition.CONTROLLED:
            if not materials or gap_ids:
                raise WorkflowError("controlled facet must reference material and no gap")
        elif not gap_ids:
            raise WorkflowError("explicit gap facet must reference at least one gap")
        return cls(facet, disposition, materials, gap_ids)

    def to_dict(self) -> dict[str, object]:
        return {
            **self.facet.to_dict(),
            "disposition": self.disposition.value,
            "material_ids": list(self.material_ids),
            "gap_ids": list(self.gap_ids),
        }


_REASON_BY_OUTCOME = {
    RetrievalOutcome.NOT_FOUND: GapReason.NOT_FOUND,
    RetrievalOutcome.ACCESS_RESTRICTED: GapReason.ACCESS_RESTRICTED,
    RetrievalOutcome.LICENSE_UNCERTAIN: GapReason.LICENSE_UNCERTAIN,
    RetrievalOutcome.CONFLICT: GapReason.CONFLICTING_SOURCES,
}


def reason_for_attempts(attempts: tuple[RetrievalAttempt, ...]) -> GapReason:
    if not attempts:
        raise WorkflowError("evidence gap requires retrieval attempts")
    try:
        reasons = {_REASON_BY_OUTCOME[attempt.outcome] for attempt in attempts}
    except KeyError as error:
        raise WorkflowError(
            "successful or transiently failed retrieval cannot establish an evidence gap"
        ) from error
    if len(reasons) == 1:
        return next(iter(reasons))
    return GapReason.UNAVAILABLE_PUBLIC_EVIDENCE


def _identifiers(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise WorkflowError(f"{label} must be a list")
    return tuple(_identifier(item, label) for item in value)


def _content_refs(value: object) -> tuple[EvidenceContentRef, ...]:
    if not isinstance(value, list):
        raise WorkflowError("retrieval content references must be a list")
    return tuple(EvidenceContentRef.from_dict(item) for item in value)


def _identifier(value: object, label: str) -> str:
    text = _nonempty(value, label)
    if not all(character.isalnum() or character in "._-" for character in text):
        raise WorkflowError(f"{label} contains unsupported characters")
    return text


def _nonempty(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkflowError(f"{label} must be a non-empty string")
    return value.strip()
