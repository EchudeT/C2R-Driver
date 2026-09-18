from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .closure_model import ClosureContext, TranslationUnitSet
from .closure_paths import require_fields, source_directory, source_path


@dataclass(frozen=True, slots=True)
class ValidatedUnit:
    record: dict[str, Any]
    database_record: dict[str, Any]
    source_path: Path
    source_files: dict[str, Path]
    defines: frozenset[str]
    include_paths: frozenset[Path]
    target_triple: str
    target_abi: dict[str, object]


class TranslationUnitValidator:
    REQUIRED_FIELDS = frozenset(
        {
            "unit_id",
            "source_path",
            "sha256",
            "compile_directory",
            "arguments",
            "dependencies",
        }
    )

    def __init__(self, context: ClosureContext) -> None:
        self.context = context

    def validate(self, value: Any) -> TranslationUnitSet:
        if not isinstance(value, list) or not value:
            raise WorkflowError("source closure requires at least one translation unit")
        units = [self._validate_unit(unit) for unit in value]
        self._require_unique(units)
        self._validate_aggregate(units)
        source_files = {
            relative: path for unit in units for relative, path in unit.source_files.items()
        }
        return TranslationUnitSet(
            tuple(unit.record for unit in units),
            tuple(unit.database_record for unit in units),
            source_files,
            frozenset(unit.source_path for unit in units),
            units[0].target_triple,
            units[0].target_abi,
        )

    def _validate_unit(self, value: Any) -> ValidatedUnit:
        unit = require_fields(value, self.REQUIRED_FIELDS, "translation unit")
        unit_id = unit["unit_id"]
        if not isinstance(unit_id, str) or not unit_id:
            raise WorkflowError("translation unit unit_id must be a non-empty string")
        path = source_path(self.context.source_root, unit["source_path"])
        if file_sha256(path) != str(unit["sha256"]).lower():
            raise WorkflowError(f"translation unit hash mismatch: {unit['source_path']}")
        directory = source_directory(self.context.source_root, unit["compile_directory"])
        arguments = self._arguments(unit, directory, path)
        dependencies = self._dependencies(unit, arguments, directory, path)
        relative = path.relative_to(self.context.source_root).as_posix()
        files = {
            record["path"]: source_path(self.context.source_root, record["path"])
            for record in dependencies
        }
        files[relative] = path
        return ValidatedUnit(
            {
                "unit_id": unit_id,
                "source_path": relative,
                "sha256": file_sha256(path),
                "compile_directory": str(directory),
                "arguments": arguments,
                "dependencies": dependencies,
            },
            {"directory": str(directory), "file": str(path), "arguments": arguments},
            path,
            files,
            frozenset(self.context.command_adapter.option_values(arguments, "-D")),
            frozenset(
                self.context.command_adapter.resolved_include_arguments(arguments, directory)
            ),
            self.context.command_adapter.effective_target_triple(
                arguments,
                directory,
                executable=self.context.compiler_path,
            ),
            self.context.command_adapter.abi_signature(arguments, directory),
        )

    def _arguments(
        self,
        unit: dict[str, Any],
        compile_directory: Path,
        path: Path,
    ) -> list[str]:
        arguments = unit["arguments"]
        if (
            not isinstance(arguments, list)
            or not arguments
            or not all(isinstance(argument, str) and argument for argument in arguments)
        ):
            raise WorkflowError("translation unit arguments must be a non-empty argv list")
        argument_compiler = shutil.which(arguments[0])
        if (
            not argument_compiler
            or Path(argument_compiler).resolve() != self.context.compiler_path.resolve()
        ):
            raise WorkflowError("translation unit argv does not use the frozen compiler")
        if not self.context.command_adapter.contains_source(arguments, compile_directory, path):
            raise WorkflowError(
                f"translation unit argv does not compile its source_path: {unit['source_path']}"
            )
        language = self.context.compiler["language_mode"]
        if f"-std={language}" not in arguments:
            raise WorkflowError(f"translation unit argv does not select {language}")
        return arguments

    def _dependencies(
        self,
        unit: dict[str, Any],
        arguments: list[str],
        compile_directory: Path,
        path: Path,
    ) -> list[dict[str, Any]]:
        dependencies = unit["dependencies"]
        if not isinstance(dependencies, list):
            raise WorkflowError("translation unit dependencies must be a list")
        records = [self._dependency(dependency) for dependency in dependencies]
        declared = {source_path(self.context.source_root, record["path"]) for record in records}
        discovered = self.context.command_adapter.dependencies(
            arguments,
            compile_directory,
            self.context.source_root,
            path,
        )
        missing = sorted(
            dependency.relative_to(self.context.source_root).as_posix()
            for dependency in discovered - declared
        )
        if missing:
            raise WorkflowError(
                f"translation unit {unit['unit_id']} omits compiler-discovered dependencies: "
                + ", ".join(missing)
            )
        return records

    def _dependency(self, value: Any) -> dict[str, Any]:
        dependency = require_fields(
            value,
            {"path", "sha256", "role"},
            "source dependency",
        )
        path = source_path(self.context.source_root, dependency["path"])
        if file_sha256(path) != str(dependency["sha256"]).lower():
            raise WorkflowError(f"source dependency hash mismatch: {dependency['path']}")
        return {
            "path": path.relative_to(self.context.source_root).as_posix(),
            "sha256": file_sha256(path),
            "role": dependency["role"],
        }

    @staticmethod
    def _require_unique(units: list[ValidatedUnit]) -> None:
        identifiers = [unit.record["unit_id"] for unit in units]
        sources = [unit.source_path for unit in units]
        if len(identifiers) != len(set(identifiers)):
            raise WorkflowError("duplicate translation unit id")
        if len(sources) != len(set(sources)):
            raise WorkflowError("duplicate translation unit source")

    def _validate_aggregate(self, units: list[ValidatedUnit]) -> None:
        if any(unit.defines != frozenset(self.context.defines) for unit in units):
            raise WorkflowError(
                "source closure defines differ from the explicit -D compile arguments"
            )
        expected_includes = frozenset(self.context.resolved_include_paths)
        if any(unit.include_paths != expected_includes for unit in units):
            raise WorkflowError(
                "source closure include_paths differ from the explicit -I compile arguments"
            )
        if any(unit.target_abi != units[0].target_abi for unit in units):
            raise WorkflowError("translation units do not share one frozen target ABI")
        if any(unit.target_triple != units[0].target_triple for unit in units):
            raise WorkflowError("translation units do not share one frozen target triple")
        if self.context.compiler["target_triple"] != units[0].target_triple:
            raise WorkflowError(
                "declared compiler target_triple differs from the translation-unit argv"
            )
        if self.context.compiler["target_abi"] != units[0].target_abi:
            raise WorkflowError("declared compiler target_abi differs from the compile argv probe")
