#!/usr/bin/env python3
"""Explicit local embedding development probe; no LLM call or driver validation.

Reads only Git blobs at the selected revision, never another experiment's worktree.
"""

import argparse
import hashlib
import json
import subprocess
import time
from pathlib import Path

from driver_port_factory.acquisition.facets import MaterialRedistribution, parse_facet
from driver_port_factory.acquisition.material import GitBlobOrigin, MaterialRecord
from driver_port_factory.acquisition.repository_role import RepositoryRole
from driver_port_factory.knowledge import embeddings, rag
from driver_port_factory.knowledge.corpus import CorpusManifest
from driver_port_factory.knowledge.index import KnowledgeIndex

# Fixed development probes. File-level recall is deliberately not semantic correctness.
PROBES = [
    ("try_lock", "ostd/src/sync/spin.rs"),
    ("nonblocking acquisition of a mutual exclusion guard", "ostd/src/sync/spin.rs"),
    ("prevent local interrupts while accessing shared state", "ostd/src/sync/spin.rs"),
    ("register a driver and probe existing PCI devices", "kernel/core/comps/pci/src/bus.rs"),
    ("wait until a condition is true and wake sleeping tasks", "ostd/src/sync/wait.rs"),
    ("share immutable readers while replacing a pointer safely", "ostd/src/sync/rcu/mod.rs"),
]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def prepare(args):
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    revision = git(args.repository, "rev-parse", args.revision + "^{commit}").decode().strip()
    paths = (
        git(
            args.repository,
            "ls-tree",
            "-r",
            "--name-only",
            revision,
            "ostd/src/sync",
            "kernel/core/comps/pci",
        )
        .decode()
        .splitlines()
    )
    records = []
    for number, name in enumerate(p for p in paths if p.endswith(".rs")):
        data = git(args.repository, "show", revision + ":" + name)
        path = root / "raw" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        blob = git(args.repository, "rev-parse", revision + ":" + name).decode().strip()
        records.append(
            MaterialRecord(
                f"target-{number}",
                parse_facet("target", "api_definitions_and_calls"),
                str(path.relative_to(root)),
                "https://github.com/asterinas/asterinas",
                revision,
                "development-probe",
                "Preserve upstream notices; see original file",
                MaterialRedistribution.UNKNOWN,
                hashlib.sha256(data).hexdigest(),
                len(data),
                "text/plain",
                True,
                True,
                GitBlobOrigin(RepositoryRole.TARGET, revision, blob, name),
            )
        )
    (root / "probes.json").write_text(json.dumps(PROBES, indent=2) + "\n")
    index = KnowledgeIndex(root, CorpusManifest.candidate(tuple(records), parent_digest=revision))
    index.build()
    return root, index, revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--model-path", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root, index, revision = prepare(args)
    encoder = embeddings.LocalEncoder(args.model_path)
    vector_status = embeddings.build(index, encoder)
    rows = []
    for number, (question, expected) in enumerate(PROBES):
        row = {"question": question, "expected_file": expected, "modes": {}}
        for mode in ("bm25", "hybrid"):
            begin = time.monotonic()
            packet = rag.query(index, question, mode=mode, encoder=encoder, limit=5)
            (root / f"{number}-{mode}.json").write_text(rag.encode(packet) + "\n")
            row["modes"][mode] = {
                "paths": [e["path"] for e in packet["evidence"]],
                "file_hit_at_5": any(e["path"] == "raw/" + expected for e in packet["evidence"]),
                "packet_bytes": len(rag.encode(packet).encode()),
                "seconds": time.monotonic() - begin,
            }
        rows.append(row)
    result = {
        "scope": "six known development queries, file recall only; no held-out or driver claim",
        "revision": revision,
        "vector_status": vector_status,
        "queries": rows,
        "seconds": time.monotonic() - started,
    }
    (root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "seconds": result["seconds"],
                "chunks": vector_status["chunks"],
                "file_hits_at_5": {
                    m: sum(r["modes"][m]["file_hit_at_5"] for r in rows) for m in ("bm25", "hybrid")
                },
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
