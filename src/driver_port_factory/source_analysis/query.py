"""Bounded navigation of frozen C facts, without loading the full AST graph."""

from __future__ import annotations

from collections import Counter
from typing import Any

import ijson

from ..core.models import WorkflowError
from ..core.project import Project
from ..knowledge.index import file_sha256
from .contracts import SourceAnalysisArtifact, SourceAnalysisStage


def query_facts(
    project: Project, *, symbol: str, source_path: str | None = None, limit: int = 5
) -> dict[str, Any]:
    if not symbol or not 1 <= limit <= 20:
        raise WorkflowError("C fact query requires a symbol and limit between 1 and 20")
    facts = project.load_json_artifact(
        SourceAnalysisStage.STRUCTURED_C_ANALYSIS, SourceAnalysisArtifact.STRUCTURED_C_FACTS
    )
    results = []
    count = 0
    for unit in facts["units"]:
        link = unit["semantic_index"]
        path = project.artifacts.path_for_digest(link["sha256"])
        if file_sha256(path) != link["sha256"]:
            raise WorkflowError("C semantic index failed integrity verification")
        with path.open("rb") as stream:
            indexes = next(ijson.items(stream, "indexes"))
        first_result = len(results)
        for category, identities in indexes["definition_identities"].items():
            for identity in identities:
                if identity.get("name") != symbol:
                    continue
                if source_path and not any(
                    item.get("path", "").startswith(source_path)
                    for item in identity.get("closure_source", [])
                ):
                    continue
                count += 1
                if len(results) < limit:
                    results.append(_details(unit, indexes, category, identity, str(path)))
        _fill_callees(path, results[first_result:])
    return {
        "symbol": symbol, "match_count": count, "truncated": count > len(results),
        "results": results,
        "scope": "Structured navigation only; inspect cited originals for behavior and safety.",
    }


def _fill_callees(path, results):
    pending = {
        call["target_id"]
        for result in results for call in result.get("calls", [])
        if call.get("target_id") and not call.get("target_name")
    }
    if not pending:
        return
    names = {}
    with path.open("rb") as stream:
        for node in ijson.items(stream, "nodes.item"):
            if node["id"] in pending:
                names[node["id"]] = node.get("name")
                pending.remove(node["id"])
                if not pending:
                    break
    for result in results:
        for call in result.get("calls", []):
            if not call.get("target_name"):
                call["target_name"] = names.get(call.get("target_id"))


def _details(unit, indexes, category, identity, path):
    node_id = identity["node_id"]
    result = {"unit_id": unit["unit_id"], "category": category,
              "identity": identity, "semantic_index": path}
    if category == "records":
        result["layouts"] = [
            record for record in unit["raw_facts"]["record_layout"]["summary"]["records"]
            if record["ast_node_id"] == node_id
        ]
        return result
    calls = [call for call in indexes["calls"] if call["node_id"].startswith(node_id + ".")]
    names = {
        item["node_id"]: item["name"]
        for item in indexes["definition_identities"]["functions"]
    }
    names.update({item["id"]: item.get("name") for item in indexes["external_declarations"]})
    result.update({
        "call_count": len(calls),
        "calls": [{**call, "target_name": names.get(call.get("target_id"))}
                  for call in calls[:20]],
        "calls_truncated": len(calls) > 20,
        "effect_counts": dict(Counter(
            effect["kind"] for effect in indexes["effects"]
            if effect["node_id"].startswith(node_id + ".")
        )),
        "cfg_gaps": [
            gap for gap in unit["raw_facts"]["cfg"]["summary"].get("unavailable_functions", [])
            if gap["ast_node_id"] == node_id
        ],
    })
    return result
