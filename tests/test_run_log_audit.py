"""Audit attribution must not double count history or export tool contents."""

import importlib.util
import json
from pathlib import Path

import pytest


def module():
    path = Path(__file__).resolve().parents[1] / "scripts/run-log-audit.py"
    spec = importlib.util.spec_from_file_location("run_log_audit", path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def record(second, kind, **payload):
    return {"timestamp": f"2026-09-26T00:00:{second:02d}Z", "type": "response_item",
            "payload": {"type": kind, **payload}}


def test_audit_attributes_call_outputs_and_excludes_other_turns():
    secret = "local-private-output"
    records = [
        record(1, "custom_tool_call", call_id="old", name="exec", input="old command"),
        record(12, "custom_tool_call", call_id="new", name="exec",
               input='await tools.write_stdin({yield_time_ms:1000});'),
        record(13, "custom_tool_call_output", call_id="new",
               output=[{"text": f"Warning: truncated output\n{secret}"}]),
        record(15, "custom_tool_call", call_id="missing", name="exec", input="new command"),
        record(16, "reasoning", encrypted_content=secret),
        record(17, "custom_tool_call", call_id="long", name="exec",
               input='await tools.write_stdin({"yield_time_ms":30000});'),
        record(18, "custom_tool_call_output", call_id="long", output="finished"),
    ]
    jobs = [{"job_id": "second", "stage": "implementation",
             "started_at": "2026-09-26T00:00:10+00:00",
             "completed_at": "2026-09-26T00:00:20+00:00"},
            {"job_id": "open", "started_at": "2026-09-26T00:00:21+00:00"}]
    rows = module().interaction_audit(records, jobs)
    assert len(rows) == 1
    row = rows[0]
    assert row["tool_calls"] == 3
    assert row["poll_wrappers"] == 2
    assert row["poll_wrappers_requesting_under_5s"] == 1
    assert row["truncated_outputs"] == 1
    assert row["unmatched_outputs"] == 1
    assert row["output_characters"] == len(f"Warning: truncated output\n{secret}finished")
    assert secret not in json.dumps(rows)
    assert "new command" not in json.dumps(rows)


def test_native_interval_uses_same_metrics_and_includes_delayed_output(tmp_path):
    records = [record(1, "custom_tool_call", call_id="old", input="private"),
        record(12, "custom_tool_call", call_id="new", name="exec",
               input="await tools.write_stdin({yield_time_ms:1000});"),
        {"timestamp": "2026-09-26T00:00:15Z", "type": "compacted", "payload": {}},
        record(16, "reasoning", encrypted_content="private"),
        record(25, "custom_tool_call_output", call_id="new", output="private"),
        record(30, "custom_tool_call", call_id="after", input="private")]
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\nnot-json\n")
    audit = module()
    start, end = "2026-09-26T00:00:10Z", "2026-09-26T00:00:20Z"
    result = audit.native_audit(path, start, end)
    expected = audit.interaction_audit(records, [{"job_id": "native_interval", "stage": None,
        "started_at": start, "completed_at": end}])[0]
    assert result["interactions"] == expected
    assert result["compactions"] == 1 and result["invalid_lines"] == 1
    assert "private" not in json.dumps(result)
    with pytest.raises(ValueError, match="ordered timestamps"):
        audit.native_audit(path, end, start)
