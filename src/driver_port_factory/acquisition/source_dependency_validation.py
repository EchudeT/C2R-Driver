from __future__ import annotations

from pathlib import Path

from ..core.models import WorkflowError
from .accounting import RetrievalAttempt
from .closure_artifacts import EvidenceCoverageInventory
from .facets import SOURCE_DEPENDENCY_CLOSURE, SOURCE_DRIVER_ENTRY
from .material import MaterialRecord
from .source_dependencies import build_initial_dependency_inventory


def validate_initial_source_dependencies(
    root: Path,
    coverage: EvidenceCoverageInventory,
    materials: tuple[MaterialRecord, ...],
    attempts: tuple[RetrievalAttempt, ...],
) -> None:
    entry = tuple(record for record in materials if record.facet == SOURCE_DRIVER_ENTRY)
    dependency_materials = tuple(
        record for record in materials if record.facet == SOURCE_DEPENDENCY_CLOSURE
    )
    dependency_attempts = tuple(
        attempt for attempt in attempts if attempt.facet == SOURCE_DEPENDENCY_CLOSURE
    )
    if len(entry) != 1:
        raise WorkflowError("initial dependency inventory requires one source entry")
    observed = build_initial_dependency_inventory(
        root,
        entry[0],
        dependency_materials,
        dependency_attempts,
    )
    if observed != coverage.source_dependencies:
        raise WorkflowError("initial dependency inventory differs from frozen source bytes")
    dependency_coverage = next(
        item for item in coverage.facets if item.facet == SOURCE_DEPENDENCY_CLOSURE
    )
    controlled = {
        requirement.material_id
        for requirement in observed.requirements
        if requirement.material_id is not None
    }
    gaps = {
        requirement.gap_id
        for requirement in observed.requirements
        if requirement.gap_id is not None
    }
    if (
        set(dependency_coverage.material_ids) != controlled
        or set(dependency_coverage.gap_ids) != gaps
    ):
        raise WorkflowError("dependency facet accounting differs from its per-path inventory")
