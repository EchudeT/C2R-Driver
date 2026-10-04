"""Replayable target-knowledge probes. Relevance remains an explicit reviewed interpretation."""

import hashlib
import json
from pathlib import Path

from ..core.models import WorkflowError
from .contracts import KnowledgeDomain, RequiredProbeTopic
from .index import KnowledgeIndex, file_sha256

TARGET_TOPICS = tuple(t.value for t in RequiredProbeTopic if t.domain is KnowledgeDomain.TARGET)


def _rows(spec):
    if (
        not isinstance(spec, dict)
        or set(spec) not in ({"probes"}, {"mode", "probes"})
        or not isinstance(spec["probes"], list)
    ):
        raise WorkflowError(
            "Knowledge probe specification requires probes and optional mode=focused"
        )
    focused = "mode" in spec
    if focused and spec["mode"] != "focused":
        raise WorkflowError("Unknown knowledge probe mode")
    rows = spec["probes"]
    if any(not isinstance(r, dict) for r in rows):
        raise WorkflowError("Each knowledge probe must be an object")
    if any(not isinstance(r.get("topic"), str) for r in rows):
        raise WorkflowError("Knowledge probe topic must be a string")
    topics = [r["topic"] for r in rows]
    if focused:
        if len(topics) != len(set(topics)) or set(topics) - set(TARGET_TOPICS):
            raise WorkflowError("Focused probes need unique known target topics")
    elif len(rows) != len(TARGET_TOPICS) or set(topics) != set(TARGET_TOPICS):
        raise WorkflowError(
            "Target knowledge probes must cover each of: " + ", ".join(TARGET_TOPICS)
        )
    return rows


def _evidence(index, row, hits):
    selected = row.get("evidence")
    if not isinstance(selected, list) or not selected:
        raise WorkflowError(
            "Target knowledge probe needs inspected original evidence: " + row["topic"]
        )
    evidence = []
    for citation in selected:
        if not isinstance(citation, dict) or set(citation) != {
            "chunk_id",
            "line_start",
            "line_end",
        }:
            raise WorkflowError("Probe citation needs chunk_id/line_start/line_end")
        if not isinstance(citation["chunk_id"], str):
            raise WorkflowError("Probe chunk_id must be a string")
        chunk = hits.get(citation["chunk_id"])
        if chunk is None:
            raise WorkflowError(
                "Selected evidence was not retrieved; repair corpus/query and repeat"
            )
        start, end = citation["line_start"], citation["line_end"]
        if (
            type(start) is not int
            or type(end) is not int
            or not chunk["line_start"] <= start <= end <= chunk["line_end"]
        ):
            raise WorkflowError("Probe citation is outside retrieved original range")
        original = index.controlled_path(chunk["path"])
        lines = original.read_text(encoding="utf-8").splitlines()
        if file_sha256(original) != chunk["sha256"] or len(lines) < end:
            raise WorkflowError("Probe original changed or citation is unavailable")
        evidence.append(
            {
                **{
                    k: chunk[k]
                    for k in (
                        "chunk_id",
                        "record_id",
                        "domain",
                        "path",
                        "revision",
                        "source_url",
                        "sha256",
                    )
                },
                "line_start": start,
                "line_end": end,
                "text": "\n".join(lines[start - 1 : end]),
            }
        )
    return evidence


def evaluate(index, spec, report_bytes):
    rows = _rows(spec)
    chunks = index._load_chunks()
    source_hash = file_sha256(index.chunks_path)
    observations = []
    for row in rows:
        if set(row) != {"topic", "query", "applicability", "interpretation", "evidence"}:
            raise WorkflowError("Probe needs topic/query/applicability/interpretation/evidence")
        if row["applicability"] not in ("APPLICABLE", "NOT_APPLICABLE"):
            raise WorkflowError("Unknown target requirement is not a successful knowledge probe")
        if not isinstance(row["interpretation"], str) or not row["interpretation"].strip():
            raise WorkflowError(
                "Explain how original evidence supports this topic or its inapplicability"
            )
        if not isinstance(row["query"], str) or not row["query"].strip():
            raise WorkflowError("Knowledge probe needs a concrete query")
        result = index._search_chunks(
            chunks, row["query"], domain=KnowledgeDomain.TARGET, limit=20, compact=False
        )
        evidence = _evidence(index, row, {c["chunk_id"]: c for c in result["results"]})
        observations.append(
            {
                "topic": row["topic"],
                "query": row["query"],
                "applicability": row["applicability"],
                "interpretation": row["interpretation"],
                "evidence": evidence,
            }
        )
    index.status()
    if file_sha256(index.chunks_path) != source_hash:
        raise WorkflowError("Knowledge index changed during target probes")
    return {
        "schema_version": 1,
        "status": "RETRIEVAL_VALIDATED" if rows else "NO_RETRIEVAL_REQUESTED",
        "semantic_status": "SEMANTICS_NOT_MECHANICALLY_VERIFIED",
        "manifest_sha256": index.manifest.digest,
        "chunks_sha256": source_hash,
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "specification": spec,
        "observations": observations,
        "limits": "Actual query and original-byte checks; does not prove model reading, "
        "relevance, complete API coverage or correctness of NOT_APPLICABLE.",
    }


def read_specification(report):
    spec_path = report.with_suffix(".probes.json")
    if not spec_path.exists():
        return {"mode": "focused", "probes": []}
    try:
        spec = json.loads(spec_path.read_text())
    except (OSError, ValueError) as error:
        raise WorkflowError(
            "Target study requires " + str(spec_path) + ": " + str(error)
        ) from error
    rows = _rows(spec)
    from ..short_refs import active

    refs = active()
    for row in rows:
        if not isinstance(row.get("evidence"), list):
            raise WorkflowError("Target knowledge probe evidence must be a list")
        for citation in row["evidence"]:
            if not isinstance(citation, dict) or "evidence" not in citation:
                continue
            if refs is None or set(citation) != {"evidence", "line_start", "line_end"}:
                raise WorkflowError("Short probe evidence requires its original worker project")
            bound = refs.get(citation["evidence"], "provenance")
            from ..composition import open_project

            index = KnowledgeIndex.for_project(
                open_project(refs.root, read_only=True, verify_artifacts=False)
            )
            current = index.show(bound.get("chunk_id"))["result"]
            if any(current.get(k) != bound.get(k) for k in ("sha256", "path", "revision")):
                raise WorkflowError("Probe evidence reference is stale; query current corpus")
            citation["chunk_id"] = bound["chunk_id"]
            del citation["evidence"]
    return spec


def for_report(project, report):
    return evaluate(
        KnowledgeIndex.for_project(project), read_specification(report), report.read_bytes()
    )


def command(args):
    from ..composition import open_project

    project = open_project(Path(args.path), read_only=True, verify_artifacts=False)
    print(json.dumps(for_report(project, Path(args.report)), ensure_ascii=False, indent=2))


def register(commands):
    parser = commands.add_parser(
        "check-probes", help="replay target queries and inspect cited originals"
    )
    parser.add_argument("path")
    parser.add_argument("--report", required=True)
    parser.set_defaults(handler=command)
