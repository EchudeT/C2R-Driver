from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..core.models import WorkflowError
from .accounting import CoverageEntry, EvidenceGap, RetrievalAttempt
from .material import MaterialRecord, parse_materials
from .parsing import exact_object, schema_version
from .source_dependencies import InitialDependencyInventory

if TYPE_CHECKING:
    from .closure import EvidenceClosurePlan


@dataclass(frozen=True, slots=True)
class EvidenceCoverageInventory:
    facets: tuple[CoverageEntry, ...]
    source_dependencies: InitialDependencyInventory
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> EvidenceCoverageInventory:
        candidate = exact_object(
            value,
            required={"schema_version", "facets", "source_dependencies"},
            label="evidence coverage inventory",
        )
        schema_version(candidate, "evidence coverage inventory")
        raw_facets = candidate["facets"]
        if not isinstance(raw_facets, list):
            raise WorkflowError("evidence coverage inventory facets must be a list")
        return cls(
            tuple(CoverageEntry.from_dict(item) for item in raw_facets),
            InitialDependencyInventory.from_dict(candidate["source_dependencies"]),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "facets": [item.to_dict() for item in self.facets],
            "source_dependencies": self.source_dependencies.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class EvidenceGapRegister:
    gaps: tuple[EvidenceGap, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> EvidenceGapRegister:
        candidate = exact_object(
            value,
            required={"schema_version", "gaps"},
            label="evidence gap register",
        )
        schema_version(candidate, "evidence gap register")
        raw_gaps = candidate["gaps"]
        if not isinstance(raw_gaps, list):
            raise WorkflowError("evidence gap register gaps must be a list")
        return cls(tuple(EvidenceGap.from_dict(item) for item in raw_gaps))

    def to_dict(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "gaps": [gap.to_dict() for gap in self.gaps]}


@dataclass(frozen=True, slots=True)
class EvidenceRetrievalLedger:
    attempts: tuple[RetrievalAttempt, ...]
    schema_version = 1

    @classmethod
    def from_dict(cls, value: object) -> EvidenceRetrievalLedger:
        candidate = exact_object(
            value,
            required={"schema_version", "attempts"},
            label="evidence retrieval ledger",
        )
        schema_version(candidate, "evidence retrieval ledger")
        raw_attempts = candidate["attempts"]
        if not isinstance(raw_attempts, list) or not raw_attempts:
            raise WorkflowError("evidence retrieval ledger requires attempts")
        return cls(tuple(RetrievalAttempt.from_dict(item) for item in raw_attempts))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass(frozen=True, slots=True)
class EvidenceMaterialsManifest:
    records: tuple[MaterialRecord, ...]

    @classmethod
    def from_bytes(cls, data: bytes) -> EvidenceMaterialsManifest:
        return cls(parse_materials(data))


@dataclass(frozen=True, slots=True)
class EvidenceClosureBundle:
    plan: EvidenceClosurePlan
    materials: EvidenceMaterialsManifest
    coverage: EvidenceCoverageInventory
    gaps: EvidenceGapRegister
    ledger: EvidenceRetrievalLedger

    def __post_init__(self) -> None:
        if not self.materials.records:
            raise WorkflowError("evidence closure bundle requires controlled materials")
