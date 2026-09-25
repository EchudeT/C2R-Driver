#!/usr/bin/env python3
"""Minimal provenance-checked local text/source knowledge-base CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


MANIFEST = Path("knowledge/manifests/materials.jsonl")
INDEX_DIR = Path("knowledge/index")
CHUNKS = INDEX_DIR / "chunks.jsonl"
STATE = INDEX_DIR / "state.json"
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:.+-]*|[0-9]+|[\u3400-\u9fff]")
TEXT_SUFFIXES = {
    ".c", ".h", ".rs", ".md", ".txt", ".rst", ".toml", ".yaml", ".yml",
    ".json", ".jsonl", ".xml", ".html", ".htm", ".ini", ".cfg", ".mk",
}


class KBError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def workspace_path(raw: str) -> Path:
    return Path(raw).expanduser().resolve()


def controlled_path(workspace: Path, relative: str) -> Path:
    candidate = (workspace / relative).resolve()
    if candidate != workspace and workspace not in candidate.parents:
        raise KBError(f"manifest path escapes workspace: {relative}")
    return candidate


def load_manifest(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / MANIFEST
    if not path.is_file():
        raise KBError(f"missing manifest: {path}")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KBError(f"invalid JSON at {path}:{number}: {exc}") from exc
        required = {"id", "domain", "path", "source_url", "revision", "sha256"}
        missing = sorted(required - record.keys())
        if missing:
            raise KBError(f"missing {missing} at {path}:{number}")
        if record["id"] in seen:
            raise KBError(f"duplicate manifest id: {record['id']}")
        seen.add(record["id"])
        records.append(record)
    if not records:
        raise KBError("manifest has no records")
    return records


def verify_records(workspace: Path, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    verified: list[dict[str, Any]] = []
    for record in records:
        path = controlled_path(workspace, str(record["path"]))
        if not path.is_file():
            raise KBError(f"missing controlled file: {record['path']}")
        actual = digest(path)
        expected = str(record["sha256"]).lower()
        if actual != expected:
            raise KBError(f"hash mismatch for {record['path']}: expected {expected}, got {actual}")
        item = dict(record)
        item["sha256"] = actual
        verified.append(item)
    return verified


def manifest_fingerprint(records: Iterable[dict[str, Any]]) -> str:
    canonical = "\n".join(
        json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for record in records
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_text(record: dict[str, Any]) -> bool:
    if record.get("index") is False:
        return False
    if record.get("media_type", "").startswith("text/"):
        return True
    return Path(str(record["path"])).suffix.lower() in TEXT_SUFFIXES


def chunk_id(record_id: str, start: int, end: int) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", record_id).strip("-")
    return f"{safe}-L{start}-L{end}"


def make_chunks(workspace: Path, record: dict[str, Any], line_count: int, overlap: int) -> list[dict[str, Any]]:
    path = controlled_path(workspace, str(record["path"]))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise KBError(f"non-UTF-8 indexable file: {record['path']}") from exc
    output: list[dict[str, Any]] = []
    step = max(1, line_count - overlap)
    for offset in range(0, len(lines), step):
        selected = lines[offset : offset + line_count]
        if not selected:
            break
        start = offset + 1
        end = offset + len(selected)
        chunk = {
            "chunk_id": chunk_id(str(record["id"]), start, end),
            "record_id": record["id"],
            "domain": record["domain"],
            "path": record["path"],
            "line_start": start,
            "line_end": end,
            "revision": record["revision"],
            "source_url": record["source_url"],
            "sha256": record["sha256"],
            "text": "\n".join(selected),
        }
        for optional in ("category", "derived_from", "original_path", "page_map", "authority", "document_version"):
            if optional in record:
                chunk[optional] = record[optional]
        output.append(chunk)
        if end == len(lines):
            break
    return output


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def build(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    records = verify_records(workspace, load_manifest(workspace))
    chunks: list[dict[str, Any]] = []
    for record in sorted(records, key=lambda item: str(item["id"])):
        if is_text(record):
            chunks.extend(make_chunks(workspace, record, args.lines, args.overlap))
    index_dir = workspace / INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)
    chunk_text = "".join(
        json.dumps(chunk, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for chunk in chunks
    )
    (workspace / CHUNKS).write_text(chunk_text, encoding="utf-8")
    state = {
        "schema": 1,
        "manifest_fingerprint": manifest_fingerprint(records),
        "record_count": len(records),
        "indexed_record_count": sum(1 for record in records if is_text(record)),
        "chunk_count": len(chunks),
        "domain_record_counts": dict(sorted(Counter(str(record["domain"]) for record in records).items())),
        "domain_chunk_counts": dict(sorted(Counter(str(chunk["domain"]) for chunk in chunks).items())),
        "chunk_sha256": digest(workspace / CHUNKS),
        "line_count": args.lines,
        "overlap": args.overlap,
    }
    write_json(workspace / STATE, state)
    return {"status": "READY", **state}


def current_state(workspace: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records = verify_records(workspace, load_manifest(workspace))
    state_path = workspace / STATE
    chunks_path = workspace / CHUNKS
    if not state_path.is_file() or not chunks_path.is_file():
        raise KBError("index is missing; run build")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("manifest_fingerprint") != manifest_fingerprint(records):
        raise KBError("index is stale: manifest fingerprint changed")
    if state.get("chunk_sha256") != digest(chunks_path):
        raise KBError("index is corrupt or stale: chunk hash changed")
    return records, state


def status(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    records, state = current_state(workspace)
    return {"status": "READY", "record_count": len(records), **state}


def inventory(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    records, state = current_state(workspace)
    selected = []
    for record in records:
        if args.domain and record["domain"] != args.domain:
            continue
        item = {
            key: record[key]
            for key in (
                "id", "domain", "category", "path", "revision", "source_url", "sha256",
                "authority", "derived_from", "original_path", "page_map",
            )
            if key in record
        }
        selected.append(item)
    selected.sort(key=lambda item: (str(item["domain"]), str(item["path"]), str(item["id"])))
    return {
        "status": "READY",
        "domain": args.domain,
        "count": len(selected),
        "domain_record_counts": state.get("domain_record_counts", {}),
        "records": selected,
    }


def load_chunks(workspace: Path) -> list[dict[str, Any]]:
    current_state(workspace)
    return [json.loads(line) for line in (workspace / CHUNKS).read_text(encoding="utf-8").splitlines() if line]


def tokens(text: str) -> list[str]:
    return [token.lower() for token in TOKEN_RE.findall(text)]


def search(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    query_tokens = tokens(args.query)
    if not query_tokens:
        raise KBError("query has no searchable tokens")
    wanted = Counter(query_tokens)
    phrase = args.query.casefold()
    ranked: list[tuple[float, dict[str, Any]]] = []
    for chunk in load_chunks(workspace):
        if args.domain and chunk["domain"] != args.domain:
            continue
        if args.record_id and chunk["record_id"] != args.record_id:
            continue
        if args.path_prefix and not str(chunk["path"]).startswith(args.path_prefix):
            continue
        text_value = chunk["text"].casefold()
        counts = Counter(tokens(text_value))
        overlap = sum(min(counts[token], count) for token, count in wanted.items())
        if overlap == 0 and phrase not in text_value:
            continue
        coverage = sum(1 for token in wanted if counts[token]) / len(wanted)
        score = overlap + (2.0 * coverage) + (3.0 if phrase in text_value else 0.0)
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
    results = []
    for score, chunk in ranked[: args.limit]:
        result = {
            key: chunk[key]
            for key in (
                "chunk_id", "record_id", "domain", "category", "path",
                "line_start", "line_end", "revision", "source_url", "sha256",
            )
            if key in chunk
        }
        result["score"] = round(score, 6)
        result["summary"] = next(
            (line.strip() for line in chunk["text"].splitlines() if line.strip()),
            "",
        )[:240]
        if args.with_text:
            result["text"] = chunk["text"]
        results.append(result)
    return {"status": "READY", "query": args.query, "count": len(results), "results": results}


def show(args: argparse.Namespace) -> dict[str, Any]:
    workspace = workspace_path(args.workspace)
    for chunk in load_chunks(workspace):
        if chunk["chunk_id"] == args.chunk_id:
            return {"status": "READY", "result": chunk}
    raise KBError(f"unknown chunk id: {args.chunk_id}")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--workspace", required=True, help="absolute or relative project workspace")

    build_parser = commands.add_parser("build", parents=[common])
    build_parser.add_argument("--lines", type=int, default=80)
    build_parser.add_argument("--overlap", type=int, default=20)
    build_parser.set_defaults(handler=build)

    status_parser = commands.add_parser("status", parents=[common])
    status_parser.set_defaults(handler=status)

    inventory_parser = commands.add_parser("inventory", parents=[common])
    inventory_parser.add_argument("--domain")
    inventory_parser.set_defaults(handler=inventory)

    search_parser = commands.add_parser("search", parents=[common])
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--domain")
    search_parser.add_argument("--record-id")
    search_parser.add_argument("--path-prefix")
    search_parser.add_argument("--limit", type=int, default=3)
    search_parser.add_argument(
        "--with-text", action="store_true",
        help="include chunk text; default returns compact evidence locators",
    )
    search_parser.set_defaults(handler=search)

    show_parser = commands.add_parser("show", parents=[common])
    show_parser.add_argument("--chunk-id", required=True)
    show_parser.set_defaults(handler=show)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if getattr(args, "lines", 1) < 1:
            raise KBError("--lines must be positive")
        if getattr(args, "overlap", 0) < 0 or getattr(args, "overlap", 0) >= getattr(args, "lines", 1):
            raise KBError("--overlap must be non-negative and smaller than --lines")
        if getattr(args, "limit", 1) < 1:
            raise KBError("--limit must be positive")
        print(json.dumps(args.handler(args), indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (KBError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
