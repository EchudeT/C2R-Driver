"""Offline RAG integration and provenance boundaries. Fake vectors test mechanics only."""

import hashlib
import json
from typing import ClassVar

import pytest

from driver_port_factory.acquisition.facets import MaterialRedistribution, parse_facet
from driver_port_factory.acquisition.material import GitBlobOrigin, MaterialRecord
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.core.models import WorkflowError
from driver_port_factory.knowledge import embeddings, rag
from driver_port_factory.knowledge.contracts import KnowledgeDomain
from driver_port_factory.knowledge.corpus import CorpusManifest
from driver_port_factory.knowledge.index import KnowledgeIndex
from driver_port_factory.knowledge.ranking import bm25


def make_index(root):
    records = []
    texts = {
        "lock.rs": "/// Obtains ownership immediately or returns None when held.\n"
        "pub fn try_lock(&self) -> Option<Guard> {}\n",
        "irq.rs": "/// Release an interrupt callback after waiting for outstanding invocations.\n",
        "noise.rs": "/// framebuffer colors and pixel formats\n",
    }
    for name, text in texts.items():
        data = text.encode()
        (root / name).write_bytes(data)
        records.append(
            MaterialRecord(
                name,
                parse_facet("target", "api_definitions_and_calls"),
                name,
                "https://example.invalid/tree/" + name,
                "a" * 40,
                "2026-10-02",
                "fixture",
                MaterialRedistribution.UNKNOWN,
                hashlib.sha256(data).hexdigest(),
                len(data),
                "text/plain",
                True,
                True,
                GitBlobOrigin(RepositoryRole.TARGET, "a" * 40, "b" * 40, name),
            )
        )
    manifest = CorpusManifest.candidate(tuple(records), parent_digest="fixture")
    index = KnowledgeIndex(root, manifest)
    index.build()
    return index


class Encoder:
    identity: ClassVar[dict] = {"path": "fixture-only", "files": {"fake": "v1"}}
    recipe: ClassVar[dict] = {
        "backend": "synthetic-test",
        "query_prefix": "",
        "document_prefix": "",
    }

    def encode(self, texts, *, query=False):
        if query:
            return [[1.0, 0.0] for _ in texts]
        return [[1.0, 0.0] if "ownership" in t else [0.0, 1.0] for t in texts]

    def verify(self):
        pass


def test_bm25_finds_split_symbols_and_downweights_repeated_common_words():
    rows = [
        {"chunk_id": "a", "text": "pub fn try_lock() {}"},
        {"chunk_id": "b", "text": "pub fn lock() {} lock lock lock"},
    ]
    assert bm25(rows, "try lock")[0][1]["chunk_id"] == "a"
    assert bm25([{"chunk_id": "c", "text": "DmaStreamMap"}], "stream map")


def test_hybrid_recovers_zero_overlap_and_cites_exact_original(tmp_path):
    index = make_index(tmp_path)
    assert rag.query(index, "nonblocking acquisition", mode="bm25")["status"] == "NO_MATCH"
    embeddings.build(index, Encoder())
    result = rag.query(index, "nonblocking acquisition", encoder=Encoder(), limit=1)
    assert result["retrieval"]["mode"] == "hybrid"
    item = result["evidence"][0]
    assert item["path"] == "lock.rs" and item["channels"] == ["dense"]
    original = (tmp_path / item["path"]).read_text().splitlines()
    assert item["text"] == "\n".join(original[item["line_start"] - 1 : item["line_end"]])
    assert item["sha256"] == hashlib.sha256((tmp_path / "lock.rs").read_bytes()).hexdigest()
    assert len(rag.encode(result).encode()) <= result["budget_bytes"]
    assert (
        rag.query(
            index, "nonblocking acquisition", encoder=Encoder(), domain=KnowledgeDomain.SOURCE
        )["status"]
        == "NO_MATCH"
    )


def test_stale_source_corrupt_vectors_and_changed_model_never_fallback(tmp_path):
    index = make_index(tmp_path)
    embeddings.build(index, Encoder())

    class Changed(Encoder):
        identity: ClassVar[dict] = {"path": "fixture-only", "files": {"fake": "v2"}}

    with pytest.raises(WorkflowError, match="model or inference recipe"):
        rag.query(index, "lock", encoder=Changed())
    path = embeddings.location(index)
    body = json.loads(path.read_text())
    body["vectors"][0][0] = 42
    path.write_text(json.dumps(body))
    with pytest.raises(WorkflowError, match="checksum"):
        rag.query(index, "lock", encoder=Encoder())
    embeddings.build(index, Encoder())
    index.build(line_count=1, overlap=0)
    with pytest.raises(WorkflowError, match="stale"):
        rag.query(index, "lock", encoder=Encoder())
    (tmp_path / "lock.rs").write_text("changed")
    with pytest.raises(WorkflowError, match="hash mismatch"):
        rag.query(index, "lock", mode="bm25")


def test_missing_dense_is_explicit_and_budget_omission_is_not_no_match(tmp_path):
    index = make_index(tmp_path)
    with pytest.raises(WorkflowError, match="Cannot load dense"):
        rag.query(index, "lock", mode="hybrid")
    result = rag.query(index, "try_lock", budget=1000)
    assert result["status"] == "BUDGET_OMITTED"
    assert len(rag.encode(result).encode()) <= 1000
    assert rag.query(index, "try_lock")["retrieval"]["mode"] == "bm25"


def test_generation_contract_and_worker_tool_expose_rag():
    from pathlib import Path
    from types import SimpleNamespace

    from driver_port_factory.codex.optional_tools import context
    from driver_port_factory.knowledge.validation import _query_contract

    result = context(
        SimpleNamespace(root=Path("/fixture")), SimpleNamespace(value="driver_implementation")
    )
    assert result["knowledge_rag"][-3:] == ["knowledge", "rag", "/fixture"]
    _query_contract(
        json.dumps(
            {
                "commands": {k: k for k in ("status", "rebuild", "search", "show", "rag")},
                "template_sha256": "x",
                "manifest_sha256": "y",
            }
        ).encode()
    )


def test_bootstrap_builds_configured_embeddings_and_preserves_failure(tmp_path, monkeypatch):
    index = make_index(tmp_path)
    monkeypatch.setenv("DPF_KB_EMBEDDING_MODEL", "/fixture-model")
    monkeypatch.setattr(embeddings, "LocalEncoder", lambda *args, **kwargs: Encoder())
    state = embeddings.build_configured(index)
    assert state["retrieval"]["mode"] == "hybrid"
    assert rag.query(index, "nonblocking acquisition", encoder=Encoder())["selected"]
    monkeypatch.delenv("DPF_KB_EMBEDDING_MODEL")
    monkeypatch.setattr(embeddings, "model_identity", lambda _: Encoder.identity)
    assert embeddings.build_configured(index)["retrieval"]["mode"] == "hybrid"
    monkeypatch.setattr(embeddings, "model_identity", lambda _: {"changed": True})
    with pytest.raises(WorkflowError, match="model changed"):
        embeddings.build_configured(index)
