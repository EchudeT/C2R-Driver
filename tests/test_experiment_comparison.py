"""Synthetic/offline experiment infrastructure checks, not real driver or model validation."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).parents[1] / "scripts/native_experiment"
sys.path.insert(0, str(TOOLS))
from baseline_prompt import render
from comparison_metrics import (
    GoalState,
    complete_final_batch,
    dpf_outcome,
    native_outcome,
    normalized_usage,
    quote,
    usage_from_jobs,
)
from comparison_summary import summarize
from docker_policy import validated_run
from goal_client import GoalClient
from worker_isolation import prepare_view
from worker_sandbox import namespace_command

sys.path.remove(str(TOOLS))

RATES = {"input": 0.2, "cached_input": 0.02, "output": 1.2}


def test_native_cumulative_usage_replaces_instead_of_double_counting():
    state = GoalState()
    for count in [1000, 2000, 2000]:
        state.observe(
            {
                "method": "thread/tokenUsage/updated",
                "params": {
                    "tokenUsage": {
                        "total": {
                            "inputTokens": count,
                            "cachedInputTokens": 500,
                            "outputTokens": 100,
                        }
                    },
                },
            }
        )
    assert state.usage["input_tokens"] == 2000
    assert quote(state.usage, RATES) == pytest.approx(0.00043)


def test_unknown_usage_and_unsupported_cache_writes_are_not_free():
    usage, unknown = usage_from_jobs(
        [
            {"usage": None},
            {
                "usage": {
                    "input_tokens": 1000,
                    "cached_input_tokens": 500,
                    "output_tokens": 100,
                }
            },
        ]
    )
    assert unknown == 1 and usage["input_tokens"] == 1000
    assert quote(None, RATES) is None
    assert quote(usage | {"cache_write_input_tokens": 10}, RATES) is None
    assert (
        normalized_usage({"input_tokens": 1, "cached_input_tokens": 2, "output_tokens": 0}) is None
    )


def test_goals_do_not_self_approve_the_public_suite_and_budget_waits_for_turn():
    state = GoalState()
    state.goal = {"status": "complete"}
    state.turn_active = True
    state.usage = {"input_tokens": 10_000_000, "cached_input_tokens": 0, "output_tokens": 0}
    limits = {"usd": 1, "seconds": 60}
    assert state.stop_reason(10, limits, RATES) is None
    state.turn_active = False
    assert state.stop_reason(10, limits, RATES) == "COST_LIMIT"
    assert not native_outcome({}, ["native-test"])["suite_passed"]


def test_incomplete_native_suite_and_latest_fail_are_failures():
    receipt = {
        "status": "PASS",
        "build": {"exit_code": 0},
        "tests": [
            {"case": "a", "passed": True},
        ],
    }
    assert not native_outcome(receipt, ["a", "b"])["suite_passed"]
    receipt["status"] = "FAIL"
    assert not native_outcome(receipt, ["a"])["suite_passed"]
    assert native_outcome(receipt, ["a"])["build_passed"]
    # A complete failed batch is still final evidence: do not rerun or select an older PASS.
    report = {"execution_status": "FAIL", "run": {"final_suite": True, "cases": [{}]}}
    assert complete_final_batch(report, 1)
    assert not complete_final_batch(report, 2)


def test_summary_retains_failed_attempts_and_unknown_cost_without_zero_case_division():
    sample = {
        "method": "codex",
        "driver": "synthetic",
        "model": "synthetic-model",
        "reasoning_effort": "medium",
        "cases_expected": 0,
        "cases_passed": 0,
        "baseline_identity_confirmed": True,
        "build_passed": True,
        "suite_passed": True,
        "usage_status": "known",
        "usd_estimate": 1,
    }
    failed = sample | {
        "build_passed": None,
        "suite_passed": False,
        "usage_status": "unknown",
        "usd_estimate": None,
    }
    group = summarize([sample, failed])["groups"][0]
    assert group["attempts"] == 2
    assert group["suite_pass_rate"] == 0.5 and group["build_unknown"] == 1
    assert group["cost_unknown_or_partial"] == 1 and group["median_usd_known"] == 1
    assert group["case_pass_rate"] is None


def test_baseline_model_view_excludes_factory_tools_and_other_trials(tmp_path):
    trial = tmp_path / "codex"
    trial.mkdir()
    operator = tmp_path / "records"
    operator.mkdir()
    protocol = {
        "factory": str(tmp_path / "factory"),
        "root": str(tmp_path / "suite"),
        "qemu": str(tmp_path / "qemu"),
        "codex_bin": sys.executable,
    }
    view = prepare_view(protocol, trial, operator, "codex", "/fake/bwrap", tmp_path / "socket")
    visible = view["readonly"] + view["writable"]
    assert str(trial / "source") in visible and str(trial / "target") in visible
    for forbidden in (
        trial,
        trial / "build.sh",
        trial / "check.sh",
        tmp_path / "factory",
        tmp_path / "suite",
    ):
        assert str(forbidden) not in visible


def test_factory_build_must_be_for_the_tested_artifact_without_path_comparison():
    report = {
        "execution_status": "PASS",
        "run": {
            "final_suite": True,
            "runtime_artifact": {"sha256": "final", "path": "/tmp/new"},
            "cases": [{"status": "PASS"}],
        },
    }
    build = {"status": "PASS", "published_sha256": "final", "artifact": "/tmp/old"}
    assert dpf_outcome(report, build, 1)["build_passed"]
    assert dpf_outcome(report, build, 1)["suite_passed"]
    assert not dpf_outcome(report, build | {"published_sha256": "other"}, 1)["build_passed"]


def test_baseline_prompts_are_specific_source_driven_and_do_not_supply_factory_tools():
    root = Path(__file__).parents[1]
    config = json.loads((root / "configs/experiments/native-drivers.json").read_text())
    for driver in ["virtio-rng", "virtio-blk", "virtio-net", "nvme-pci"]:
        task = config["drivers"][driver] | {"driver": driver}
        prompt = render(task, "/worker", "/qemu", config)
        assert driver in prompt and config["image_id"] in prompt and "x86_64" in prompt
        assert str(Path("/worker/target") / task["omit"]) in prompt
        assert "Semantic alignment" in prompt and "Functional alignment" in prompt
        assert "Do not implement only what the tests inspect" in prompt
        assert "No\nFactory-written build.sh" in prompt
        assert "Use `./build.sh`" not in prompt
    for driver, fixture, machine in (
        ("evbug", "input/src/dpf_evbug_public.rs", "q35"),
        ("ne2k-pci", "network/src/dpf_ne2k_public.rs", "pc"),
    ):
        task = config["drivers"]["virtio-rng"] | {
            "driver": driver,
            "suite_kind": "fixed-public-fixture",
            "machine": machine,
            "fixture_file": "kernel/core/comps/" + fixture,
            "target_baseline": "original upstream target without this driver",
        }
        prompt = render(task, "/worker", "/qemu", config)
        assert task["fixture_file"] in prompt
        assert f"QEMU machine: `{machine}`" in prompt
        assert "no such tools are supplied to this baseline" in prompt
        assert "Only the unchanged fixture may emit its test success markers" in prompt


def test_native_goal_is_established_before_the_user_turn_without_inference():
    class FakeClient(GoalClient):
        def __init__(self):
            self.state = GoalState()
            self.calls = []

        def request(self, method, params, timeout=30):
            self.calls.append((method, params))
            return {"thread": {"id": "synthetic-thread"}, "goal": {"status": "active"}}

        def send(self, message):
            self.calls.append((message["method"], {}))

    client = FakeClient()
    client.start(
        Path("/worker"),
        {"driver": "virtio-rng", "model": "synthetic-model", "reasoning_effort": "medium"},
        "synthetic prompt",
    )
    assert [name for name, _ in client.calls] == [
        "initialize",
        "initialized",
        "thread/start",
        "thread/goal/set",
        "turn/start",
    ]
    assert client.state.goal["status"] == "active"


def test_docker_cannot_mount_answer_tree_or_follow_an_escape_symlink(tmp_path):
    target = tmp_path / "worker"
    target.mkdir()
    answers = tmp_path / "answers"
    answers.mkdir()
    (target / "escape").symlink_to(answers, target_is_directory=True)
    for origin in (answers, target / "escape", Path("/var/run/docker.sock")):
        with pytest.raises(ValueError, match="mount outside"):
            validated_run(
                ["run", "-v", f"{origin}:/data", "fixed-image"], "fixed-image", [target], []
            )
    validated_run(
        ["run", "-v", f"{target}:/data", "fixed-image", "true"], "fixed-image", [target], []
    )
    with pytest.raises(ValueError, match="option unavailable"):
        validated_run(["run", "--privileged", "fixed-image"], "fixed-image", [target], [])


def test_real_namespace_hides_neighboring_answer_files_and_host_docker_socket(tmp_path):
    bwrap = shutil.which("bwrap") or Path(__file__).parents[1] / ".local/bin/bwrap"
    if not Path(bwrap).exists():
        pytest.skip("bubblewrap not installed; production launcher refuses to fall back")
    visible = tmp_path / "worker"
    visible.mkdir()
    hidden = tmp_path / "answers"
    hidden.write_text("synthetic withheld answer")
    proxy = tmp_path / "docker"
    proxy.write_text("#!/bin/sh\nexit 1\n")
    manifest = {
        "bwrap": str(bwrap),
        "readonly": [],
        "writable": [str(visible)],
        "docker_proxy": str(proxy),
        "broker": str(proxy),
        "home": str(visible),
        "cwd": str(visible),
    }
    program = (
        f"from pathlib import Path; assert Path({str(visible)!r}).exists(); "
        f"assert not Path({str(hidden)!r}).exists(); "
        "assert not Path('/var/run/docker.sock').exists(); "
        "assert not Path('/home/unix/.codex').exists()"
    )
    subprocess.run(namespace_command(manifest, ["/bin/python3.11", "-c", program]), check=True)
