from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..core.project import Project
from ..intake.contracts import IntakeArtifact, IntakeStage
from .closure_model import ClosureContext, CoverageSet, TranslationUnitSet
from .closure_paths import require_fields, source_path
from .contracts import ClosureCategory, CoverageStatus

CLOSURE_CATEGORIES = frozenset(category.value for category in ClosureCategory)


class ClosureCoverageValidator:
    def __init__(
        self,
        project: Project,
        context: ClosureContext,
        units: TranslationUnitSet,
    ) -> None:
        self.project = project
        self.context = context
        self.units = units

    def validate(self, closure: dict[str, Any]) -> CoverageSet:
        categories = closure["closure_categories"]
        if not isinstance(categories, dict) or set(categories) != CLOSURE_CATEGORIES:
            raise WorkflowError(
                "source closure categories must exactly cover: "
                + ", ".join(sorted(CLOSURE_CATEGORIES))
            )
        records: dict[str, dict[str, Any]] = {}
        category_paths: dict[str, set[Path]] = {}
        source_files: dict[str, Path] = {}
        for name, category in categories.items():
            record, paths = self._category(name, category)
            records[name] = record
            category_paths[name] = paths
            source_files.update(
                {path.relative_to(self.context.source_root).as_posix(): path for path in paths}
            )
        self._validate_driver_coverage(category_paths)
        self._add_configuration_files(closure, source_files)
        return CoverageSet(records, source_files)

    def _category(self, name: str, value: Any) -> tuple[dict[str, Any], set[Path]]:
        category = require_fields(value, {"status", "paths", "rationale"}, name)
        try:
            status = CoverageStatus(category["status"])
        except (TypeError, ValueError) as error:
            raise WorkflowError(f"source closure category {name} has invalid status") from error
        paths = category["paths"]
        if not isinstance(paths, list):
            raise WorkflowError(f"source closure category {name}.paths must be a list")
        if status is CoverageStatus.COVERED and not paths:
            raise WorkflowError(f"covered source closure category {name} needs paths")
        if status is CoverageStatus.NOT_APPLICABLE and not str(category["rationale"]).strip():
            raise WorkflowError(f"source closure category {name} needs an N/A rationale")
        resolved = {source_path(self.context.source_root, path) for path in paths}
        record = {
            "status": status,
            "paths": sorted(
                path.relative_to(self.context.source_root).as_posix() for path in resolved
            ),
            "rationale": category["rationale"],
        }
        return record, resolved

    def _validate_driver_coverage(self, category_paths: dict[str, set[Path]]) -> None:
        missing_shared = sorted(
            path.relative_to(self.context.source_root).as_posix()
            for path in category_paths[ClosureCategory.SHARED_CORES.value]
            if path not in self.units.source_paths
        )
        if missing_shared:
            raise WorkflowError(
                "shared core files must be frozen translation units: " + ", ".join(missing_shared)
            )
        envelope = self.project.load_json_artifact(
            IntakeStage.ENVELOPE_FREEZE,
            IntakeArtifact.MIGRATION_ENVELOPE,
        )
        entry = envelope.get("source_driver_entry_or_repository_hint")
        if not isinstance(entry, str) or not entry:
            return
        entry_path = (self.context.source_root / entry).resolve()
        if entry_path.is_file() and entry_path not in self.units.source_paths:
            raise WorkflowError("the frozen source driver entry is not a translation unit")

    def _add_configuration_files(
        self, closure: dict[str, Any], source_files: dict[str, Path]
    ) -> None:
        for collection in ("configuration_inputs", "generated_headers"):
            for value in closure[collection]["paths"]:
                path = source_path(self.context.source_root, value)
                source_files[path.relative_to(self.context.source_root).as_posix()] = path
