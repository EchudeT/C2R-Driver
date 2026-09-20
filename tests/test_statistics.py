import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from driver_port_factory.codex.accounting import estimate, latest_usage, usage_delta
from driver_port_factory.control.statistics import project_statistics, stage_times


def counters(inp, cached, out):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}


def test_cumulative_counts_cache_and_reasoning_are_not_double_billed():
    current = {**counters(3_000_000, 1_500_000, 300_000), "reasoning_output_tokens": 100_000}
    delta = usage_delta(current, counters(1_000_000, 500_000, 100_000))
    assert delta == {**counters(2_000_000, 1_000_000, 200_000), "cache_write_input_tokens": 0}
    quote = estimate(delta, "gpt-5.6-sol")
    assert quote["usd"] == 8.4
    assert estimate(delta, "gpt-5.6-sol", "priority")["usd"] == 16.8
    assert estimate(delta, "unknown") is None
    assert estimate({**delta, "cache_write_input_tokens": 5}, "gpt-5.6-sol") is None


@pytest.mark.parametrize(
    "current,baseline",
    [
        (None, counters(0, 0, 0)),
        (counters(10, 0, 0), None),
        (counters(10, 0, 0), counters(20, 0, 0)),
        (counters(10, 11, 0), counters(0, 0, 0)),
        ({**counters(10, 0, 0), "cache_write_input_tokens": None}, counters(0, 0, 0)),
        ({**counters(10, 0, 0), "cache_write_input_tokens": "unknown"}, counters(0, 0, 0)),
        ({**counters(10, 0, 0), "cache_write_input_tokens": -1}, counters(0, 0, 0)),
        (counters(10, 0, 0), {**counters(0, 0, 0), "cache_write_input_tokens": 1}),
    ],
)
def test_missing_reset_or_invalid_usage_is_unknown(current, baseline):
    assert usage_delta(current, baseline) is None


def test_stage_time_retains_retries_excludes_user_wait_and_counts_live_time():
    def event(second, kind):
        return {
            "created_at": f"2026-09-19T00:00:{second:02d}+00:00",
            "event_type": "stage." + kind,
            "payload": json.dumps({"stage": "work"}),
        }

    events = [
        event(0, "started"),
        event(5, "waiting_for_user"),
        event(15, "resumed_after_user"),
        event(20, "completed"),
        event(25, "retried"),
        event(30, "started"),
        event(35, "retried"),
        event(40, "started"),
    ]
    result = stage_times(events, datetime(2026, 9, 19, 0, 0, 50, tzinfo=UTC))
    assert result["work"] == {"elapsed_seconds": 25, "waiting_seconds": 10, "attempts": 3}


def test_stats_independent_threads_retries_and_unknown_failed_calls(tmp_path):
    import sqlite3

    directory = tmp_path / "codex"
    directory.mkdir()
    database = tmp_path / "run.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE events(sequence INTEGER,created_at TEXT,event_type TEXT,payload TEXT)"
        )
    stage = lambda name: SimpleNamespace(
        name=SimpleNamespace(value=name), status=SimpleNamespace(value="PASS")
    )
    project = SimpleNamespace(
        control=tmp_path,
        database_path=database,
        stages=lambda: [stage("worker"), stage("reviewer")],
    )

    def job(n, thread, usage, resumed, name="worker"):
        (directory / f"{n}.metrics.json").write_text(
            json.dumps(
                {
                    "job_id": str(n),
                    "stage": name,
                    "thread_id": thread,
                    "started_at": f"2026-09-19T00:00:{n:02d}+00:00",
                    "reported_usage": [usage] if usage else [],
                    "elapsed_seconds": 2,
                    "resumed": resumed,
                    "model": "gpt-5.6-sol",
                }
            )
        )

    job(1, "a", counters(100, 50, 10), False)
    job(2, "b", counters(200, 100, 20), False, "reviewer")
    job(3, "a", counters(300, 150, 30), True)
    stats = project_statistics(project)
    assert stats["totals"]["usage"] == counters(500, 250, 50)
    assert latest_usage(directory, "a") == counters(300, 150, 30)
    job(4, "a", None, True)
    assert latest_usage(directory, "a") is None
    job(5, "a", counters(700, 350, 70), True)
    stats = project_statistics(project)
    assert stats["totals"]["unknown_usage_calls"] == 2
    assert stats["totals"]["usage"] == counters(500, 250, 50)
    job(6, "a", counters(800, 400, 80), True)
    assert project_statistics(project)["totals"]["usage"] == counters(600, 300, 60)


def test_model_settings_never_exports_provider_secrets(tmp_path, monkeypatch):
    from driver_port_factory.codex.accounting import model_settings

    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    (tmp_path / "config.toml").write_text(
        'model="gpt-5.6-sol"\nservice_tier="fast"\n[model_providers.relay]\napi_key="secret"\n'
    )
    assert model_settings(None) == {"model": "gpt-5.6-sol", "service_tier": "fast"}


def test_real_job_checkpoints_usage_and_retains_failed_call(tmp_path, monkeypatch):
    from driver_port_factory.codex.cli import run_codex_stage
    from driver_port_factory.codex.contracts import CodexBackend
    from driver_port_factory.codex.gateway import CodexExecGateway, CodexResult
    from driver_port_factory.migration.contracts import MigrationStage
    from tests.test_workflow_alignment import ready_implementation

    project = ready_implementation(tmp_path)
    calls = []

    def run(gateway, job):
        calls.append(job)
        gateway.on_event({"type": "thread.started", "thread_id": "test-thread"})
        if len(calls) == 3:
            raise RuntimeError("transport interrupted")
        gateway.on_event(
            {
                "type": "turn.completed",
                "usage": counters(len(calls) * 1000, len(calls) * 800, len(calls) * 50),
            }
        )
        return CodexResult(job.job_id, "A useful report", "test-thread")

    monkeypatch.setattr(CodexExecGateway, "run", run)
    monkeypatch.setattr(
        "driver_port_factory.codex.cli.model_settings",
        lambda _: {"model": "gpt-5.6-sol", "service_tier": "standard"},
    )
    kwargs = {
        "context": None,
        "backend": CodexBackend.EXEC,
        "codex_bin": "codex",
        "model": "gpt-5.6-sol",
    }
    for _ in range(2):
        run_codex_stage(project, MigrationStage.DRIVER_IMPLEMENTATION, **kwargs)
    assert calls[1].thread_id == "test-thread"
    stats = project_statistics(project)
    assert stats["totals"]["usage"] == counters(2000, 1600, 100)
    assert stats["totals"]["unknown_usage_calls"] == 0
    assert stats["jobs"][1]["usage"]["input_tokens"] == 1000
    with pytest.raises(RuntimeError, match="transport interrupted"):
        run_codex_stage(project, MigrationStage.DRIVER_IMPLEMENTATION, **kwargs)
    stats = project_statistics(project)
    assert stats["totals"]["unknown_usage_calls"] == 1
    assert stats["totals"]["codex_calls"] == 3
    assert stats["totals"]["usage"] == counters(2000, 1600, 100)


def test_cli_json_and_text_show_statistics(tmp_path, capsys):
    from driver_port_factory.cli import main
    from tests.test_workflow_alignment import ready_implementation

    project = ready_implementation(tmp_path)
    capsys.readouterr()
    assert main(["status", str(project.root), "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["stages"]) == len(project.stages())
    assert result["totals"]["codex_calls"] == 0
    assert main(["status", str(project.root)]) == 0
    text = capsys.readouterr().out
    assert "time=" in text and "TOTAL" in text and "Prices" in text
