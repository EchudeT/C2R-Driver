"""Bounded query navigation without discarding distinct evidence provenance."""

import json
import re

from ..core.models import WorkflowError
from .contracts import KnowledgeDomain


def batch_options(queries):
    if not isinstance(queries, list) or not queries:
        raise WorkflowError("batch queries must be a nonempty JSON list")
    result = []
    allowed = {"query", "domain", "record_id", "path_prefix", "limit"}
    for index, query in enumerate(queries):
        if not isinstance(query, dict) or set(query) - allowed:
            raise WorkflowError(f"batch query {index} needs query and optional domain/record_id/path_prefix/limit")
        if not isinstance(query.get("query"), str) or not query["query"].strip():
            raise WorkflowError(f"batch query {index} needs nonempty query text")
        option = dict(query)
        if option.get("domain") is not None:
            try:
                option["domain"] = KnowledgeDomain(option["domain"])
            except (TypeError, ValueError) as error:
                raise WorkflowError(f"batch query {index} has invalid domain") from error
        for key in ("record_id", "path_prefix"):
            if option.get(key) is not None and not isinstance(option[key], str):
                raise WorkflowError(f"batch query {index} {key} must be a string")
        limit = option.get("limit", 10)
        if type(limit) is not int or limit < 1:
            raise WorkflowError(f"batch query {index} limit must be a positive integer")
        result.append(option)
    return result


def shared_results(results):
    evidence, documents, document_ids, queries = {}, {}, {}, []
    hit_fields = {"score", "summary", "summary_line_start", "summary_line_end", "also_indexed_as"}
    chunk_fields = {"chunk_id", "line_start", "line_end", "text"}
    for result in results:
        hits = []
        for row in result["results"]:
            identifier = row["chunk_id"]
            document = {k: v for k, v in row.items() if k not in hit_fields | chunk_fields}
            key = json.dumps(document, sort_keys=True, ensure_ascii=False)
            if key not in document_ids:
                document_ids[key] = f"d{len(documents) + 1}"
                documents[document_ids[key]] = document
            evidence[identifier] = {"document_id": document_ids[key], **{
                k: row[k] for k in chunk_fields - {"chunk_id"} if k in row}}
            hits.append({"chunk_id": identifier, **{k: row[k] for k in hit_fields if k in row}})
        queries.append({**{k: v for k, v in result.items() if k != "results"}, "hits": hits})
    return {"status": "READY", "queries": queries, "evidence": evidence, "documents": documents,
            "note": "Each hit references evidence by chunk_id, then documents by document_id. "
                    "Scores, excerpts and aliases belong "
                    "to that query. Empty results do not prove absence; inspect originals for claims."}


def distinct_matches(ranked):
    """Merge only identical chunk content and metadata apart from record/chunk IDs."""
    groups = {}
    for score, chunk in ranked:
        key = json.dumps({k: v for k, v in chunk.items() if k not in {"record_id", "chunk_id"}},
                         sort_keys=True, ensure_ascii=False)
        if key not in groups:
            groups[key] = (score, dict(chunk))
        else:
            groups[key][1].setdefault("also_indexed_as", []).append({
                "record_id": chunk["record_id"], "chunk_id": chunk["chunk_id"]})
    return list(groups.values())


def match_summary(text, query, tokens, token_re, *, line_start, budget=240):
    """Expose the literal query or a matching token with original line navigation."""
    match = re.search(re.escape(query), text, flags=re.IGNORECASE)
    if match is None:
        wanted = set(tokens)
        match = next((m for m in token_re.finditer(text) if m.group().lower() in wanted), None)
    anchor = match.start() if match else 0
    start = max(0, anchor - 60)
    end = min(len(text), start + budget)
    excerpt = text[start:end]
    return {
        "summary": ("… " if start else "") + " ".join(excerpt.split()) +
                   (" …" if end < len(text) else ""),
        "summary_line_start": line_start + text.count("\n", 0, start),
        "summary_line_end": line_start + text.count("\n", 0, max(start, end - 1)),
    }
