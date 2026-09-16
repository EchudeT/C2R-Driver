from __future__ import annotations

import re
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
        try:
            text = output.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkflowError(f"{fact_kind.value} output is not UTF-8") from error
        lines = text.splitlines()
        counts = semantic_index["counts"]
        identities = semantic_index["indexes"]["definition_identities"]

        if fact_kind is RawFactKind.TYPED_AST:
            if counts["nodes"] < 1 or counts["source_spans"] < 1:
                raise WorkflowError("typed AST lacks nodes or source spans")
            return FactAvailability.STRUCTURED, {
                "node_count": counts["nodes"],
                "source_span_count": counts["source_spans"],
            }
        if fact_kind is RawFactKind.PREPROCESSED_SOURCE:
            return self._preprocessor_summary(lines)
        if fact_kind is RawFactKind.RECORD_LAYOUT:
            records = self._parse_record_layout(lines)
            self._correlate_records(records, identities["records"])
            return (
                FactAvailability.STRUCTURED if records else FactAvailability.EMPTY_VALID,
                {
                    "record_dump_count": len(records),
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
    def _preprocessor_summary(
        lines: list[str],
    ) -> tuple[FactAvailability, dict[str, Any]]:
        line_directives = 0
        define_directives = 0
        for line in lines:
            stripped = line.lstrip()
            fields = stripped.split(maxsplit=2)
            if len(fields) >= 2 and fields[0] == "#" and fields[1].isdigit():
                line_directives += 1
            if stripped.startswith("#define "):
                define_directives += 1
        if line_directives == 0:
            raise WorkflowError("preprocessor output has no source line directives")
        return FactAvailability.RAW_VALIDATED, {
            "line_count": len(lines),
            "line_directive_count": line_directives,
            "define_directive_count": define_directives,
        }

    @staticmethod
    def _llvm_summary(
        lines: list[str], target_triple: str
    ) -> tuple[FactAvailability, dict[str, Any]]:
        data_layouts = [
            line.strip().partition("=")[2].strip().strip('"')
            for line in lines
            if line.strip().startswith("target datalayout =")
        ]
        triples = [
            line.strip().partition("=")[2].strip().strip('"')
            for line in lines
            if line.strip().startswith("target triple =")
        ]
        if len(data_layouts) != 1 or len(triples) != 1:
            raise WorkflowError("LLVM IR lacks one target data layout and target triple")
        if triples[0] != target_triple:
            raise WorkflowError(
                "LLVM IR target triple differs from the frozen effective compiler target"
            )
        return FactAvailability.RAW_VALIDATED, {
            "target_triple": triples[0],
            "target_data_layout": data_layouts[0],
            "function_definition_count": sum(line.lstrip().startswith("define ") for line in lines),
        }

    def _parse_cfg(self, lines: list[str]) -> list[dict[str, Any]]:
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

    def _parse_record_layout(self, lines: list[str]) -> list[dict[str, Any]]:
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
                declaration = field.group("declaration").strip()
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
                    }
                )
            if label and size_bytes is not None and align_bytes is not None:
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
        return records

    @classmethod
    def _correlate_functions(
        cls, functions: list[dict[str, Any]], identities: list[dict[str, Any]]
    ) -> None:
        identities_by_name = {identity["name"]: identity for identity in identities}
        if len(identities_by_name) != len(identities):
            raise WorkflowError("AST contains duplicate C function definition names")
        mapped: set[str] = set()
        for function in functions:
            candidates = cls._declarator_candidates(
                function["signature"], frozenset(identities_by_name)
            )
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
        expected = {identity["node_id"] for identity in identities}
        if mapped != expected:
            missing = sorted(expected - mapped)
            raise WorkflowError(
                "CFG does not cover every AST function definition: " + ", ".join(missing)
            )

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
