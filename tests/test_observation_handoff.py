"""Observation changes are evidence, not semantic acceptance or reset triggers."""

import json

from driver_port_factory.acquisition.job import ArtifactOccurrence
from driver_port_factory.codex.accounting import estimate
from driver_port_factory.codex.context_reset import reset_session
from driver_port_factory.codex.observation_handoff import observation_handoff
from driver_port_factory.control.context_report import cache_cost_sensitivity
from driver_port_factory.core.continuation import record_continuation
from driver_port_factory.core.events import StageEvent
from driver_port_factory.migration.contracts import MigrationStage as S
from tests.test_context_policy import worker
from tests.workflow_support import ready_implementation


def observe(project, ordinal, observation):
    return record_continuation(
        project, S.DRIVER_IMPLEMENTATION, ArtifactOccurrence(str(ordinal) * 64, ordinal),
        str(ordinal), detail="synthetic observation", receipt=f"receipt-{ordinal}.json",
        observation=observation)


def test_handoff_keeps_observation_changes_and_unchanged_failures(tmp_path):
    project = ready_implementation(tmp_path)
    key, _ = worker(project)
    observe(project, 1, {"qemu_observed": False, "runtime_bound": False, "logs_observed": False})
    observe(project, 2, {"qemu_observed": True, "runtime_bound": True, "logs_observed": False})
    handoff = reset_session(project, S.DRIVER_IMPLEMENTATION, key, reason="new observations")
    packet = json.loads(project.artifacts.path_for_digest(handoff["digest"]).read_text())
    observed = packet["execution_observations"]
    assert {item["field"] for item in observed["changes"]} == {"qemu_observed", "runtime_bound"}
    assert observed["recent"][0]["observation"]["logs_observed"] is False
    assert observed["recent"][1]["receipt"] == "receipt-1.json"
    assert project.stage(S.DRIVER_IMPLEMENTATION).status.value == "READY"
    project.verify_integrity()


def test_unknown_observations_are_not_false_or_skipped(tmp_path):
    project = ready_implementation(tmp_path)
    observe(project, 1, {"qemu_observed": False})
    observe(project, 2, None)  # Legacy event: do not compare across it to an older observation.
    observe(project, 3, {"qemu_observed": True})
    handoff = observation_handoff(project, S.DRIVER_IMPLEMENTATION)
    assert handoff["changes"] == []
    assert handoff["recent"][1]["observation"] is None


def test_observation_comparison_stops_at_new_episode(tmp_path):
    project = ready_implementation(tmp_path)
    observe(project, 1, {"runtime_bound": False})
    project.record_event(StageEvent.RETRIED, {
        "stage": S.DRIVER_IMPLEMENTATION.value, "operator_reopen": True})
    observe(project, 2, {"runtime_bound": True})
    handoff = observation_handoff(project, S.DRIVER_IMPLEMENTATION)
    assert len(handoff["recent"]) == 1 and handoff["changes"] == []


def test_cache_counterfactual_does_not_mix_unknown_or_historical_rates():
    usage = {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 100}
    quote = estimate(usage, "gpt-5.6-sol")
    job = {"usage": usage, "estimate": quote, "model": "gpt-5.6-sol"}
    result = cache_cost_sensitivity([
        job, {**job, "usage": None},
        {**job, "estimate": {**quote, "price_date": "old-rate-date"}},
    ])
    assert result["covered_calls"] == 1 and result["uncovered_calls"] == 2
    assert result["estimated_usd_if_same_tokens_uncached"] == 0.006
    assert result["estimated_cache_discount_usd"] == 0.00324
    assert cache_cost_sensitivity([])["estimated_cache_discount_usd"] is None


def test_transition_survives_repeated_observations(tmp_path):
    project = ready_implementation(tmp_path)
    observe(project, 1, {"runtime_bound": False, "logs_observed": False})
    observe(project, 2, {"runtime_bound": True, "logs_observed": False})
    observe(project, 3, {"runtime_bound": True, "logs_observed": False})
    handoff = observation_handoff(project, S.DRIVER_IMPLEMENTATION)
    assert handoff["changes"] == []
    transition = handoff["last_transition"]
    assert transition["previous"]["receipt"] == "receipt-1.json"
    assert transition["current"]["receipt"] == "receipt-2.json"
    assert transition["changes"] == [
        {"field": "runtime_bound", "previous": False, "current": True}]


def test_transition_search_stops_at_unknown_or_different_fields():
    from driver_port_factory.codex.observation_handoff import last_transition
    def record(observation):
        return {"observation": observation}
    for gap in (None, {}, {"other_field": True}):
        assert last_transition([
            record({"runtime_bound": True}), record(gap),
            record({"runtime_bound": True}), record({"runtime_bound": False}),
        ]) is None
