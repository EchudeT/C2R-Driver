from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from ..core.validation import json_object, json_value
from ..knowledge.index import file_sha256
from .ast_index import AstSemanticIndexer
from .bundle_artifacts import ArtifactPayload, StructuredArtifactInventory, require_within
from .bundle_commands import CommandEvidenceValidator
from .clang_backend import CLANG_EXTRACTIONS
from .contracts import SourceAnalysisArtifact
from .fact_model import RawFactKind
from .fact_parsers import RawFactParser
from .semantic_validation import validate_semantic_index


class TranslationUnitBundleValidator:
    """Validate one-to-one closure coverage and rebuild each unit's semantic evidence."""

    def __init__(self, inventory: StructuredArtifactInventory) -> None:
        self.inventory = inventory
        self.commands = CommandEvidenceValidator(inventory)

    def validate(
        self,
        facts: dict[str, Any],
        compile_manifest: dict[str, Any],
        source_root: Path,
    ) -> Counter[str]:
        fact_units = self._unique(facts.get("units"), "structured facts")
        manifest_units = self._unique(compile_manifest.get("translation_units"), "compile manifest")
        if set(fact_units) != set(manifest_units):
            raise WorkflowError("structured facts do not cover every frozen translation unit")
        aggregate: Counter[str] = Counter()
        for unit_id, fact_unit in fact_units.items():
            aggregate.update(
                self._validate_unit(
                    unit_id,
                    fact_unit,
                    manifest_units[unit_id],
                    facts,
                    compile_manifest,
                    source_root,
                )
            )
        return aggregate

    def _validate_unit(
        self,
        unit_id: str,
        fact_unit: dict[str, Any],
        manifest_unit: dict[str, Any],
        facts: dict[str, Any],
        compile_manifest: dict[str, Any],
        source_root: Path,
    ) -> dict[str, int]:
        source_relative = manifest_unit.get("source_path")
        if fact_unit.get("source_path") != source_relative:
            raise WorkflowError(f"structured source path differs for unit {unit_id}")
        source_path = self._source_path(source_root, source_relative, unit_id)
        self._validate_source_identity(fact_unit, manifest_unit, source_path, unit_id)
        self._validate_target_identity(fact_unit, facts, unit_id)
        raw_records = self._raw_records(fact_unit, unit_id)
        raw_payloads = {
            kind: self.inventory.linked(
                SourceAnalysisArtifact.STRUCTURED_C_RAW_FACT,
                raw_records[kind.value],
                f"{unit_id}:{kind.value}",
            )
            for kind in RawFactKind
        }
        rebuilt_semantic = self._validate_semantic(fact_unit, unit_id, source_path, raw_payloads)
        self._validate_raw_summaries(
            raw_records,
            raw_payloads,
            rebuilt_semantic,
            str(fact_unit["analyzer_target_triple"]),
            unit_id,
        )
        commands_payload = self.inventory.linked(
            SourceAnalysisArtifact.STRUCTURED_C_COMMAND_RECORDS,
            fact_unit.get("command_records"),
            f"{unit_id}:command-records",
        )
        commands = json_value(commands_payload.data, "structured command records")
        self.commands.validate(
            commands,
            unit_id,
            source_path,
            source_root,
            manifest_unit,
            facts["analyzer"],
            raw_payloads,
            rebuilt_semantic,
        )
        return rebuilt_semantic["counts"]

    def _validate_semantic(
        self,
        fact_unit: dict[str, Any],
        unit_id: str,
        source_path: Path,
        raw_payloads: dict[RawFactKind, ArtifactPayload],
    ) -> dict[str, Any]:
        typed_ast = json_object(raw_payloads[RawFactKind.TYPED_AST].data, "typed AST")
        rebuilt = AstSemanticIndexer(unit_id, source_path, typed_ast).build()
        semantic_payload = self.inventory.linked(
            SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX,
            fact_unit.get("semantic_index"),
            f"{unit_id}:semantic-index",
        )
        semantic = json_object(semantic_payload.data, "semantic index")
        validate_semantic_index(semantic)
        if semantic != rebuilt:
            raise WorkflowError(f"semantic index does not match the typed AST for unit {unit_id}")
        if fact_unit.get("semantic_counts") != semantic.get("counts"):
            raise WorkflowError(f"semantic counts differ for unit {unit_id}")
        return rebuilt

    @staticmethod
    def _validate_raw_summaries(
        raw_records: dict[str, Any],
        raw_payloads: dict[RawFactKind, ArtifactPayload],
        rebuilt_semantic: dict[str, Any],
        target_triple: str,
        unit_id: str,
    ) -> None:
        parser = RawFactParser()
        specifications = {specification.kind: specification for specification in CLANG_EXTRACTIONS}
        for kind, payload in raw_payloads.items():
            record = raw_records[kind.value]
            specification = specifications[kind]
            if record.get("format") != specification.format:
                raise WorkflowError(f"raw fact format differs for {unit_id}:{kind.value}")
            availability, summary = parser.summarize(
                kind,
                payload.data,
                rebuilt_semantic,
                target_triple,
            )
            if record.get("availability") != availability or record.get("summary") != summary:
                raise WorkflowError(f"raw fact summary differs for {unit_id}:{kind.value}")

    @staticmethod
    def _raw_records(fact_unit: dict[str, Any], unit_id: str) -> dict[str, Any]:
        records = fact_unit.get("raw_facts")
        if not isinstance(records, dict) or set(records) != {kind.value for kind in RawFactKind}:
            raise WorkflowError(f"raw fact set is incomplete for unit {unit_id}")
        return records

    @staticmethod
    def _validate_source_identity(
        fact_unit: dict[str, Any],
        manifest_unit: dict[str, Any],
        source_path: Path,
        unit_id: str,
    ) -> None:
        source_digest = file_sha256(source_path)
        if source_digest != manifest_unit.get("sha256"):
            raise WorkflowError(f"frozen source hash changed for unit {unit_id}")
        if fact_unit.get("source_sha256") != source_digest:
            raise WorkflowError(f"structured source hash differs for unit {unit_id}")

    @staticmethod
    def _validate_target_identity(
        fact_unit: dict[str, Any], facts: dict[str, Any], unit_id: str
    ) -> None:
        analyzer = facts["analyzer"]
        if fact_unit.get("analyzer_target_triple") != analyzer.get("observed_target_triple"):
            raise WorkflowError(f"analyzer target differs for unit {unit_id}")
        if fact_unit.get("verified_target_abi") != analyzer.get("target_abi"):
            raise WorkflowError(f"analyzer ABI differs for unit {unit_id}")

    @staticmethod
    def _source_path(source_root: Path, value: Any, unit_id: str) -> Path:
        if not isinstance(value, str) or not value:
            raise WorkflowError(f"source path is invalid for unit {unit_id}")
        path = (source_root / value).resolve()
        require_within(source_root, path, f"source {unit_id}")
        if not path.is_file():
            raise WorkflowError(f"source file is missing for unit {unit_id}")
        return path

    @staticmethod
    def _unique(value: Any, label: str) -> dict[str, dict[str, Any]]:
        if (
            not isinstance(value, list)
            or not value
            or not all(isinstance(item, dict) for item in value)
        ):
            raise WorkflowError(f"{label} requires non-empty translation units")
        result: dict[str, dict[str, Any]] = {}
        source_paths: set[str] = set()
        for unit in value:
            unit_id = unit.get("unit_id")
            source_path = unit.get("source_path")
            if not isinstance(unit_id, str) or not unit_id:
                raise WorkflowError(f"{label} unit has no identity")
            if not isinstance(source_path, str) or not source_path:
                raise WorkflowError(f"{label} unit has no source path")
            if unit_id in result or source_path in source_paths:
                raise WorkflowError(f"{label} contains duplicate unit identity or source path")
            result[unit_id] = unit
            source_paths.add(source_path)
        return result
