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


def test_actual_limit_violation_takes_precedence_over_missing_echo():
    payload = {"model": "gpt-5.6-sol", "max_output_tokens": 64}
    final = {"model": "gpt-5.6-sol", "max_output_tokens": None,
             "usage": {"output_tokens": 403}}
    assert pilot.response_stop_reason(final, payload) == "provider_exceeded_output_cap"
    final["usage"]["output_tokens"] = 32
    assert pilot.response_stop_reason(final, payload) == "provider_did_not_confirm_output_cap"
    final["max_output_tokens"] = 64
    assert pilot.response_stop_reason(final, payload) is None


@pytest.mark.parametrize("budget", [float("nan"), float("inf"), -1, 0])
def test_invalid_budget_prevents_provider_access(tmp_path, budget):
    with (patch.object(pilot, "connection") as connect,
          pytest.raises(ValueError, match="finite and positive")):
        pilot.run_call(tmp_path, {}, 0, "factual_handoff", budget=budget)
    connect.assert_not_called()


def test_failed_attempt_blocks_a_different_call(tmp_path):
    (tmp_path / "protocol.json").write_text("{}")
    (tmp_path / "call-00.metrics.json").write_text(json.dumps({
        "state": "FAILED", "reserved_usd": 0.1, "estimate": None}))
    payload = {"model": "gpt-5.6-sol", "max_output_tokens": 64}
    with (patch.object(pilot, "payload_for", return_value=payload),
          patch.object(pilot, "connection") as connect,
          pytest.raises(ValueError, match="prior uncertain")):
        pilot.run_call(tmp_path, {}, 1, "history_replay", budget=20)
    connect.assert_not_called()


def test_factorial_arms_only_receive_declared_material(tmp_path):
    for name in ("shared", "history", "mechanism"):
        (tmp_path / name).write_text(name.upper())
    protocol = {"files": {name: {"path": name} for name in
                          ("shared", "history", "mechanism")},
                "arms": {"compact": ["shared"], "enriched": ["shared", "mechanism"]},
                "model": "gpt-5.6-sol", "max_output_tokens": 64,
                "reasoning_effort": "low", "instructions": "instructions", "task": "task"}
    compact = pilot.payload_for(protocol, tmp_path, "compact")["input"][0]["content"]
    enriched = pilot.payload_for(protocol, tmp_path, "enriched")["input"][0]["content"]
    assert compact == "SHARED\n\ntask"
    assert enriched == "SHARED\n\nMECHANISM\n\ntask"


def test_protocol_block_precedes_provider_access(tmp_path):
    with (patch.object(pilot, "connection") as connect,
          pytest.raises(ValueError, match="protocol blocks execution")):
        pilot.run_call(tmp_path, {"execution_blocked": "output cap not enforced"},
                       0, "compact", budget=20)
    connect.assert_not_called()


def test_explicit_observed_usage_policy_relaxes_only_missing_echo():
    payload = {"model": "gpt-5.6-sol", "max_output_tokens": 4096}
    final = {"model": "gpt-5.6-sol", "max_output_tokens": None,
             "usage": {"output_tokens": 1000}}
    assert pilot.experiment_stop_reason(final, payload, {}) is not None
    policy = {"limit_policy": "observe_actual_usage"}
    assert pilot.experiment_stop_reason(final, payload, policy) is None
    final["usage"]["output_tokens"] = 5000
    assert pilot.experiment_stop_reason(final, payload, policy) == "provider_exceeded_output_cap"
    final["usage"]["output_tokens"] = 1000
    final["model"] = "other-model"
    assert (pilot.experiment_stop_reason(final, payload, policy)
            == "provider_model_identity_mismatch")
