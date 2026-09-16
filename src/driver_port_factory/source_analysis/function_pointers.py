from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from ..core.models import WorkflowError
from .semantic_model import (
    ASSIGNMENT_OPERATORS,
    INCREMENT_DECREMENT_OPERATORS,
    CallDispatch,
    CallResolutionBasis,
    ClangNodeKind,
    ClangStorageClass,
    COperator,
    PointerTargetStatus,
    RelationKind,
)

PointerBindingKey = tuple[str | None, str]


@dataclass(frozen=True, slots=True)
class PointerResolution:
    holders: frozenset[PointerBindingKey]
    candidate_targets: frozenset[str]
    complete: bool
    bases: tuple[CallResolutionBasis, ...]


class FunctionPointerAnalysis:
    """Conservative, object-sensitive target analysis for immutable C pointer holders."""

    def __init__(
        self,
        *,
        unit_id: str,
        root: dict[str, Any],
        clang_to_stable: dict[str, str],
        stable_to_node: dict[str, dict[str, Any]],
        record_fields_by_type: dict[str, list[str]],
        closure_field_by_declaration: dict[str, str],
        declaration_target: Callable[[dict[str, Any]], str],
    ) -> None:
        self.unit_id = unit_id
        self.root = root
        self.clang_to_stable = clang_to_stable
        self.stable_to_node = stable_to_node
        self.record_fields_by_type = record_fields_by_type
        self.closure_field_by_declaration = closure_field_by_declaration
        self.declaration_target = declaration_target
        self.targets: dict[PointerBindingKey, set[str]] = {}
        self.status: dict[PointerBindingKey, PointerTargetStatus] = {}
        self.declarations: dict[PointerBindingKey, dict[str, Any]] = {}
        self.closure_assignments: list[dict[str, Any]] = []

    def build(self) -> FunctionPointerAnalysis:
        self._collect_declarations(self.root, (), None)
        self._collect_initial_bindings(self.root, (), None)
        self._collect_closure_assignments(self.root, ())
        self._invalidate_mutable_or_escaped_bindings()
        return self

    def resolve(self, callee: Any) -> PointerResolution:
        holders = self._pointer_holders(callee)
        expression_targets, expression_complete = self._expression_targets(callee)
        all_holders_exact = all(
            self.status.get(holder) is PointerTargetStatus.EXACT for holder in holders
        )
        declaration_targets = set().union(*(self.targets.get(holder, set()) for holder in holders))
        bases: list[CallResolutionBasis] = []
        if expression_targets:
            bases.append(CallResolutionBasis.CALLEE_EXPRESSION)
        if declaration_targets:
            bases.append(CallResolutionBasis.DECLARATION_POINTS_TO)
        return PointerResolution(
            holders=frozenset(holders),
            candidate_targets=frozenset(expression_targets | declaration_targets),
            complete=expression_complete and all_holders_exact,
            bases=tuple(bases),
        )

    def binding_records(self) -> list[dict[str, Any]]:
        records = []
        for key, target_ids in self._ordered_bindings():
            object_id, holder_id = key
            records.append(
                {
                    "object_id": object_id,
                    "holder_id": holder_id,
                    "status": self.status.get(key, PointerTargetStatus.UNKNOWN),
                    "candidate_target_ids": sorted(target_ids),
                }
            )
        return records

    def closure_assignment_records(self) -> list[dict[str, Any]]:
        return sorted(
            self.closure_assignments,
            key=lambda record: (record["holder_id"], record["assignment_node_id"]),
        )

    def exact_relations(self) -> Iterator[dict[str, str]]:
        for key, target_ids in self._ordered_bindings():
            if self.status.get(key) is not PointerTargetStatus.EXACT:
                continue
            _, holder_id = key
            for target_id in sorted(target_ids):
                yield {
                    "kind": RelationKind.FUNCTION_POINTER_TARGET,
                    "source": holder_id,
                    "target": target_id,
                }

    def holder_status(self, holder: PointerBindingKey) -> PointerTargetStatus:
        return self.status.get(holder, PointerTargetStatus.UNKNOWN)

    def _collect_declarations(
        self, node: Any, path: tuple[int, ...], parent_kind: str | None
    ) -> None:
        if not self._is_node(node):
            return
        node_id = self._stable_id(path)
        if node["kind"] == ClangNodeKind.VAR_DECL:
            self.declarations[(None, node_id)] = {
                "node": node,
                "is_global": parent_kind == ClangNodeKind.TRANSLATION_UNIT_DECL,
            }
        for child_index, child in enumerate(node.get("inner", [])):
            self._collect_declarations(child, (*path, child_index), node["kind"])

    def _collect_initial_bindings(
        self, node: Any, path: tuple[int, ...], parent_kind: str | None
    ) -> None:
        if not self._is_node(node):
            return
        if node["kind"] == ClangNodeKind.VAR_DECL:
            object_id = self._stable_id(path)
            targets, complete = self._expression_targets_from_children(node)
            if self._is_function_pointer_declaration(object_id) and targets and complete:
                self._set_exact((None, object_id), targets)
            for child_index, child in enumerate(node.get("inner", [])):
                self._bind_record_initializer(
                    child,
                    (*path, child_index),
                    object_id,
                    node,
                    parent_kind,
                )
        for child_index, child in enumerate(node.get("inner", [])):
            self._collect_initial_bindings(child, (*path, child_index), node["kind"])

    def _bind_record_initializer(
        self,
        initializer: Any,
        path: tuple[int, ...],
        object_id: str,
        declaration: dict[str, Any],
        parent_kind: str | None,
    ) -> None:
        if not self._is_node(initializer) or initializer["kind"] != ClangNodeKind.INIT_LIST_EXPR:
            return
        children = initializer.get("inner", [])
        selected = initializer.get("field")
        if isinstance(selected, dict) and selected.get("id"):
            fields = [self.declaration_target(selected)]
        else:
            fields = next(
                (
                    self.record_fields_by_type[name.removeprefix("const ")]
                    for name in self._type_names(initializer.get("type"))
                    if name.removeprefix("const ") in self.record_fields_by_type
                ),
                [],
            )
        for field_id, child in zip(fields, children, strict=False):
            targets, complete = self._expression_targets(child)
            closure_holder = self.closure_field_by_declaration.get(field_id)
            if closure_holder is not None:
                self._record_closure_assignment(
                    closure_holder,
                    self._stable_id(path),
                    targets,
                    complete,
                )
            if targets and complete and self._immutable_record_object(declaration, parent_kind):
                key = (object_id, field_id)
                self.declarations[key] = {
                    "node": declaration,
                    "is_global": parent_kind == ClangNodeKind.TRANSLATION_UNIT_DECL,
                }
                self._set_exact(key, targets)
        for child_index, child in enumerate(children):
            self._bind_record_initializer(
                child,
                (*path, child_index),
                object_id,
                declaration,
                parent_kind,
            )

    def _collect_closure_assignments(self, node: Any, path: tuple[int, ...]) -> None:
        if not self._is_node(node):
            return
        children = [child for child in node.get("inner", []) if self._is_node(child)]
        if node["kind"] == ClangNodeKind.BINARY_OPERATOR and children:
            holder = self._closure_member_holder(children[0])
            if holder is not None:
                targets, complete = (
                    self._expression_targets(children[1])
                    if node.get("opcode") == COperator.ASSIGN and len(children) == 2
                    else (set(), False)
                )
                self._record_closure_assignment(
                    holder,
                    self._stable_id(path),
                    targets,
                    complete,
                )
        for child_index, child in enumerate(node.get("inner", [])):
            self._collect_closure_assignments(child, (*path, child_index))

    def _record_closure_assignment(
        self,
        holder_id: str,
        assignment_node_id: str,
        targets: set[str],
        complete: bool,
    ) -> None:
        self.closure_assignments.append(
            {
                "holder_id": holder_id,
                "assignment_node_id": assignment_node_id,
                "status": (
                    PointerTargetStatus.EXACT
                    if complete and bool(targets)
                    else PointerTargetStatus.UNKNOWN
                ),
                "candidate_target_ids": sorted(targets) if complete else [],
            }
        )

    def _invalidate_mutable_or_escaped_bindings(self) -> None:
        for _, node in self._walk(self.root):
            children = node.get("inner", [])
            if (
                node["kind"] == ClangNodeKind.BINARY_OPERATOR
                and node.get("opcode") in ASSIGNMENT_OPERATORS
                and children
            ):
                self._mark_unknown(self._pointer_holders(children[0]))
            if (
                node["kind"] == ClangNodeKind.UNARY_OPERATOR
                and node.get("opcode") in INCREMENT_DECREMENT_OPERATORS
                and children
            ):
                self._mark_unknown(self._pointer_holders(children[0]))
            if (
                node["kind"] == ClangNodeKind.UNARY_OPERATOR
                and node.get("opcode") == COperator.ADDRESS_OF
            ):
                escaped_ids = self._referenced_object_ids(node)
                self._mark_unknown(
                    key for key in self.status if key[1] in escaped_ids or key[0] in escaped_ids
                )
        for key, declaration in self.declarations.items():
            if declaration["is_global"] and not self._externally_immutable(declaration["node"]):
                self._mark_unknown((key,))

    def _pointer_holders(self, node: Any) -> set[PointerBindingKey]:
        holders: set[PointerBindingKey] = set()
        self._collect_pointer_holders(node, holders)
        return holders

    def _collect_pointer_holders(self, value: Any, holders: set[PointerBindingKey]) -> None:
        if not self._is_node(value):
            return
        if value["kind"] == ClangNodeKind.MEMBER_EXPR:
            closure_holder = self._closure_member_holder(value)
            if closure_holder is None:
                return
            member_id = value.get("referencedMemberDecl")
            member_target = self.clang_to_stable.get(str(member_id)) if member_id else None
            children = value.get("inner", [])
            object_id = self._direct_object(children[0]) if children else None
            local_holder = (object_id, member_target) if member_target and object_id else None
            if local_holder and self.status.get(local_holder) is PointerTargetStatus.EXACT:
                holders.add(local_holder)
            else:
                holders.add((None, closure_holder))
            return
        referenced = value.get("referencedDecl")
        if isinstance(referenced, dict) and referenced.get("id"):
            declaration_id = self.declaration_target(referenced)
            if self._is_function_pointer_declaration(declaration_id):
                holders.add((None, declaration_id))
        for child in value.get("inner", []):
            self._collect_pointer_holders(child, holders)

    def _closure_member_holder(self, value: Any) -> str | None:
        if not self._is_node(value) or value["kind"] != ClangNodeKind.MEMBER_EXPR:
            return None
        member_id = value.get("referencedMemberDecl")
        declaration_id = self.clang_to_stable.get(str(member_id)) if member_id else None
        return self.closure_field_by_declaration.get(declaration_id) if declaration_id else None

    def _direct_object(self, value: Any) -> str | None:
        if not self._is_node(value):
            return None
        if value["kind"] == ClangNodeKind.DECL_REF_EXPR:
            referenced = value.get("referencedDecl")
            if isinstance(referenced, dict) and referenced.get("kind") == ClangNodeKind.VAR_DECL:
                return self.declaration_target(referenced)
            return None
        if value["kind"] == ClangNodeKind.MEMBER_EXPR:
            if value.get("isArrow"):
                return None
            children = value.get("inner", [])
            return self._direct_object(children[0]) if children else None
        children = value.get("inner", [])
        return self._direct_object(children[0]) if len(children) == 1 else None

    def _expression_targets_from_children(self, node: dict[str, Any]) -> tuple[set[str], bool]:
        pointer_children = [
            child
            for child in node.get("inner", [])
            if self._is_node(child) and self.is_function_pointer_type(child.get("type"))
        ]
        if len(pointer_children) != 1:
            return set(), False
        return self._expression_targets(pointer_children[0])

    def _expression_targets(self, node: Any) -> tuple[set[str], bool]:
        if not self._is_node(node):
            return set(), False
        referenced = node.get("referencedDecl")
        if isinstance(referenced, dict) and referenced.get("id"):
            if referenced.get("kind") == ClangNodeKind.FUNCTION_DECL:
                return {self.declaration_target(referenced)}, True
            declaration_id = self.declaration_target(referenced)
            if self._is_function_pointer_declaration(declaration_id):
                key = (None, declaration_id)
                return set(self.targets.get(key, set())), self.status.get(
                    key
                ) is PointerTargetStatus.EXACT
        if node["kind"] == ClangNodeKind.MEMBER_EXPR:
            holders = self._pointer_holders(node)
            if not holders:
                return set(), False
            targets = set().union(*(self.targets.get(holder, set()) for holder in holders))
            return targets, all(
                self.status.get(holder) is PointerTargetStatus.EXACT for holder in holders
            )
        children = [child for child in node.get("inner", []) if self._is_node(child)]
        if node["kind"] in {
            ClangNodeKind.CONDITIONAL_OPERATOR,
            ClangNodeKind.BINARY_CONDITIONAL_OPERATOR,
        }:
            branches = children[-2:]
            if len(branches) != 2:
                return set(), False
            alternatives = [self._expression_targets(branch) for branch in branches]
            return (
                set().union(*(targets for targets, _ in alternatives)),
                all(complete and bool(targets) for targets, complete in alternatives),
            )
        if len(children) == 1:
            return self._expression_targets(children[0])
        return set(), False

    def _referenced_object_ids(self, node: Any) -> set[str]:
        result: set[str] = set()
        if not self._is_node(node):
            return result
        referenced = node.get("referencedDecl")
        if isinstance(referenced, dict) and referenced.get("id"):
            result.add(self.declaration_target(referenced))
        for child in node.get("inner", []):
            result.update(self._referenced_object_ids(child))
        return result

    def _is_function_pointer_declaration(self, declaration_id: str) -> bool:
        declaration = self.stable_to_node.get(declaration_id)
        return bool(
            declaration
            and declaration.get("kind")
            in {ClangNodeKind.VAR_DECL, ClangNodeKind.PARM_VAR_DECL, ClangNodeKind.FIELD_DECL}
            and self.is_function_pointer_type(declaration.get("type"))
        )

    @classmethod
    def is_function_pointer_type(cls, type_record: Any) -> bool:
        return any("(*" in name and ")(" in name for name in cls._type_names(type_record))

    @staticmethod
    def _immutable_record_object(declaration: dict[str, Any], parent_kind: str | None) -> bool:
        is_const = any(
            "const" in name.split()
            for name in FunctionPointerAnalysis._type_names(declaration.get("type"))
        )
        if parent_kind == ClangNodeKind.TRANSLATION_UNIT_DECL:
            return declaration.get("storageClass") == ClangStorageClass.STATIC and is_const
        return is_const

    @staticmethod
    def _externally_immutable(declaration: dict[str, Any]) -> bool:
        return declaration.get("storageClass") == ClangStorageClass.STATIC and any(
            "const" in name.split() or "(*const" in name.replace(" ", "")
            for name in FunctionPointerAnalysis._type_names(declaration.get("type"))
        )

    def _set_exact(self, key: PointerBindingKey, target_ids: set[str]) -> None:
        self.targets.setdefault(key, set()).update(target_ids)
        self.status[key] = PointerTargetStatus.EXACT

    def _mark_unknown(self, keys: Iterable[PointerBindingKey]) -> None:
        for key in keys:
            if key in self.status:
                self.status[key] = PointerTargetStatus.UNKNOWN

    def _ordered_bindings(self):
        return sorted(self.targets.items(), key=lambda item: (item[0][0] or "", item[0][1]))

    def _stable_id(self, path: tuple[int, ...]) -> str:
        suffix = ".".join(map(str, path)) if path else "root"
        return f"{self.unit_id}:{suffix}"

    def _walk(self, node: Any, path: tuple[int, ...] = ()):
        if not self._is_node(node):
            return
        yield path, node
        for child_index, child in enumerate(node.get("inner", [])):
            yield from self._walk(child, (*path, child_index))

    @staticmethod
    def _type_names(type_record: Any) -> tuple[str, ...]:
        if not isinstance(type_record, dict):
            return ()
        values: list[str] = []
        for field in ("desugaredQualType", "qualType"):
            value = type_record.get(field)
            if isinstance(value, str) and value not in values:
                values.append(value)
        return tuple(values)

    @staticmethod
    def _is_node(value: Any) -> bool:
        return isinstance(value, dict) and isinstance(value.get("kind"), str)


class ClosureFunctionPointerResolver:
    """Resolve field-based indirect calls once across all frozen translation units."""

    @classmethod
    def resolve(cls, semantic_indexes: list[dict[str, Any]]) -> None:
        definitions = cls._definitions(semantic_indexes)
        bindings = cls._bindings(semantic_indexes, definitions)
        blockers: list[str] = []
        for semantic in semantic_indexes:
            cls._resolve_unit(semantic, bindings, definitions, blockers)
        if blockers:
            raise WorkflowError(
                "structured call targets remain unresolved across source closure: "
                + ", ".join(blockers)
            )

    @staticmethod
    def _definitions(semantic_indexes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        definitions: dict[str, dict[str, Any]] = {}
        for semantic in semantic_indexes:
            unit_id = semantic["unit_id"]
            for identity in semantic["indexes"]["definition_identities"]["functions"]:
                target_id = identity["node_id"]
                record = {"id": target_id, "unit_id": unit_id, **identity}
                if target_id in definitions and definitions[target_id] != record:
                    raise WorkflowError(f"conflicting closure function identity: {target_id}")
                definitions[target_id] = record
        return definitions

    @classmethod
    def _bindings(
        cls,
        semantic_indexes: list[dict[str, Any]],
        definitions: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        declarations: dict[str, list[dict[str, Any]]] = defaultdict(list)
        assignments: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for semantic in semantic_indexes:
            indexes = semantic["indexes"]
            for declaration in indexes["closure_field_declarations"]:
                declarations[declaration["holder_id"]].append(declaration)
            for assignment in indexes["closure_field_assignments"]:
                assignments[assignment["holder_id"]].append(assignment)

        holders_by_declaration: dict[str, set[str]] = defaultdict(set)
        for holder_id, occurrences in declarations.items():
            for occurrence in occurrences:
                identity = dict(occurrence["identity"])
                identity.pop("field_type", None)
                holders_by_declaration[cls._canonical(identity)].add(holder_id)
        conflicted = {
            holder_id
            for holder_ids in holders_by_declaration.values()
            if len(holder_ids) > 1
            for holder_id in holder_ids
        }

        result: dict[str, dict[str, Any]] = {}
        for holder_id, occurrences in declarations.items():
            writes = assignments.get(holder_id, [])
            identities = {
                cls._canonical(declaration["identity"]) for declaration in occurrences
            }
            targets = {
                target
                for write in writes
                for target in write["candidate_target_ids"]
            }
            complete = (
                holder_id not in conflicted
                and bool(writes)
                and len(identities) == 1
                and all(write["status"] == PointerTargetStatus.EXACT for write in writes)
                and targets <= definitions.keys()
            )
            result[holder_id] = {
                "holder_id": holder_id,
                "status": PointerTargetStatus.EXACT if complete else PointerTargetStatus.UNKNOWN,
                "candidate_target_ids": sorted(targets) if complete else [],
                "assignment_node_ids": sorted(
                    write["assignment_node_id"] for write in writes
                ),
            }
        return result

    @classmethod
    def _resolve_unit(
        cls,
        semantic: dict[str, Any],
        bindings: dict[str, dict[str, Any]],
        definitions: dict[str, dict[str, Any]],
        blockers: list[str],
    ) -> None:
        indexes = semantic["indexes"]
        relevant_bindings: dict[str, dict[str, Any]] = {}
        target_ids: set[str] = set()
        for call in indexes["calls"]:
            if call["dispatch"] is not CallDispatch.INDIRECT_UNRESOLVED:
                continue
            call_bindings = [
                bindings[holder["holder_id"]]
                for holder in call["callee_holders"]
                if holder["holder_id"] in bindings
            ]
            if len(call_bindings) != len(call["callee_holders"]) or any(
                binding["status"] is not PointerTargetStatus.EXACT
                for binding in call_bindings
            ):
                blockers.append(call["node_id"])
                continue
            candidates = {
                target
                for binding in call_bindings
                for target in binding["candidate_target_ids"]
            }
            if not candidates:
                blockers.append(call["node_id"])
                continue
            call["dispatch"] = CallDispatch.INDIRECT_RESOLVED
            call["candidate_target_ids"] = sorted(candidates)
            call["target_set_complete"] = True
            call["resolution_bases"] = [
                *call["resolution_bases"],
                CallResolutionBasis.CLOSURE_POINTS_TO,
            ]
            for holder in call["callee_holders"]:
                binding = bindings.get(holder["holder_id"])
                if binding is not None:
                    holder["status"] = binding["status"]
                    relevant_bindings[holder["holder_id"]] = binding
            target_ids.update(candidates)
            semantic["relations"].extend(
                {
                    "kind": RelationKind.INDIRECT_CALL_TARGET,
                    "source": call["node_id"],
                    "target": target,
                }
                for target in sorted(candidates)
            )

        node_ids = {node["id"] for node in semantic["nodes"]}
        indexes["closure_function_pointer_bindings"] = [
            relevant_bindings[key] for key in sorted(relevant_bindings)
        ]
        indexes["closure_targets"] = [
            definitions[target]
            for target in sorted(target_ids - node_ids)
        ]
        cls._update_counts(semantic)

    @staticmethod
    def _update_counts(semantic: dict[str, Any]) -> None:
        calls = semantic["indexes"]["calls"]
        semantic["counts"].update(
            {
                "relations": len(semantic["relations"]),
                "resolved_indirect_calls": sum(
                    call["dispatch"] is CallDispatch.INDIRECT_RESOLVED for call in calls
                ),
                "unresolved_indirect_calls": sum(
                    call["dispatch"] is CallDispatch.INDIRECT_UNRESOLVED for call in calls
                ),
            }
        )

    @staticmethod
    def _canonical(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
