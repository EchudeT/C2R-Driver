"""Monitor reads synthetic logs without loading a workflow or invoking a model."""

import json
import sqlite3

from driver_port_factory.control.monitor import EventTail, clean, fit, selected_job, snapshot


def event(text):
    return (
        json.dumps(
            {"type": "item.completed", "item": {"type": "agent_message", "text": text}},
            ensure_ascii=False,
        ).encode()
        + b"\n"
    )


def test_tail_handles_partial_utf8_append_and_rotation(tmp_path):
    path = tmp_path / "events.jsonl"
    encoded = event("中文输出")
    path.write_bytes(encoded[:-4])
    tail = EventTail()
    assert tail.update(path) == []
    with path.open("ab") as stream:
        stream.write(encoded[-4:])
    assert tail.update(path)[-1] == "中文输出"
    assert tail.update(path).count("中文输出") == 1
    replacement = tmp_path / "replacement"
    replacement.write_bytes(event("new call"))
    replacement.replace(path)
    assert "中文输出" not in tail.update(path)
    assert tail.update(path)[-1] == "new call"
    assert tail.update(None) == []


def test_monitor_does_not_create_or_mutate_controller_state(tmp_path):
    control = tmp_path / ".dpf"
    codex = control / "codex"
    codex.mkdir(parents=True)
    database = control / "run.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE stages(name TEXT, status TEXT, position INTEGER)")
        db.execute("INSERT INTO stages VALUES('implementation','RUNNING',0)")
    (codex / "one.metrics.json").write_text(
        json.dumps(
            {"stage": "implementation", "started_at": "2026-01-01", "invocation_state": "RUNNING"}
        )
    )
    (codex / "two.metrics.json").write_text(
        json.dumps({"stage": "analysis", "started_at": "2026-01-02"})
    )
    (codex / "partial.metrics.json").write_text("{")
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    stages, jobs, state = snapshot(tmp_path)
    assert stages == [("implementation", "RUNNING")]
    assert state == "UNTRACKED"
    assert selected_job(jobs)[1]["stage"] == "analysis"
    assert selected_job(jobs, "implementation")[1]["stage"] == "implementation"
    assert before == {
        p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }
    assert "\x1b" not in clean("\x1b[2J malicious log")
    assert fit("中英ab", 5) == "中英a"
