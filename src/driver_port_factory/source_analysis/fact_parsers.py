from __future__ import annotations

import json
import re
from collections.abc import Iterable
from io import StringIO
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from .fact_model import CfgBlockRole, FactAvailability, RawFactKind


class RawFactParser:
    """Validate analyzer output and correlate it one-to-one with frozen AST identities."""

    _CFG_BLOCK = re.compile(rf"^\[B(?P<number>\d+)(?: \((?P<role>{'|'.join(CfgBlockRole)})\))?\]$")
    _CFG_EDGE = re.compile(r"B\d+")
    _LAYOUT_SUMMARY = re.compile(r"^\| \[sizeof=(\d+), align=(\d+)\]$")
    _LAYOUT_FIELD = re.compile(
        r"^(?P<byte>\d+)(?::(?P<bit_start>\d+)-(?P<bit_end>\d+))? \| "
        r"(?P<declaration>.+)$"
    )
    _ANONYMOUS_LOCATION = re.compile(
        r"\((?:unnamed|anonymous) at (?P<file>.+):(?P<line>\d+):(?P<col>\d+)\)$"
    )

    def summarize(
        self,
        fact_kind: RawFactKind,
        output: bytes,
        semantic_index: dict[str, Any],
        target_triple: str,
    ) -> tuple[FactAvailability, dict[str, Any]]:
        if fact_kind is RawFactKind.TYPED_AST:
            return self._typed_ast_summary(semantic_index)
        try:
            lines = StringIO(output.decode("utf-8"))
        except UnicodeDecodeError as error:
            raise WorkflowError(f"{fact_kind.value} output is not UTF-8") from error
        return self._summarize_lines(fact_kind, lines, semantic_index, target_triple)

    def summarize_path(
        self,
        fact_kind: RawFactKind,
        path: Path,
        semantic_index: dict[str, Any],
        target_triple: str,
    ) -> tuple[FactAvailability, dict[str, Any]]:
        if fact_kind is RawFactKind.TYPED_AST:
            return self._typed_ast_summary(semantic_index)
        with path.open("r", encoding="utf-8") as lines:
            return self._summarize_lines(fact_kind, lines, semantic_index, target_triple)

    def _summarize_lines(
        self,
        fact_kind: RawFactKind,
        lines: Iterable[str],
        semantic_index: dict[str, Any],
        target_triple: str,
    ) -> tuple[FactAvailability, dict[str, Any]]:
        identities = semantic_index["indexes"]["definition_identities"]
        if fact_kind is RawFactKind.PREPROCESSED_SOURCE:
            return self._preprocessor_summary(lines)
        if fact_kind is RawFactKind.RECORD_LAYOUT:
            records = self._parse_record_layout(lines)
            self._correlate_records(records, identities["records"])
            return (
                FactAvailability.STRUCTURED if records else FactAvailability.EMPTY_VALID,
                {
                    "record_fact_count": len(records),
                    "ast_record_definition_count": len(identities["records"]),
                    "records": records,
                },
            )
        if fact_kind is RawFactKind.LLVM_IR:
            return self._llvm_summary(lines, target_triple)
        if fact_kind is RawFactKind.CFG:
            functions = self._parse_cfg(lines)
            self._correlate_functions(functions, identities["functions"])
            block_count = sum(len(function["blocks"]) for function in functions)
            edge_record_count = sum(
                bool(block["predecessors"]) + bool(block["successors"])
                for function in functions
                for block in function["blocks"]
            )
            return (
                FactAvailability.STRUCTURED if block_count else FactAvailability.EMPTY_VALID,
                {
                    "block_count": block_count,
                    "edge_record_count": edge_record_count,
                    "ast_function_definition_count": len(identities["functions"]),
                    "functions": functions,
                },
            )
        raise WorkflowError(f"unsupported raw fact kind: {fact_kind.value}")

    @staticmethod
    def _typed_ast_summary(
        semantic_index: dict[str, Any],
    ) -> tuple[FactAvailability, dict[str, Any]]:
        counts = semantic_index["counts"]
        if counts["nodes"] < 1 or counts["source_spans"] < 1:
            raise WorkflowError("typed AST lacks nodes or source spans")
        return FactAvailability.STRUCTURED, {
            "node_count": counts["nodes"],
            "source_span_count": counts["source_spans"],
        }

    @staticmethod
    def _preprocessor_summary(
        lines: Iterable[str],
    ) -> tuple[FactAvailability, dict[str, Any]]:
        line_directives = 0
        define_directives = 0
        line_count = 0
        for line in lines:
            line_count += 1
            stripped = line.lstrip()
            fields = stripped.split(maxsplit=2)
            if len(fields) >= 2 and fields[0] == "#" and fields[1].isdigit():
                line_directives += 1
            if stripped.startswith("#define "):
                define_directives += 1
        if line_directives == 0:
            raise WorkflowError("preprocessor output has no source line directives")
        return FactAvailability.RAW_VALIDATED, {
            "line_count": line_count,
            "line_directive_count": line_directives,
            "define_directive_count": define_directives,
        }

    @staticmethod
    def _llvm_summary(
        lines: Iterable[str], target_triple: str
    ) -> tuple[FactAvailability, dict[str, Any]]:
        data_layouts: list[str] = []
        triples: list[str] = []
        function_definition_count = 0
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("target datalayout ="):
                data_layouts.append(stripped.partition("=")[2].strip().strip('"'))
            if stripped.startswith("target triple ="):
                triples.append(stripped.partition("=")[2].strip().strip('"'))
            function_definition_count += line.lstrip().startswith("define ")
        if len(data_layouts) != 1 or len(triples) != 1:
            raise WorkflowError("LLVM IR lacks one target data layout and target triple")
        if triples[0] != target_triple:
            raise WorkflowError(
                "LLVM IR target triple differs from the frozen effective compiler target"
            )
        return FactAvailability.RAW_VALIDATED, {
            "target_triple": triples[0],
            "target_data_layout": data_layouts[0],
            "function_definition_count": function_definition_count,
        }

    def _parse_cfg(self, lines: Iterable[str]) -> list[dict[str, Any]]:
        functions: list[dict[str, Any]] = []
        current_function: dict[str, Any] | None = None
        current_block: dict[str, Any] | None = None
        previous_nonempty = ""
        for line in lines:
            stripped = line.strip()
            block_match = self._CFG_BLOCK.fullmatch(stripped)
            if block_match:
                role = block_match.group("role")
                if role == CfgBlockRole.ENTRY or current_function is None:
                    current_function = {
                        "signature": previous_nonempty,
                        "ast_node_id": None,
                        "blocks": [],
                    }
                    functions.append(current_function)
                current_block = {
                    "id": f"B{block_match.group('number')}",
                    "role": role,
                    "predecessors": [],
                    "successors": [],
                    "element_count": 0,
                    "has_terminator": False,
                }
                current_function["blocks"].append(current_block)
            elif current_block is not None and stripped.startswith("Preds ("):
                current_block["predecessors"] = self._CFG_EDGE.findall(stripped)
            elif current_block is not None and stripped.startswith("Succs ("):
                current_block["successors"] = self._CFG_EDGE.findall(stripped)
            elif current_block is not None and re.match(r"^\d+:", stripped):
                current_block["element_count"] += 1
            elif current_block is not None and stripped.startswith("T:"):
                current_block["has_terminator"] = True
            if stripped:
                previous_nonempty = stripped
        return functions

    def _parse_record_layout(self, lines: Iterable[str]) -> list[dict[str, Any]]:
        delimiter = "*** Dumping AST Record Layout"
        records: list[dict[str, Any]] = []
        section: list[str] = []

        def finish() -> None:
            if not section:
                return
            fields: list[dict[str, Any]] = []
            label = ""
            size_bytes: int | None = None
            align_bytes: int | None = None
            for line in section:
                stripped = line.strip()
                summary = self._LAYOUT_SUMMARY.fullmatch(stripped)
                if summary:
                    size_bytes = int(summary.group(1))
                    align_bytes = int(summary.group(2))
                    continue
                field = self._LAYOUT_FIELD.fullmatch(stripped)
                if not field:
                    continue
                raw_declaration = field.group("declaration")
                declaration = raw_declaration.strip()
                if not label:
                    label = declaration
                    continue
                fields.append(
                    {
                        "byte_offset": int(field.group("byte")),
                        "bit_start": (
                            int(field.group("bit_start"))
                            if field.group("bit_start") is not None
                            else None
                        ),
                        "bit_end": (
                            int(field.group("bit_end"))
                            if field.group("bit_end") is not None
                            else None
                        ),
                        "declaration": declaration,
                        "indent": len(raw_declaration) - len(raw_declaration.lstrip()),
                    }
                )
            if label and size_bytes is not None and align_bytes is not None:
                indentation = {
                    value: depth
                    for depth, value in enumerate(
                        sorted({field["indent"] for field in fields}),
                        start=1,
                    )
                }
                for field in fields:
                    field["depth"] = indentation[field.pop("indent")]
                records.append(
                    {
                        "record": label,
                        "ast_node_id": None,
                        "size_bytes": size_bytes,
                        "align_bytes": align_bytes,
                        "fields": fields,
                    }
                )

        for line in lines:
            if line.strip() == delimiter:
                finish()
                section = []
            else:
                section.append(line)
        finish()
        unique = {
            json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")): record
            for record in records
        }
        return list(unique.values())

    @classmethod
    def _correlate_functions(
        cls, functions: list[dict[str, Any]], identities: list[dict[str, Any]]
    ) -> None:
        identities_by_name = {identity["name"]: identity for identity in identities}
        if len(identities_by_name) != len(identities):
            raise WorkflowError("AST contains duplicate C function definition names")
        mapped: set[str] = set()
        retained: list[dict[str, Any]] = []
        for function in functions:
            candidates = cls._declarator_candidates(
                function["signature"], frozenset(identities_by_name)
            )
            if not candidates:
                continue
            if len(candidates) != 1:
                raise WorkflowError(
                    "CFG signature does not identify exactly one AST definition: "
                    f"{function['signature']!r}; candidates={sorted(candidates)}"
                )
            identity = identities_by_name[next(iter(candidates))]
            if identity["node_id"] in mapped:
                raise WorkflowError(
                    f"CFG function {identity['name']} maps to more than one AST definition"
                )
            function["ast_node_id"] = identity["node_id"]
            mapped.add(identity["node_id"])
            retained.append(function)
        expected = {identity["node_id"] for identity in identities}
        if mapped != expected:
            missing = sorted(expected - mapped)
            raise WorkflowError(
                "CFG does not cover every AST function definition: " + ", ".join(missing)
            )
        functions[:] = retained

    @staticmethod
    def _declarator_candidates(signature: str, known_names: frozenset[str]) -> set[str]:
        """Resolve the outer C declarator from lexical structure and AST candidates.

        The CFG dump is text, so it is used only as an identity carrier. Candidate names
        originate in the typed AST. Parenthesis depth separates the outer function declarator
        from callback parameters and nested declarators; ambiguity fails closed.
        """

        tokens: list[tuple[str, int]] = []
        depth = 0
        position = 0
        while position < len(signature):
            character = signature[position]
            if character == "(":
                tokens.append((character, depth))
                depth += 1
                position += 1
                continue
            if character == ")":
                depth = max(0, depth - 1)
                tokens.append((character, depth))
                position += 1
                continue
            if character == "_" or character.isalpha():
                end = position + 1
                while end < len(signature) and (signature[end] == "_" or signature[end].isalnum()):
                    end += 1
                tokens.append((signature[position:end], depth))
                position = end
                continue
            position += 1
        possible = [
            (token, token_depth)
            for index, (token, token_depth) in enumerate(tokens[:-1])
            if token in known_names and tokens[index + 1][0] == "("
        ]
        if not possible:
            return set()
        minimum_depth = min(item[1] for item in possible)
        return {name for name, candidate_depth in possible if candidate_depth == minimum_depth}

    def _correlate_records(
        self, records: list[dict[str, Any]], identities: list[dict[str, Any]]
    ) -> None:
        for identity in identities:
            candidates = [
                record for record in records if self._record_matches(record["record"], identity)
            ]
            if len(candidates) > 1:
                candidates = [
                    record
                    for record in candidates
                    if self._record_structure_matches(record, identity)
                ]
            if len(candidates) > 1:
                candidates = [
                    record
                    for record in candidates
                    if self._record_structure_matches(record, identity, compare_types=True)
                ]
            if len(candidates) != 1:
                raise WorkflowError(
                    "record-layout identity correlation failed for AST record "
                    f"{identity['node_id']}: found {len(candidates)}"
                )
            if candidates[0]["ast_node_id"] is not None:
                raise WorkflowError(
                    f"record layout {candidates[0]['record']} maps to multiple AST definitions"
                )
            candidates[0]["ast_node_id"] = identity["node_id"]
        records[:] = [record for record in records if record["ast_node_id"] is not None]

    @classmethod
    def _record_structure_matches(
        cls,
        record: dict[str, Any],
        identity: dict[str, Any],
        *,
        compare_types: bool = False,
    ) -> bool:
        ast_fields = identity["direct_fields"]
        layout_fields = [field for field in record["fields"] if field["depth"] == 1]
        if len(ast_fields) != len(layout_fields):
            return False
        if not ast_fields:
            return True
        for ast_field, layout_field in zip(ast_fields, layout_fields, strict=True):
            field_name = ast_field.get("name")
            declaration = layout_field["declaration"]
            field_type = declaration
            if field_name:
                field_type = cls._declaration_type(declaration, field_name)
                if field_type is None:
                    return False
            elif not ast_field.get("types"):
                return False
            if compare_types or not field_name:
                ast_types = {cls._normalize_type(value) for value in ast_field["types"]}
                if cls._normalize_type(field_type) not in ast_types:
                    return False
        return True

    @staticmethod
    def _declaration_type(declaration: str, field_name: str) -> str | None:
        match = re.search(rf"(?<![A-Za-z0-9_]){re.escape(field_name)}$", declaration)
        return declaration[: match.start()].strip() if match else None

    @staticmethod
    def _normalize_type(value: str) -> str:
        return " ".join(value.split())

    def _record_matches(self, layout_label: str, identity: dict[str, Any]) -> bool:
        if layout_label in identity["layout_labels"]:
            return True
        layout_location = self._ANONYMOUS_LOCATION.search(layout_label)
        return bool(
            identity["name"] is None
            and layout_location
            and any(
                source_location.get("file")
                and self._same_source_file(
                    layout_location.group("file"), source_location["file"]
                )
                and source_location.get("line") == int(layout_location.group("line"))
                and source_location.get("col") == int(layout_location.group("col"))
                for source_location in identity["source_location_candidates"]
            )
            and layout_label.startswith(identity["tag"] + " ")
        )

    @staticmethod
    def _same_source_file(layout_file: str, source_file: str) -> bool:
        if layout_file == source_file:
            return True
        if layout_file.startswith("<") or source_file.startswith("<"):
            return False
        return Path(layout_file).resolve() == Path(source_file).resolve()
