from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..core.project import Project
from ..knowledge.index import file_sha256
from .ast_index import AstSemanticIndexer
from .clang_backend import ClangAnalysisBackend
from .contracts import SourceAnalysisArtifact
from .fact_parsers import RawFactParser
from .semantic_model import CallDispatch


@dataclass(frozen=True, slots=True)
class UnitResult:
    fact: dict[str, Any]
    raw_paths: tuple[Path, ...]
    semantic_path: Path
    commands_path: Path


class TranslationUnitExtractor:
    """Extract and validate the structured evidence for one frozen translation unit."""

    def __init__(
        self,
        *,
        project: Project,
        attempt_dir: Path,
        source_root: Path,
        database_by_file: dict[Path, dict[str, Any]],
        backend: ClangAnalysisBackend,
        target_triple: str,
        target_abi: dict[str, object],
        command_adapter: type,
    ) -> None:
        self.project = project
        self.attempt_dir = attempt_dir
        self.source_root = source_root
        self.database_by_file = database_by_file
        self.backend = backend
        self.target_triple = target_triple
        self.target_abi = target_abi
        self.command_adapter = command_adapter

    def extract(self, unit: Any) -> UnitResult:
        unit_id, source_path, compile_directory, arguments = self._validate_unit(unit)
        unit_dir = self.attempt_dir / "units" / self._safe_name(unit_id)
        unit_dir.mkdir(parents=True)
        extraction = self.backend.extract(
            project_root=self.project.root,
            unit_dir=unit_dir,
            unit_id=unit_id,
            source_path=source_path,
            compile_directory=compile_directory,
            arguments=arguments,
            target_triple=self.target_triple,
            expected_abi=self.target_abi,
        )
        semantic_index = AstSemanticIndexer(unit_id, source_path, extraction.typed_ast).build()
        parser = RawFactParser()
        for fact_kind, record in extraction.raw_records.items():
            fact_path = self.project.root / record["path"]
            availability, summary = parser.summarize(
                fact_kind,
                fact_path.read_bytes(),
                semantic_index,
                extraction.target_triple,
            )
            record["availability"] = availability
            record["summary"] = summary
        unresolved_calls = [
            call["node_id"]
            for call in semantic_index["indexes"]["calls"]
            if call["dispatch"] is CallDispatch.INDIRECT_UNRESOLVED
        ]
        if unresolved_calls:
            raise WorkflowError(
                f"structured call targets remain unresolved for {unit_id}: "
                + ", ".join(unresolved_calls)
            )
        semantic_path = unit_dir / "semantic-index.json"
        semantic_path.write_bytes(self._json_bytes(semantic_index))
        self.project.validators.validate(
            SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
            semantic_path.read_bytes(),
        )
        return UnitResult(
            fact={
                "unit_id": unit_id,
                "source_path": str(source_path.relative_to(self.source_root)),
                "source_sha256": file_sha256(source_path),
                "raw_facts": extraction.raw_records,
                "semantic_index": {
                    "path": str(semantic_path.relative_to(self.project.root)),
                    "sha256": file_sha256(semantic_path),
                },
                "command_records": {
                    "path": str(extraction.command_records_path.relative_to(self.project.root)),
                    "sha256": file_sha256(extraction.command_records_path),
                },
                "semantic_counts": semantic_index["counts"],
                "analyzer_target_triple": extraction.target_triple,
                "verified_target_abi": extraction.target_abi,
            },
            raw_paths=extraction.raw_paths,
            semantic_path=semantic_path,
            commands_path=extraction.command_records_path,
        )

    def _validate_unit(self, unit: Any) -> tuple[str, Path, Path, list[str]]:
        if not isinstance(unit, dict):
            raise WorkflowError("compile manifest translation unit must be an object")
        unit_id = unit.get("unit_id")
        if not isinstance(unit_id, str) or not unit_id:
            raise WorkflowError("compile manifest translation unit has no unit_id")
        source_path = (self.source_root / str(unit.get("source_path", ""))).resolve()
        self._require_within(self.source_root, source_path, unit_id)
        if not source_path.is_file() or file_sha256(source_path) != unit.get("sha256"):
            raise WorkflowError(f"translation unit source hash changed: {unit_id}")
        self._validate_dependencies(unit, unit_id)
        entry = self.database_by_file.get(source_path)
        if entry is None:
            raise WorkflowError(f"translation unit has no compilation database entry: {unit_id}")
        arguments = entry.get("arguments")
        compile_directory = Path(str(entry.get("directory", ""))).resolve()
        self._require_within(self.source_root, compile_directory, unit_id)
        if arguments != unit.get("arguments") or str(compile_directory) != unit.get(
            "compile_directory"
        ):
            raise WorkflowError(f"translation unit argv differs from compile manifest: {unit_id}")
        if not isinstance(arguments, list) or not all(isinstance(item, str) for item in arguments):
            raise WorkflowError(f"translation unit argv is invalid: {unit_id}")
        if not self.command_adapter.contains_source(arguments, compile_directory, source_path):
            raise WorkflowError(f"translation unit argv is not associated with source: {unit_id}")
        return unit_id, source_path, compile_directory, arguments

    def _validate_dependencies(self, unit: dict[str, Any], unit_id: str) -> None:
        for dependency in unit.get("dependencies", []):
            if not isinstance(dependency, dict):
                raise WorkflowError(f"translation unit has an invalid dependency: {unit_id}")
            path = (self.source_root / str(dependency.get("path", ""))).resolve()
            self._require_within(self.source_root, path, unit_id)
            if not path.is_file() or file_sha256(path) != dependency.get("sha256"):
                raise WorkflowError(f"translation unit dependency hash changed: {unit_id}")

    @staticmethod
    def _require_within(root: Path, path: Path, unit_id: str) -> None:
        if path != root and root not in path.parents:
            raise WorkflowError(f"translation unit path escapes source root: {unit_id}")

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]", "-", value).strip("-")[:80] or "unit"
        return safe + "-" + hashlib.sha256(value.encode()).hexdigest()[:8]

    @staticmethod
    def _json_bytes(value: dict[str, Any]) -> bytes:
        return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
