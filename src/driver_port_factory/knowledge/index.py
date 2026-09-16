from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import uuid
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from ..core.project import Project
from .contracts import KnowledgeDomain, KnowledgeIndexStatus
from .corpus import CorpusManifest

INDEX_ROOT = Path("knowledge/indexes")
CHUNKS_FILENAME = "chunks.jsonl"
STATE_FILENAME = "state.json"
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_:.+-]*|[0-9]+|[\u3400-\u9fff]")
TEXT_SUFFIXES = {
    ".c",
    ".h",
    ".rs",
    ".md",
    ".txt",
    ".rst",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".jsonl",
    ".xml",
    ".html",
    ".htm",
    ".ini",
    ".cfg",
    ".mk",
}


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class KnowledgeIndex:
    """Deterministic, provenance-checked index compatible with the upstream KB contract."""

    def __init__(self, workspace: Path, manifest: CorpusManifest) -> None:
        self.workspace = workspace.resolve()
        if not self.workspace.is_dir():
            raise WorkflowError(f"knowledge workspace does not exist: {self.workspace}")
        self.manifest = manifest

    @property
    def index_directory(self) -> Path:
        return self.workspace / INDEX_ROOT / self.manifest.digest

    @property
    def chunks_path(self) -> Path:
        return self.index_directory / CHUNKS_FILENAME

    @property
    def state_path(self) -> Path:
        return self.index_directory / STATE_FILENAME

    @classmethod
    def for_project(cls, project: Project) -> KnowledgeIndex:
        return cls(project.root, CorpusManifest.current(project))

    def controlled_path(self, relative: str) -> Path:
        candidate = (self.workspace / relative).resolve()
        if candidate != self.workspace and self.workspace not in candidate.parents:
            raise WorkflowError(f"manifest path escapes workspace: {relative}")
        return candidate

    def load_manifest(self) -> list[dict[str, Any]]:
        return [record.to_dict() for record in self.manifest.records]

    def verified_records(self) -> list[dict[str, Any]]:
        verified: list[dict[str, Any]] = []
        for record in self.load_manifest():
            path = self.controlled_path(str(record["path"]))
            if not path.is_file():
                raise WorkflowError(f"missing controlled knowledge file: {record['path']}")
            actual = file_sha256(path)
            expected = str(record["sha256"]).lower()
            if actual != expected:
                raise WorkflowError(
                    f"knowledge hash mismatch for {record['path']}: "
                    f"expected {expected}, got {actual}"
                )
            item = dict(record)
            item["sha256"] = actual
            verified.append(item)
        return verified

    @staticmethod
    def _manifest_fingerprint(records: Iterable[dict[str, Any]]) -> str:
        canonical = "\n".join(canonical_json(record) for record in records)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_text(record: dict[str, Any]) -> bool:
        if record.get("index") is False:
            return False
        if str(record.get("media_type", "")).startswith("text/"):
            return True
        return Path(str(record["path"])).suffix.lower() in TEXT_SUFFIXES

    @staticmethod
    def _chunk_id(record_id: str, start: int, end: int) -> str:
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", record_id).strip("-")
        return f"{safe}-L{start}-L{end}"

    def _chunks_for(
        self, record: dict[str, Any], line_count: int, overlap: int
    ) -> list[dict[str, Any]]:
        path = self.controlled_path(str(record["path"]))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise WorkflowError(f"non-UTF-8 indexable file: {record['path']}") from error
        chunks: list[dict[str, Any]] = []
        step = max(1, line_count - overlap)
        for offset in range(0, len(lines), step):
            selected = lines[offset : offset + line_count]
            if not selected:
                break
            start = offset + 1
            end = offset + len(selected)
            chunk = {
                "chunk_id": self._chunk_id(str(record["id"]), start, end),
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
            for optional in (
                "category",
                "derived_from",
                "original_path",
                "page_map",
                "authority",
                "document_version",
            ):
                if optional in record:
                    chunk[optional] = record[optional]
            chunks.append(chunk)
            if end == len(lines):
                break
        return chunks

    def build(self, *, line_count: int = 80, overlap: int = 20) -> dict[str, Any]:
        if line_count < 1 or overlap < 0 or overlap >= line_count:
            raise WorkflowError("invalid knowledge chunk line_count/overlap")
        records = self.verified_records()
        chunks: list[dict[str, Any]] = []
        for record in sorted(records, key=lambda item: str(item["id"])):
            if self._is_text(record):
                chunks.extend(self._chunks_for(record, line_count, overlap))
        index_root = self.workspace / INDEX_ROOT
        index_root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{self.manifest.digest}.", dir=index_root))
        chunks_path = temporary / CHUNKS_FILENAME
        chunks_data = "".join(canonical_json(chunk) + "\n" for chunk in chunks).encode()
        chunks_path.write_bytes(chunks_data)
        state = {
            "schema_version": 1,
            "status": KnowledgeIndexStatus.READY.value,
            "manifest_fingerprint": self._manifest_fingerprint(records),
            "manifest_sha256": self.manifest.digest,
            "index_path": str(self.index_directory.relative_to(self.workspace)),
            "record_count": len(records),
            "indexed_record_count": sum(1 for record in records if self._is_text(record)),
            "chunk_count": len(chunks),
            "domain_record_counts": dict(
                sorted(Counter(str(record["domain"]) for record in records).items())
            ),
            "domain_chunk_counts": dict(
                sorted(Counter(str(chunk["domain"]) for chunk in chunks).items())
            ),
            "chunks_sha256": file_sha256(chunks_path),
            "line_count": line_count,
            "overlap": overlap,
        }
        state_data = (
            json.dumps(state, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        ).encode()
        (temporary / STATE_FILENAME).write_bytes(state_data)
        self._publish_index(temporary, chunks_data, state_data)
        return state

    def _publish_index(self, temporary: Path, chunks_data: bytes, state_data: bytes) -> None:
        destination = self.index_directory
        if destination.exists():
            existing = (
                (destination / CHUNKS_FILENAME).read_bytes()
                if (destination / CHUNKS_FILENAME).is_file()
                else None,
                (destination / STATE_FILENAME).read_bytes()
                if (destination / STATE_FILENAME).is_file()
                else None,
            )
            if existing == (chunks_data, state_data):
                shutil.rmtree(temporary)
                return
            quarantine = destination.with_name(f".{destination.name}.replaced-{uuid.uuid4().hex}")
            destination.rename(quarantine)
        temporary.rename(destination)

    def _current(self) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        records = self.verified_records()
        state_path = self.state_path
        chunks_path = self.chunks_path
        if not state_path.is_file() or not chunks_path.is_file():
            raise WorkflowError("knowledge index is missing; run build")
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise WorkflowError("knowledge index state is invalid") from error
        if state.get("manifest_fingerprint") != self._manifest_fingerprint(records):
            raise WorkflowError("knowledge index is stale: manifest fingerprint changed")
        if state.get("manifest_sha256") != self.manifest.digest:
            raise WorkflowError("knowledge index is stale: corpus artifact changed")
        if state.get("index_path") != str(self.index_directory.relative_to(self.workspace)):
            raise WorkflowError("knowledge index state has a mismatched content-addressed path")
        if state.get("chunks_sha256") != file_sha256(chunks_path):
            raise WorkflowError("knowledge index is corrupt or stale: chunk hash changed")
        return records, state

    def status(self) -> dict[str, Any]:
        records, state = self._current()
        return {
            **state,
            "status": KnowledgeIndexStatus.READY.value,
            "record_count": len(records),
        }

    def inventory(self, *, domain: KnowledgeDomain | None = None) -> dict[str, Any]:
        records, state = self._current()
        selected = []
        for record in records:
            if domain and record["domain"] != domain.value:
                continue
            selected.append(
                {
                    key: record[key]
                    for key in (
                        "id",
                        "domain",
                        "category",
                        "path",
                        "revision",
                        "source_url",
                        "sha256",
                        "authority",
                        "derived_from",
                        "original_path",
                        "page_map",
                    )
                    if key in record
                }
            )
        selected.sort(key=lambda item: (str(item["domain"]), str(item["path"]), str(item["id"])))
        return {
            "status": KnowledgeIndexStatus.READY.value,
            "domain": domain.value if domain else None,
            "count": len(selected),
            "domain_record_counts": state.get("domain_record_counts", {}),
            "records": selected,
        }

    def _load_chunks(self) -> list[dict[str, Any]]:
        self._current()
        return [
            json.loads(line)
            for line in self.chunks_path.read_text(encoding="utf-8").splitlines()
            if line
        ]

    @staticmethod
    def _tokens(text: str) -> list[str]:
        return [token.lower() for token in TOKEN_RE.findall(text)]

    def search(
        self,
        query: str,
        *,
        domain: KnowledgeDomain | None = None,
        record_id: str | None = None,
        path_prefix: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if limit < 1:
            raise WorkflowError("knowledge search limit must be positive")
        query_tokens = self._tokens(query)
        if not query_tokens:
            raise WorkflowError("knowledge query has no searchable tokens")
        wanted = Counter(query_tokens)
        phrase = query.casefold()
        ranked: list[tuple[float, dict[str, Any]]] = []
        for chunk in self._load_chunks():
            if domain and chunk["domain"] != domain.value:
                continue
            if record_id and chunk["record_id"] != record_id:
                continue
            if path_prefix and not str(chunk["path"]).startswith(path_prefix):
                continue
            text = chunk["text"].casefold()
            counts = Counter(self._tokens(text))
            overlap = sum(min(counts[token], count) for token, count in wanted.items())
            if overlap == 0 and phrase not in text:
                continue
            coverage = sum(1 for token in wanted if counts[token]) / len(wanted)
            score = overlap + (2.0 * coverage) + (3.0 if phrase in text else 0.0)
            ranked.append((score, chunk))
        ranked.sort(key=lambda item: (-item[0], item[1]["chunk_id"]))
        results = []
        for score, chunk in ranked[:limit]:
            result = dict(chunk)
            result["score"] = round(score, 6)
            results.append(result)
        return {
            "status": KnowledgeIndexStatus.READY.value,
            "query": query,
            "count": len(results),
            "results": results,
        }

    def show(self, chunk_id: str) -> dict[str, Any]:
        for chunk in self._load_chunks():
            if chunk["chunk_id"] == chunk_id:
                return {"status": KnowledgeIndexStatus.READY.value, "result": chunk}
        raise WorkflowError(f"unknown knowledge chunk id: {chunk_id}")
