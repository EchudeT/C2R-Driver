"""The paid pilot must not retry an uncertain call or exceed its reserved budget."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import patch

import pytest

SPEC = importlib.util.spec_from_file_location(
    "context_pilot", Path(__file__).resolve().parents[1] / "scripts/context-pilot.py")
pilot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pilot)


def test_budget_rejection_precedes_provider_access(tmp_path):
    (tmp_path / "protocol.json").write_text("{}")
    payload = {"model": "gpt-5.6-sol", "max_output_tokens": 4096, "input": "test"}
    with (patch.object(pilot, "payload_for", return_value=payload),
          patch.object(pilot, "connection") as connect,
          pytest.raises(ValueError, match="budget")):
        pilot.run_call(tmp_path, {}, 0, "factual_handoff", budget=0.01)
    connect.assert_not_called()


def test_uncertain_stream_retains_reservation_without_retry(tmp_path):
    (tmp_path / "protocol.json").write_text("{}")
    payload = {"model": "gpt-5.6-sol", "max_output_tokens": 4096, "input": "test"}
    with (patch.object(pilot, "payload_for", return_value=payload),
          patch.object(pilot, "connection", return_value=("unused", "unused", "fixture")),
          patch.object(pilot, "request_response", side_effect=TimeoutError) as request):
        with pytest.raises(TimeoutError):
            pilot.run_call(tmp_path, {}, 0, "factual_handoff", budget=20)
        pilot.run_call(tmp_path, {}, 0, "factual_handoff", budget=20)
    assert request.call_count == 1
    record = json.loads(next(tmp_path.glob("*.metrics.json")).read_text())
    assert record["reserved_usd"] > 0 and record["estimate"] is None
    assert record["state"] == "FAILED"


def test_streamed_answer_survives_empty_terminal_output(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in [
        {"type": "response.output_text.delta", "delta": "hello "},
        {"type": "response.output_text.delta", "delta": "world"},
        {"type": "response.completed", "response": {"output": []}},
    ]))
    assert pilot.response_text(path) == "hello world"
