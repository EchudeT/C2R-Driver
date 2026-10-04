"""Explicit local model indexing. Querying never downloads models or rebuilds indexes."""

import importlib.metadata
import json
import math
import os
import tempfile
from pathlib import Path

from ..core.ledger import canonical_json
from ..core.models import WorkflowError
from .index import file_sha256


def model_identity(path):
    root = Path(path).resolve()
    if not root.is_dir():
        raise WorkflowError("Embedding model must be an existing local directory")
    files = {
        p.relative_to(root).as_posix(): file_sha256(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".cache" not in p.parts
    }
    if not files:
        raise WorkflowError("Embedding model directory is empty")
    return {"path": str(root), "files": files}


def normalized(vector):
    if not vector or any(not math.isfinite(v) for v in vector):
        raise WorkflowError("Embedding contains empty or nonfinite values")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or not norm:
        raise WorkflowError("Embedding has zero norm")
    return [v / norm for v in vector]


class LocalEncoder:
    """Sentence Transformers CPU inference, with explicit windows instead of silent truncation."""

    def __init__(self, path, *, query_prefix="", document_prefix=""):
        self.identity = model_identity(path)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:
            raise WorkflowError(
                "Install driver-port-factory[rag] to use dense retrieval"
            ) from error
        self.model = SentenceTransformer(
            self.identity["path"], device="cpu", local_files_only=True, trust_remote_code=False
        )
        self.query_prefix, self.document_prefix = query_prefix, document_prefix
        self.recipe = {
            "backend": "sentence-transformers-local",
            "version": importlib.metadata.version("sentence-transformers"),
            "runtime_versions": {
                n: importlib.metadata.version(n) for n in ("torch", "transformers", "tokenizers")
            },
            "query_prefix": query_prefix,
            "document_prefix": document_prefix,
            "pooling": "normalized-mean-token-windows-v1",
            "max_seq_length": self.model.max_seq_length,
        }

    def encode(self, texts, *, query=False):
        prefix = self.query_prefix if query else self.document_prefix
        tokenizer = self.model.tokenizer
        reserve = len(tokenizer.encode(prefix, add_special_tokens=False)) + 8
        width = self.model.max_seq_length - reserve
        if width < 16:
            raise WorkflowError("Embedding prefix leaves insufficient model context")
        vectors = []
        for text in texts:
            tokens = tokenizer.encode(text, add_special_tokens=False)
            windows = [
                prefix + tokenizer.decode(tokens[i : i + width])
                for i in range(0, max(1, len(tokens)), width)
            ]
            values = self.model.encode(
                windows, batch_size=32, normalize_embeddings=True, show_progress_bar=False
            ).tolist()
            vectors.append(
                normalized([sum(v[i] for v in values) / len(values) for i in range(len(values[0]))])
            )
        return vectors

    def verify(self):
        if model_identity(self.identity["path"]) != self.identity:
            raise WorkflowError("Embedding model changed during inference")


def location(index):
    return index.workspace / "knowledge/rag" / index.manifest.digest / "dense.json"


def build(index, encoder):
    chunks = index._load_chunks()
    source_hash = file_sha256(index.chunks_path)
    vectors = encoder.encode([c["text"] for c in chunks])
    if len(vectors) != len(chunks) or not vectors:
        raise WorkflowError("Embedding count differs from corpus or corpus is empty")
    vectors = [normalized(v) for v in vectors]
    if len({len(v) for v in vectors}) != 1:
        raise WorkflowError("Embedding dimensions are inconsistent")
    index.status()
    if file_sha256(index.chunks_path) != source_hash:
        raise WorkflowError("Knowledge index changed during vector build")
    encoder.verify()
    body = {
        "format": "dpf-dense-v1",
        "manifest_sha256": index.manifest.digest,
        "chunks_sha256": source_hash,
        "model": encoder.identity,
        "recipe": encoder.recipe,
        "chunk_ids": [c["chunk_id"] for c in chunks],
        "vectors": vectors,
    }
    import hashlib

    body["payload_sha256"] = hashlib.sha256(canonical_json(body).encode()).hexdigest()
    target = location(index)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=target.parent, delete=False) as stream:
        json.dump(body, stream, ensure_ascii=False)
        temporary = stream.name
    os.replace(temporary, target)
    return {
        "status": "READY",
        "path": str(target),
        "chunks": len(chunks),
        "dimensions": len(vectors[0]),
        "model": encoder.identity,
        "recipe": encoder.recipe,
    }


def load(index, chunks):
    import hashlib

    try:
        body = json.loads(location(index).read_text())
        expected = body.pop("payload_sha256")
        if hashlib.sha256(canonical_json(body).encode()).hexdigest() != expected:
            raise WorkflowError("Dense index checksum mismatch; rebuild explicitly")
        if (
            body["format"] != "dpf-dense-v1"
            or body["manifest_sha256"] != index.manifest.digest
            or body["chunks_sha256"] != file_sha256(index.chunks_path)
            or body["chunk_ids"] != [c["chunk_id"] for c in chunks]
        ):
            raise WorkflowError("Dense index is stale; rebuild explicitly")
        if len(body["vectors"]) != len(chunks) or not chunks:
            raise WorkflowError("Dense index row count mismatch")
        if len({len(v) for v in body["vectors"]}) != 1:
            raise WorkflowError("Dense index dimensions differ")
        for vector in body["vectors"]:
            normalized(vector)
        return body
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise WorkflowError(f"Cannot load dense index: {error}") from error


def rank(index, chunks, selected, query, *, encoder=None):
    body = load(index, chunks)
    recipe = body["recipe"]
    encoder = encoder or LocalEncoder(
        body["model"]["path"],
        query_prefix=recipe["query_prefix"],
        document_prefix=recipe["document_prefix"],
    )
    if encoder.identity != body["model"] or encoder.recipe != recipe:
        raise WorkflowError("Embedding model or inference recipe changed; rebuild explicitly")
    vector = normalized(encoder.encode([query], query=True)[0])
    encoder.verify()
    if len(vector) != len(body["vectors"][0]):
        raise WorkflowError("Query embedding dimension mismatch")
    allowed = {c["chunk_id"] for c in selected}
    results = []
    for chunk, stored in zip(chunks, body["vectors"], strict=True):
        if chunk["chunk_id"] in allowed:
            score = sum(a * b for a, b in zip(vector, stored, strict=True))
            results.append((score, chunk))
    return sorted(results, key=lambda pair: (-pair[0], pair[1]["chunk_id"]))


def build_configured(index):
    """Bootstrap/rebuild hook. Model selection is explicit, results bind its exact bytes."""
    state = index.build()
    model_path = os.environ.get("DPF_KB_EMBEDDING_MODEL")
    if model_path:
        encoder = LocalEncoder(
            model_path,
            query_prefix=os.environ.get("DPF_KB_QUERY_PREFIX", ""),
            document_prefix=os.environ.get("DPF_KB_DOCUMENT_PREFIX", ""),
        )
        result = build(index, encoder)
        state["retrieval"] = {
            "mode": "hybrid",
            "dense_path": result["path"],
            "dimensions": result["dimensions"],
            "recipe": result["recipe"],
            "dense_sha256": file_sha256(location(index)),
        }
    elif location(index).exists():
        body = load(index, index._load_chunks())
        if model_identity(body["model"]["path"]) != body["model"]:
            raise WorkflowError("Stored embedding model changed; rebuild with explicit model")
        state["retrieval"] = {
            "mode": "hybrid",
            "dense_path": str(location(index)),
            "dimensions": len(body["vectors"][0]),
            "recipe": body["recipe"],
            "dense_sha256": file_sha256(location(index)),
        }
    else:
        state["retrieval"] = {"mode": "bm25", "dense": "not configured in this build"}
    return state
