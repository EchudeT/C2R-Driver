from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..core.models import WorkflowError
from .function_pointers import FunctionPointerAnalysis
from .semantic_model import (
    ASSIGNMENT_OPERATORS,
    INCREMENT_DECREMENT_OPERATORS,
    CallDispatch,
    ClangNodeKind,
    EffectKind,
    RelationKind,
)

CONTROL_FLOW_KINDS = frozenset(
    {
        ClangNodeKind.IF_STMT,
        ClangNodeKind.SWITCH_STMT,
        ClangNodeKind.CASE_STMT,
        ClangNodeKind.DEFAULT_STMT,
        ClangNodeKind.WHILE_STMT,
        ClangNodeKind.DO_STMT,
        ClangNodeKind.FOR_STMT,
        ClangNodeKind.GOTO_STMT,
        ClangNodeKind.INDIRECT_GOTO_STMT,
        ClangNodeKind.LABEL_STMT,
        ClangNodeKind.BREAK_STMT,
        ClangNodeKind.CONTINUE_STMT,
        ClangNodeKind.RETURN_STMT,
        ClangNodeKind.CONDITIONAL_OPERATOR,
        ClangNodeKind.BINARY_CONDITIONAL_OPERATOR,
    }
)


class _LocationKind(StrEnum):
    DIRECT = "direct"
    SPELLING = "spelling"
    EXPANSION = "expansion"


@dataclass(frozen=True, slots=True)
class _ResolvedLocations:
    direct: dict[str, Any] | None = None
    spelling: dict[str, Any] | None = None
    expansion: dict[str, Any] | None = None

    @property
    def primary(self) -> dict[str, Any]:
        return self.spelling or self.expansion or self.direct or {
            "file": None,
            "line": None,
            "col": None,
        }

    def candidates(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        seen: set[tuple[Any, Any, Any]] = set()
        for kind, location in (
            (_LocationKind.DIRECT, self.direct),
            (_LocationKind.SPELLING, self.spelling),
            (_LocationKind.EXPANSION, self.expansion),
        ):
            if location is None:
                continue
            identity = (location.get("file"), location.get("line"), location.get("col"))
            if identity in seen:
                continue
            seen.add(identity)
            records.append({"kind": kind, **location})
        return records

    def to_record(self) -> dict[str, dict[str, Any]]:
        return {
            kind.value: location
            for kind, location in (
                (_LocationKind.DIRECT, self.direct),
                (_LocationKind.SPELLING, self.spelling),
                (_LocationKind.EXPANSION, self.expansion),
            )
            if location is not None
        }

    @classmethod
    def from_record(cls, value: dict[str, Any]) -> _ResolvedLocations:
        return cls(
            direct=value.get(_LocationKind.DIRECT),
            spelling=value.get(_LocationKind.SPELLING),
            expansion=value.get(_LocationKind.EXPANSION),
        )


class _ClangSourceLocations:
    """Replay Clang's ordered file/line elision and index each AST node once."""

    def __init__(self) -> None:
        self.context: dict[str, Any] = {"file": None, "line": None}
        self.indexed: dict[int, _ResolvedLocations] = {}

    @classmethod
    def build(cls, root: dict[str, Any]) -> dict[int, _ResolvedLocations]:
        index = cls()
        index._scan(root)
        return index.indexed

    def scan(self, root: dict[str, Any]) -> dict[int, _ResolvedLocations]:
        self.indexed = {}
        self._scan(root)
        return self.indexed

    def _scan(self, value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                self._scan(item)
            return
        if not isinstance(value, dict):
            return
        explicit = value.get("_dpfSource")
        node_locations = (
            _ResolvedLocations.from_record(explicit) if isinstance(explicit, dict) else None
        )
        for field, child in value.items():
            if field == "loc" and isinstance(child, dict):
                resolved = self._location(child)
                if node_locations is None:
                    node_locations = resolved
            elif field == "range" and isinstance(child, dict):
                range_begin = self._range_begin(child)
                if node_locations is None:
                    node_locations = range_begin
            elif field != "_dpfSource":
                self._scan(child)
        if AstSemanticIndexer._is_node(value) and node_locations is not None:
            self.indexed[id(value)] = node_locations

    def _location(self, record: dict[str, Any]) -> _ResolvedLocations:
        direct = {"file": self.context["file"], "line": self.context["line"], "col": None}
        has_direct = False
        spelling: dict[str, Any] | None = None
        expansion: dict[str, Any] | None = None
        for field, value in record.items():
            if field in {"offset", "file", "line", "col", "tokLen"}:
                has_direct = True
            if field in {"file", "line"}:
                direct[field] = value
                self.context[field] = value
            elif field == "col":
                direct[field] = value
            elif field == "spellingLoc" and isinstance(value, dict):
                spelling = self._location(value).primary
            elif field == "expansionLoc" and isinstance(value, dict):
                expansion = self._location(value).primary
        return _ResolvedLocations(direct if has_direct else None, spelling, expansion)

    def _range_begin(self, record: dict[str, Any]) -> _ResolvedLocations | None:
        begin: _ResolvedLocations | None = None
        for field, value in record.items():
            if field in {"begin", "end"} and isinstance(value, dict):
                resolved = self._location(value)
                if field == "begin":
                    begin = resolved
            else:
                self._scan(value)
        return begin


class AstSemanticIndexer:
    """Build stable AST/CPG indexes and source identities from Clang AST JSON."""

    def __init__(self, unit_id: str, source_path: Path, ast_root: dict[str, Any]) -> None:
        if ast_root.get("kind") != ClangNodeKind.TRANSLATION_UNIT_DECL:
            raise WorkflowError(f"Clang AST root is not TranslationUnitDecl: {unit_id}")
        self.unit_id = unit_id
        self.source_path = source_path
        self.root = ast_root
        self.clang_to_stable: dict[str, str] = {}
        self.stable_to_node: dict[str, dict[str, Any]] = {}
        self.record_fields_by_type: dict[str, list[str]] = {}
        self.record_fields_by_declaration: dict[str, list[str]] = {}
        self.external_declarations: dict[str, dict[str, Any]] = {}
        self.source_locations = _ClangSourceLocations.build(ast_root)

    def build(self) -> dict[str, Any]:
        self._collect_declarations(self.root, ())
        self._collect_record_types(self.root, ())
        pointers = FunctionPointerAnalysis(
            unit_id=self.unit_id,
            root=self.root,
            clang_to_stable=self.clang_to_stable,
            stable_to_node=self.stable_to_node,
            record_fields_by_type=self.record_fields_by_type,
            declaration_target=self._declaration_target,
        ).build()

        nodes: list[dict[str, Any]] = []
        relations: list[dict[str, str]] = list(pointers.exact_relations())
        functions: list[str] = []
        function_definitions: list[str] = []
        record_definitions: list[str] = []
        function_identities: list[dict[str, Any]] = []
        record_identities: list[dict[str, Any]] = []
        globals_: list[str] = []
        calls: list[dict[str, Any]] = []
        control_flow: list[str] = []
        effects: list[dict[str, str]] = []
        source_span_count = 0

        for path, node, parent_id, parent_kind in self._walk(self.root):
            node_id = self._stable_id(path)
            record = self._node_record(node_id, node)
            if "loc" in record or "range" in record:
                source_span_count += 1
            nodes.append(record)
            if parent_id:
                relations.append(
                    {"kind": RelationKind.AST_CHILD, "source": parent_id, "target": node_id}
                )
            referenced = node.get("referencedDecl")
            if isinstance(referenced, dict) and referenced.get("id"):
                relations.append(
                    {
                        "kind": RelationKind.REFERENCES_DECL,
                        "source": node_id,
                        "target": self._declaration_target(referenced),
                    }
                )

            kind = node["kind"]
            if kind == ClangNodeKind.FUNCTION_DECL:
                functions.append(node_id)
                if self._has_child(node, ClangNodeKind.COMPOUND_STMT):
                    function_definitions.append(node_id)
                    function_identities.append(self._function_identity(node_id, node))
            if kind in {
                ClangNodeKind.RECORD_DECL,
                ClangNodeKind.CXX_RECORD_DECL,
            } and self._has_child(node, ClangNodeKind.FIELD_DECL):
                record_definitions.append(node_id)
                record_identities.append(self._record_identity(node_id, node))
            if (
                kind == ClangNodeKind.VAR_DECL
                and parent_kind == ClangNodeKind.TRANSLATION_UNIT_DECL
            ):
                globals_.append(node_id)
            if kind == ClangNodeKind.CALL_EXPR:
                effects.append({"node_id": node_id, "kind": EffectKind.CALL})
                self._index_call(node, path, node_id, pointers, calls, relations)
            if kind in CONTROL_FLOW_KINDS:
                control_flow.append(node_id)
            self._index_effect(node, node_id, effects)

        return {
            "schema_version": 1,
            "unit_id": self.unit_id,
            "source_path": str(self.source_path),
            "graph_model": "clang-ast code-property graph",
            "nodes": nodes,
            "relations": relations,
            "indexes": {
                "functions": functions,
                "function_definitions": function_definitions,
                "record_definitions": record_definitions,
                "definition_identities": {
                    "functions": function_identities,
                    "records": record_identities,
                },
                "globals": globals_,
                "calls": calls,
                "control_flow": control_flow,
                "effects": effects,
                "function_pointer_bindings": pointers.binding_records(),
                "external_declarations": [
                    self.external_declarations[key] for key in sorted(self.external_declarations)
                ],
            },
            "counts": self._counts(
                nodes,
                relations,
                functions,
                function_definitions,
                record_definitions,
                globals_,
                calls,
                control_flow,
                effects,
                source_span_count,
                len(self.external_declarations),
            ),
            "effect_classification": (
                "Structural candidates only; hardware meaning requires evidence-backed contract "
                "mapping."
            ),
        }

    def _collect_declarations(self, node: Any, path: tuple[int, ...]) -> None:
        if not self._is_node(node):
            return
        node_id = self._stable_id(path)
        if node.get("id"):
            self.clang_to_stable[str(node["id"])] = node_id
            self.stable_to_node[node_id] = node
        for child_index, child in enumerate(node.get("inner", [])):
            self._collect_declarations(child, (*path, child_index))

    def _collect_record_types(self, node: Any, path: tuple[int, ...]) -> None:
        if not self._is_node(node):
            return
        if node["kind"] in {ClangNodeKind.RECORD_DECL, ClangNodeKind.CXX_RECORD_DECL}:
            fields = [
                self._stable_id((*path, index))
                for index, child in enumerate(node.get("inner", []))
                if self._is_node(child) and child["kind"] == ClangNodeKind.FIELD_DECL
            ]
            name = node.get("name")
            if name:
                self.record_fields_by_type[f"{node.get('tagUsed', 'struct')} {name}"] = fields
            self.record_fields_by_declaration[self._stable_id(path)] = fields
        if node["kind"] == ClangNodeKind.TYPEDEF_DECL:
            field_sets = {
                tuple(self.record_fields_by_declaration[declaration_id])
                for declaration_id in self._referenced_record_declarations(node)
                if declaration_id in self.record_fields_by_declaration
            }
            if len(field_sets) == 1:
                fields = list(field_sets.pop())
                aliases = set(self._type_names(node.get("type")))
                if isinstance(node.get("name"), str):
                    aliases.add(node["name"])
                for alias in aliases:
                    self.record_fields_by_type[alias.removeprefix("const ")] = fields
        for child_index, child in enumerate(node.get("inner", [])):
            self._collect_record_types(child, (*path, child_index))

    def _index_call(
        self,
        node: dict[str, Any],
        path: tuple[int, ...],
        node_id: str,
        pointers: FunctionPointerAnalysis,
        calls: list[dict[str, Any]],
        relations: list[dict[str, str]],
    ) -> None:
        target = self._direct_callee(node)
        if target is not None:
            target_id = self._declaration_target(target)
            calls.append(
                {"node_id": node_id, "dispatch": CallDispatch.DIRECT, "target_id": target_id}
            )
            relations.append(
                {
                    "kind": RelationKind.DIRECT_CALL_TARGET,
                    "source": node_id,
                    "target": target_id,
                }
            )
            return
        inner = node.get("inner")
        callee = inner[0] if isinstance(inner, list) and inner and self._is_node(inner[0]) else None
        resolution = pointers.resolve(callee)
        resolved = bool(resolution.candidate_targets) and resolution.complete
        call: dict[str, Any] = {
            "node_id": node_id,
            "dispatch": (
                CallDispatch.INDIRECT_RESOLVED if resolved else CallDispatch.INDIRECT_UNRESOLVED
            ),
            "callee_declaration_ids": sorted(
                {identifier for holder in resolution.holders for identifier in holder if identifier}
            ),
            "callee_holders": [
                {
                    "object_id": object_id,
                    "holder_id": holder_id,
                    "status": pointers.holder_status((object_id, holder_id)),
                }
                for object_id, holder_id in sorted(
                    resolution.holders, key=lambda holder: (holder[0] or "", holder[1])
                )
            ],
            "candidate_target_ids": sorted(resolution.candidate_targets),
            "target_set_complete": resolved,
            "resolution_bases": resolution.bases,
        }
        if callee is not None:
            call["callee_expression_node_id"] = self._stable_id((*path, 0))
            if isinstance(callee.get("type"), dict):
                call["callee_type"] = callee["type"]
        calls.append(call)
        if resolved:
            for target_id in sorted(resolution.candidate_targets):
                relations.append(
                    {
                        "kind": RelationKind.INDIRECT_CALL_TARGET,
                        "source": node_id,
                        "target": target_id,
                    }
                )

    @staticmethod
    def _index_effect(node: dict[str, Any], node_id: str, effects: list[dict[str, str]]) -> None:
        kind = node["kind"]
        opcode = node.get("opcode")
        if kind == ClangNodeKind.BINARY_OPERATOR and opcode in ASSIGNMENT_OPERATORS:
            effects.append({"node_id": node_id, "kind": EffectKind.ASSIGNMENT})
        if kind == ClangNodeKind.UNARY_OPERATOR and opcode in INCREMENT_DECREMENT_OPERATORS:
            effects.append({"node_id": node_id, "kind": EffectKind.INCREMENT_DECREMENT})
        effect_by_kind = {
            ClangNodeKind.RETURN_STMT: EffectKind.RETURN,
            ClangNodeKind.GOTO_STMT: EffectKind.GOTO,
            ClangNodeKind.INDIRECT_GOTO_STMT: EffectKind.GOTO,
            ClangNodeKind.BREAK_STMT: EffectKind.LOOP_CONTROL,
            ClangNodeKind.CONTINUE_STMT: EffectKind.LOOP_CONTROL,
            ClangNodeKind.ATOMIC_EXPR: EffectKind.ATOMIC,
            ClangNodeKind.GCC_ASM_STMT: EffectKind.INLINE_ASM,
            ClangNodeKind.MS_ASM_STMT: EffectKind.INLINE_ASM,
        }
        if kind in effect_by_kind:
            effects.append({"node_id": node_id, "kind": effect_by_kind[kind]})
        qual_type = (
            node.get("type", {}).get("qualType") if isinstance(node.get("type"), dict) else None
        )
        if isinstance(qual_type, str) and "volatile" in qual_type.split():
            effects.append({"node_id": node_id, "kind": EffectKind.VOLATILE_TYPED_EXPRESSION})

    @staticmethod
    def _counts(
        nodes: list[dict[str, Any]],
        relations: list[dict[str, str]],
        functions: list[str],
        function_definitions: list[str],
        record_definitions: list[str],
        globals_: list[str],
        calls: list[dict[str, Any]],
        control_flow: list[str],
        effects: list[dict[str, str]],
        source_spans: int,
        external_declarations: int,
    ) -> dict[str, int]:
        return {
            "nodes": len(nodes),
            "relations": len(relations),
            "functions": len(functions),
            "function_definitions": len(function_definitions),
            "record_definitions": len(record_definitions),
            "globals": len(globals_),
            "calls": len(calls),
            "direct_calls": sum(call["dispatch"] == CallDispatch.DIRECT for call in calls),
            "indirect_calls": sum(call["dispatch"] != CallDispatch.DIRECT for call in calls),
            "resolved_indirect_calls": sum(
                call["dispatch"] == CallDispatch.INDIRECT_RESOLVED for call in calls
            ),
            "unresolved_indirect_calls": sum(
                call["dispatch"] == CallDispatch.INDIRECT_UNRESOLVED for call in calls
            ),
            "control_flow": len(control_flow),
            "effects": len(effects),
            "source_spans": source_spans,
            "external_declarations": external_declarations,
        }

    def _node_record(self, node_id: str, node: dict[str, Any]) -> dict[str, Any]:
        record: dict[str, Any] = {"id": node_id, "kind": node["kind"]}
        for field in (
            "name",
            "mangledName",
            "storageClass",
            "valueCategory",
            "opcode",
            "castKind",
            "isImplicit",
            "isUsed",
        ):
            if field in node:
                record[field] = node[field]
        if isinstance(node.get("type"), dict):
            record["type"] = node["type"]
        for field in ("loc", "range"):
            if isinstance(node.get(field), dict):
                record[field] = node[field]
        return record

    def _function_identity(self, node_id: str, node: dict[str, Any]) -> dict[str, Any]:
        name = str(node.get("name", ""))
        if not name:
            raise WorkflowError(f"function definition has no identity: {node_id}")
        return {
            "node_id": node_id,
            "name": name,
            "mangled_name": node.get("mangledName"),
            "type": node.get("type", {}).get("qualType"),
            "source_location": self._source_location(node),
        }

    def _record_identity(self, node_id: str, node: dict[str, Any]) -> dict[str, Any]:
        tag = str(node.get("tagUsed", "struct"))
        name = node.get("name")
        location = self._source_location(node)
        labels = [f"{tag} {name}"] if name else []
        candidates = self._source_location_candidates(node)
        if not name:
            for candidate in candidates:
                if all(candidate.get(field) is not None for field in ("file", "line", "col")):
                    file_name = candidate["file"]
                    position = f"{file_name}:{candidate['line']}:{candidate['col']}"
                    labels.extend(
                        (
                            f"{tag} (unnamed at {position})",
                            f"{tag} (anonymous at {position})",
                        )
                    )
        labels = list(dict.fromkeys(labels))
        if not labels:
            raise WorkflowError(f"record definition has no correlatable identity: {node_id}")
        return {
            "node_id": node_id,
            "tag": tag,
            "name": name,
            "direct_fields": [
                {
                    "name": child.get("name"),
                    "types": list(self._type_names(child.get("type"))),
                }
                for child in node.get("inner", [])
                if self._is_node(child) and child["kind"] == ClangNodeKind.FIELD_DECL
            ],
            "layout_labels": labels,
            "source_location": location,
            "source_location_candidates": candidates,
        }

    def _source_location(self, node: dict[str, Any]) -> dict[str, Any]:
        locations = self.source_locations.get(id(node))
        return dict(locations.primary) if locations else {"file": None, "line": None, "col": None}

    def _source_location_candidates(self, node: dict[str, Any]) -> list[dict[str, Any]]:
        locations = self.source_locations.get(id(node))
        return locations.candidates() if locations else []

    def _referenced_record_declarations(self, node: Any) -> set[str]:
        result: set[str] = set()
        if not isinstance(node, dict):
            return result
        for field in ("ownedTagDecl", "decl"):
            declaration = node.get(field)
            if (
                isinstance(declaration, dict)
                and declaration.get("kind")
                in {ClangNodeKind.RECORD_DECL, ClangNodeKind.CXX_RECORD_DECL}
                and declaration.get("id")
            ):
                target = self.clang_to_stable.get(str(declaration["id"]))
                if target:
                    result.add(target)
        for child in node.get("inner", []):
            result.update(self._referenced_record_declarations(child))
        return result

    def _declaration_target(self, referenced: dict[str, Any]) -> str:
        clang_id = str(referenced.get("id", ""))
        if clang_id in self.clang_to_stable:
            return self.clang_to_stable[clang_id]
        fields = {key: value for key, value in referenced.items() if key != "id"}
        material = json.dumps(
            fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        target = "external-decl:" + hashlib.sha256(material).hexdigest()[:20]
        self.external_declarations.setdefault(target, {"id": target, **fields})
        return target

    @classmethod
    def _direct_callee(cls, call: dict[str, Any]) -> dict[str, Any] | None:
        inner = call.get("inner")
        if not isinstance(inner, list) or not inner:
            return None

        def find_reference(node: Any) -> dict[str, Any] | None:
            if not cls._is_node(node):
                return None
            referenced = node.get("referencedDecl")
            if (
                isinstance(referenced, dict)
                and referenced.get("kind") == ClangNodeKind.FUNCTION_DECL
                and referenced.get("id")
            ):
                return referenced
            children = node.get("inner")
            if not isinstance(children, list) or len(children) != 1:
                return None
            return find_reference(children[0])

        return find_reference(inner[0])

    def _walk(
        self,
        node: Any,
        path: tuple[int, ...] = (),
        parent_id: str | None = None,
        parent_kind: str | None = None,
    ) -> Iterator[tuple[tuple[int, ...], dict[str, Any], str | None, str | None]]:
        if not self._is_node(node):
            return
        yield path, node, parent_id, parent_kind
        node_id = self._stable_id(path)
        for child_index, child in enumerate(node.get("inner", [])):
            yield from self._walk(child, (*path, child_index), node_id, node["kind"])

    def _stable_id(self, path: tuple[int, ...]) -> str:
        suffix = ".".join(map(str, path)) if path else "root"
        return f"{self.unit_id}:{suffix}"

    @staticmethod
    def _type_names(type_record: Any) -> tuple[str, ...]:
        if not isinstance(type_record, dict):
            return ()
        return tuple(
            value
            for field in ("desugaredQualType", "qualType")
            if isinstance((value := type_record.get(field)), str)
        )

    @staticmethod
    def _has_child(node: dict[str, Any], kind: ClangNodeKind) -> bool:
        return any(
            AstSemanticIndexer._is_node(child) and child["kind"] == kind
            for child in node.get("inner", [])
        )

    @staticmethod
    def _is_node(value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("kind"), str)
