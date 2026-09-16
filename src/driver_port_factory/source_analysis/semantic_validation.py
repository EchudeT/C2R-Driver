from __future__ import annotations

from collections import Counter
from types import MappingProxyType
from typing import Any

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object
from .contracts import SourceAnalysisArtifact
from .semantic_model import (
    CallDispatch,
    CallResolutionBasis,
    PointerTargetStatus,
    RelationKind,
)


def _semantic_index(data: bytes) -> None:
    validate_semantic_index(
        json_object(data, SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX.value)
    )


def validate_semantic_index(value: dict[str, Any]) -> None:
    nodes = value.get("nodes")
    relations = value.get("relations")
    indexes = value.get("indexes")
    if value.get("schema_version") != 1:
        raise WorkflowError("structured semantic index must be schema_version=1")
    if not isinstance(value.get("unit_id"), str) or not value["unit_id"]:
        raise WorkflowError("structured semantic index requires a unit identity")
    if not isinstance(value.get("source_path"), str) or not value["source_path"]:
        raise WorkflowError("structured semantic index requires a source path")
    if not isinstance(nodes, list) or not nodes:
        raise WorkflowError("structured semantic index requires non-empty graph nodes")
    if not isinstance(relations, list) or not isinstance(indexes, dict):
        raise WorkflowError("structured semantic index requires relations and indexes")
    node_ids = _node_ids(nodes)
    relation_edges = _relation_edges(relations, node_ids)
    calls, dispatches = _calls(indexes, node_ids)
    _validate_external_targets(indexes, relation_edges, node_ids)
    _validate_counts(value.get("counts"), nodes, relations, indexes, dispatches)
    _validate_indirect_calls(calls, dispatches, relation_edges)
    _validate_bindings(indexes, relation_edges)


def _node_ids(nodes: list[Any]) -> set[str]:
    identifiers = [node.get("id") for node in nodes if isinstance(node, dict)]
    if len(identifiers) != len(nodes) or any(not isinstance(item, str) for item in identifiers):
        raise WorkflowError("structured semantic graph contains an invalid node")
    if len(set(identifiers)) != len(identifiers):
        raise WorkflowError("structured semantic graph contains duplicate node identities")
    if not any("loc" in node or "range" in node for node in nodes):
        raise WorkflowError("structured semantic graph has no source-spanned nodes")
    return set(identifiers)


def _relation_edges(relations: list[Any], node_ids: set[str]) -> set[tuple[RelationKind, str, str]]:
    try:
        edges = {
            (RelationKind(relation["kind"]), relation["source"], relation["target"])
            for relation in relations
            if isinstance(relation, dict)
        }
    except (KeyError, TypeError, ValueError) as error:
        raise WorkflowError("structured semantic graph has an invalid relation") from error
    if len(edges) != len(relations):
        raise WorkflowError("structured semantic graph has duplicate or invalid relations")
    if any(source not in node_ids for _, source, _ in edges):
        raise WorkflowError("structured semantic relation source is not a graph node")
    return edges


def _calls(
    indexes: dict[str, Any], node_ids: set[str]
) -> tuple[list[dict[str, Any]], list[CallDispatch]]:
    calls = indexes.get("calls")
    if not isinstance(calls, list) or not all(isinstance(call, dict) for call in calls):
        raise WorkflowError("structured semantic call index is invalid")
    try:
        dispatches = [CallDispatch(call["dispatch"]) for call in calls]
    except (KeyError, TypeError, ValueError) as error:
        raise WorkflowError("structured semantic call dispatch is invalid") from error
    if any(call.get("node_id") not in node_ids for call in calls):
        raise WorkflowError("structured semantic call is not bound to a graph node")
    return calls, dispatches


def _validate_external_targets(
    indexes: dict[str, Any],
    relations: set[tuple[RelationKind, str, str]],
    node_ids: set[str],
) -> None:
    records = indexes.get("external_declarations")
    closure_targets = indexes.get("closure_targets")
    if (
        not isinstance(records, list)
        or not all(isinstance(item, dict) for item in records)
        or not isinstance(closure_targets, list)
        or not all(isinstance(item, dict) for item in closure_targets)
    ):
        raise WorkflowError("structured semantic external declarations are invalid")
    external_ids = [record.get("id") for record in records]
    closure_ids = [record.get("id") for record in closure_targets]
    if (
        any(not isinstance(identifier, str) for identifier in external_ids)
        or len(external_ids) != len(set(external_ids))
        or any(not identifier.startswith("external-decl:") for identifier in external_ids)
    ):
        raise WorkflowError("structured semantic external declaration identities are invalid")
    unresolved_targets = {target for _, _, target in relations if target not in node_ids}
    if (
        any(not isinstance(identifier, str) for identifier in closure_ids)
        or len(closure_ids) != len(set(closure_ids))
        or unresolved_targets != set(external_ids) | set(closure_ids)
    ):
        raise WorkflowError("structured semantic relation targets lack external declarations")


def _validate_counts(
    counts: Any,
    nodes: list[dict[str, Any]],
    relations: list[dict[str, Any]],
    indexes: dict[str, Any],
    dispatches: list[CallDispatch],
) -> None:
    list_indexes = (
        "functions",
        "function_definitions",
        "record_definitions",
        "globals",
        "calls",
        "control_flow",
        "effects",
        "external_declarations",
    )
    if not isinstance(counts, dict) or any(
        not isinstance(indexes.get(name), list) for name in list_indexes
    ):
        raise WorkflowError("structured semantic counts or indexes are invalid")
    dispatch_counts = Counter(dispatches)
    expected = {
        "nodes": len(nodes),
        "relations": len(relations),
        "functions": len(indexes["functions"]),
        "function_definitions": len(indexes["function_definitions"]),
        "record_definitions": len(indexes["record_definitions"]),
        "globals": len(indexes["globals"]),
        "calls": len(indexes["calls"]),
        "direct_calls": dispatch_counts[CallDispatch.DIRECT],
        "indirect_calls": len(dispatches) - dispatch_counts[CallDispatch.DIRECT],
        "resolved_indirect_calls": dispatch_counts[CallDispatch.INDIRECT_RESOLVED],
        "unresolved_indirect_calls": dispatch_counts[CallDispatch.INDIRECT_UNRESOLVED],
        "control_flow": len(indexes["control_flow"]),
        "effects": len(indexes["effects"]),
        "source_spans": sum("loc" in node or "range" in node for node in nodes),
        "external_declarations": len(indexes["external_declarations"]),
    }
    if counts != expected:
        raise WorkflowError("structured semantic counts do not match graph indexes")


def _validate_indirect_calls(
    calls: list[dict[str, Any]],
    dispatches: list[CallDispatch],
    relation_edges: set[tuple[RelationKind, str, str]],
) -> None:
    indirect = [
        (call, dispatch)
        for call, dispatch in zip(calls, dispatches, strict=True)
        if dispatch is not CallDispatch.DIRECT
    ]
    for call, dispatch in indirect:
        targets = call.get("candidate_target_ids")
        complete = call.get("target_set_complete")
        if dispatch is CallDispatch.INDIRECT_RESOLVED and (not targets or complete is not True):
            raise WorkflowError("resolved indirect call lacks a complete target set")
        if dispatch is CallDispatch.INDIRECT_UNRESOLVED and complete is not False:
            raise WorkflowError("unresolved indirect call must declare an incomplete target set")
        try:
            [CallResolutionBasis(basis) for basis in call["resolution_bases"]]
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("indirect call has invalid resolution bases") from error
        if dispatch is CallDispatch.INDIRECT_RESOLVED and any(
            (RelationKind.INDIRECT_CALL_TARGET, call["node_id"], target) not in relation_edges
            for target in targets
        ):
            raise WorkflowError("resolved indirect call lacks exact target relations")


def _validate_bindings(
    indexes: dict[str, Any],
    relation_edges: set[tuple[RelationKind, str, str]],
) -> None:
    bindings = indexes.get("function_pointer_bindings")
    if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
        raise WorkflowError("structured semantic index lacks function-pointer bindings")
    for binding in bindings:
        try:
            status = PointerTargetStatus(binding["status"])
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("function-pointer binding has an invalid status") from error
        if status is not PointerTargetStatus.EXACT:
            continue
        holder = binding.get("holder_id")
        targets = binding.get("candidate_target_ids")
        if not holder or not targets:
            raise WorkflowError("exact function-pointer binding lacks holder or targets")
        if any(
            (RelationKind.FUNCTION_POINTER_TARGET, holder, target) not in relation_edges
            for target in targets
        ):
            raise WorkflowError("exact function-pointer binding lacks target relations")


VALIDATORS = MappingProxyType[SourceAnalysisArtifact, ArtifactValidator](
    {SourceAnalysisArtifact.STRUCTURED_C_SEMANTIC_INDEX: _semantic_index}
)
