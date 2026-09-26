import json
import sqlite3
from types import SimpleNamespace

import pytest

from driver_port_factory.codex.context_logs import (
    ContextLog,
    export_rollout,
    inspect_snapshot,
    locate_rollout,
    log_report,
)

THREAD = "01a0d93e-26ae-7713-9d5c-336f23490a89"


def setup_log(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    project = SimpleNamespace(control=tmp_path / "project/.dpf", root=tmp_path / "project")
    job = SimpleNamespace(job_id="test-job", stage=SimpleNamespace(value="implementation"),
                          prompt="exact prompt\n中文", thread_id=None, compact_token_limit=160000)
    metrics = dict.fromkeys(("started_at", "model", "context_policy", "context_epoch",
                             "context_handoff", "policy_sha256", "usage_baseline"))
    logger = ContextLog(project, job, metrics)
    source = home / "sessions/2026/09/26" / f"rollout-test-{THREAD}.jsonl"
    source.parent.mkdir(parents=True)
    source.write_text(json.dumps({"type": "session_meta", "payload": {"id": THREAD}}) + "\n")
    return project, logger, source, home


def test_context_snapshot_survives_source_loss_and_export_is_exact(tmp_path, monkeypatch):
    project, logger, source, _ = setup_log(tmp_path, monkeypatch)
    logger.capture(THREAD, force=True)
    with source.open("a") as stream:
        stream.write(json.dumps({"type": "compacted", "payload": {"message": "summary"}}) + "\n")
        stream.write('{"partial":')
    original = source.read_bytes()
    logger.capture(THREAD, force=True)
    assert len(logger.record["snapshots"]) == 2
    logger.capture(THREAD, force=True)
    assert len(logger.record["snapshots"]) == 2
    source.unlink()
    logger.capture(THREAD, force=True)
    assert logger.record["status"] == "native_unavailable"
    report = log_report(project)
    assert report["jobs"][0]["latest"]["native_compacted_records"] == 1
    assert report["jobs"][0]["latest"]["trailing_bytes"] > 0
    destination = tmp_path / "export.jsonl"
    export_rollout(project, "test-job", destination)
    assert destination.read_bytes() == original
    with pytest.raises(FileExistsError):
        export_rollout(project, "test-job", destination)


def test_chunks_reuse_unchanged_prefix_and_detect_corruption(tmp_path, monkeypatch):
    project, logger, source, _ = setup_log(tmp_path, monkeypatch)
    monkeypatch.setattr("driver_port_factory.codex.context_logs.CHUNK_BYTES", 64)
    logger.capture(THREAD, force=True)
    first = logger.record["snapshots"][-1]
    with source.open("a") as stream:
        stream.write('{"type":"response_item"}\n')
    logger.capture(THREAD, force=True)
    latest = logger.record["snapshots"][-1]
    assert first["chunks"][0] == latest["chunks"][0]
    (logger.root / "objects" / latest["chunks"][0]["sha256"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        inspect_snapshot(logger.root, latest)
    output = tmp_path / "invalid-export"
    with pytest.raises(ValueError, match="integrity"):
        export_rollout(project, "test-job", output)
    assert not output.exists()


def test_exact_identity_lookup_and_unknown_capture(tmp_path, monkeypatch):
    project, logger, source, home = setup_log(tmp_path, monkeypatch)
    with sqlite3.connect(home / "state_5.sqlite") as db:
        db.execute("CREATE TABLE threads(id TEXT,rollout_path TEXT)")
        db.execute("INSERT INTO threads VALUES (?,?)", (THREAD, str(source)))
    assert locate_rollout(home, THREAD) == source
    assert locate_rollout(home, "../../private") is None
    source.write_text('{"type":"session_meta","payload":{"id":"someone-else"}}\n')
    logger.capture(THREAD, force=True)
    assert logger.record["status"] == "capture_error"
    assert not logger.record["snapshots"]
    assert log_report(project)["jobs"][0]["latest"] is None
