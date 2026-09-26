"""Local, deduplicated native context snapshots; diagnostic failures never gate work."""

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path

from ..core.models import utc_now
from .accounting import read_jobs

CHUNK_BYTES = 1024 * 1024


def locate_rollout(home: Path, thread: str) -> Path | None:
    if not re.fullmatch(r"[0-9a-fA-F-]{36}", thread):
        return None
    # Exact thread lookup only. Never copy the global database or unrelated conversations.
    for database in sorted(home.glob("state_*.sqlite"), reverse=True):
        try:
            with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=0.2) as db:
                row = db.execute(
                    "SELECT rollout_path FROM threads WHERE id=?", (thread,)).fetchone()
            if row and Path(row[0]).is_file():
                return Path(row[0])
        except sqlite3.Error:
            continue  # Native schemas are version-dependent; try the filename fallback.
    for directory in (home / "sessions", home / "archived_sessions"):
        for path in directory.glob(f"**/*-{thread}.jsonl"):
            if path.is_file():
                return path
    return None


def put_chunk(root, data):
    digest = hashlib.sha256(data).hexdigest()
    path = root / "objects" / digest
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.exists():
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.chmod(0o600)
        temporary.replace(path)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError("context log object failed integrity verification")
    return {"sha256": digest, "bytes": len(data)}


def snapshot(root, source, thread):
    with source.open("rb") as stream:
        first = json.loads(stream.readline())
        meta = first.get("payload", {})
        if (first.get("type") != "session_meta"
                or (meta.get("id") or meta.get("session_id")) != thread):
            raise ValueError("native rollout identity mismatch")
        # Freeze a byte prefix. Concurrent appends belong to the next snapshot.
        size = os.fstat(stream.fileno()).st_size
        stream.seek(0)
        chunks, digest, remaining = [], hashlib.sha256(), size
        while remaining:
            data = stream.read(min(CHUNK_BYTES, remaining))
            if not data:
                raise ValueError("native rollout shortened during capture")
            digest.update(data)
            chunks.append(put_chunk(root, data))
            remaining -= len(data)
    return {"source": str(source), "bytes": size, "sha256": digest.hexdigest(),
            "chunks": chunks, "captured_at": utc_now(),
            "note": "Exact local rollout prefix; last JSONL record may still be incomplete. "
                    "Not a provider request dump or a guarantee of hidden model state."}


class ContextLog:
    def __init__(self, project, job, metrics):
        self.root = project.control / "codex" / "context-logs"
        self.home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
        self.path = self.root / f"{job.job_id}.json"
        self.last_capture = 0.0
        self.source = None
        self.source_thread = None
        self.record = {
            "schema_version": 1, "job_id": job.job_id, "stage": job.stage.value,
            "started_at": metrics["started_at"], "requested_thread": job.thread_id,
            "thread_id": job.thread_id, "model": metrics["model"],
            "context_policy": metrics["context_policy"],
            "context_epoch": metrics["context_epoch"],
            "handoff": metrics["context_handoff"],
            "policy_sha256": metrics["policy_sha256"],
            "auto_compact_token_limit": job.compact_token_limit,
            "usage_baseline": metrics["usage_baseline"],
            "events_path": str(project.control / "codex" /
                               f"{job.stage.value}-{job.job_id}.events.jsonl"),
            "metrics_path": str(project.control / "codex" /
                                f"{job.stage.value}-{job.job_id}.metrics.json"),
            "snapshots": [], "status": "pending_thread",
        }
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.record["prompt"] = put_chunk(self.root, job.prompt.encode())
        self.save()

    def save(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.record, ensure_ascii=False, indent=2) + "\n")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def capture(self, thread, *, force=False):
        if not force and time.monotonic() - self.last_capture < 60:
            return
        self.last_capture = time.monotonic()
        self.record["thread_id"] = thread
        self.record["last_checked_at"] = utc_now()
        self.record.pop("error", None)
        try:
            if not thread:
                self.record["status"] = "pending_thread"
            else:
                if self.source_thread != thread or not self.source or not self.source.is_file():
                    self.source = locate_rollout(self.home, thread)
                    self.source_thread = thread
                if self.source is None:
                    self.record["status"] = "native_unavailable"
                else:
                    value = snapshot(self.root, self.source, thread)
                    prior = self.record["snapshots"]
                    if not prior or prior[-1]["sha256"] != value["sha256"]:
                        prior.append(value)
                    self.record["status"] = "captured"
        except (OSError, ValueError, TypeError, AttributeError) as error:
            self.record.update(status="capture_error", error=f"{type(error).__name__}: {error}")
        self.save()


def verified_chunks(root, entry):
    for chunk in entry["chunks"]:
        digest = chunk["sha256"]
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid context object digest")
        data = (root / "objects" / digest).read_bytes()
        if len(data) != chunk["bytes"] or hashlib.sha256(data).hexdigest() != digest:
            raise ValueError("context object integrity mismatch")
        yield data


def inspect_snapshot(root, entry):
    counts, compacted, pending, invalid, size = {}, 0, b"", 0, 0
    digest = hashlib.sha256()
    for data in verified_chunks(root, entry):
        digest.update(data)
        size += len(data)
        lines = (pending + data).split(b"\n")
        pending = lines.pop()
        for line in lines:
            try:
                record = json.loads(line)
                kind = record.get("type", "unknown")
                counts[kind] = counts.get(kind, 0) + 1
                compacted += kind == "compacted"
            except (ValueError, AttributeError):
                invalid += 1
    if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
        raise ValueError("context snapshot integrity mismatch")
    return {"record_types": counts, "native_compacted_records": compacted,
            "invalid_complete_records": invalid, "trailing_bytes": len(pending),
            "note": "Counts are cumulative within this snapshot, not per-call deltas. "
                    "Only native top-level compacted records are counted; formats may differ."}


def log_report(project):
    root = project.control / "codex" / "context-logs"
    jobs = []
    for path in sorted(root.glob("*.json")):
        try:
            record = json.loads(path.read_text())
            for _ in verified_chunks(root, {"chunks": [record["prompt"]]}):
                pass
            latest = record["snapshots"][-1] if record["snapshots"] else None
            jobs.append({k: record.get(k) for k in (
                "job_id", "stage", "thread_id", "requested_thread", "status", "error")} | {
                "manifest": str(path), "snapshot_count": len(record["snapshots"]),
                "latest": inspect_snapshot(root, latest) if latest else None})
        except (OSError, ValueError, KeyError, TypeError) as error:
            jobs.append({"manifest": str(path), "status": "audit_error", "error": str(error)})
    recorded = read_jobs(root.parent)
    missing = [{"job_id": job.get("job_id"), "stage": job.get("stage"),
                "context_log": job.get("context_log")} for job in recorded
               if not (root / f"{job.get('job_id')}.json").is_file()]
    return {"schema_version": 1, "project": str(project.root), "jobs": jobs,
            "recorded_invocations": len(recorded), "calls_without_manifest": missing,
            "note": "Unavailable native records mean unknown compaction, not zero. "
                    "Raw local records may contain source code and tool output; no credentials "
                    "or environment are intentionally collected. Nothing is uploaded."}


def export_rollout(project, job_id, destination, *, index=-1):
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", job_id):
        raise ValueError("invalid job ID")
    root = project.control / "codex" / "context-logs"
    record = json.loads((root / f"{job_id}.json").read_text())
    if not record["snapshots"]:
        raise ValueError("no native snapshot was captured for this invocation")
    entry = record["snapshots"][index]
    digest, size = hashlib.sha256(), 0
    # Never overwrite an existing export. Remove partial output after an integrity failure.
    with destination.open("xb") as stream:
        try:
            os.chmod(destination, 0o600)
            for data in verified_chunks(root, entry):
                digest.update(data)
                size += len(data)
                stream.write(data)
            if size != entry["bytes"] or digest.hexdigest() != entry["sha256"]:
                raise ValueError("context snapshot integrity mismatch")
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
    return {"path": str(destination), "sha256": entry["sha256"], "bytes": size}
