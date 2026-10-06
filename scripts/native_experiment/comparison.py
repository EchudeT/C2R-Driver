"""Shared protocol, local accounting and native-goal baseline execution."""

import argparse
import csv
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from common import HERE, read, write
from comparison_metrics import (
    complete_final_batch,
    dpf_outcome,
    empty_outcome,
    native_outcome,
    quote,
    usage_from_jobs,
)
from goal_client import GoalClient, terminate
from worker_isolation import isolated_worker

REPO = HERE.parents[1]


def now():
    return datetime.now(UTC).isoformat()


def configure_factory(factory):
    """Operator-side import path; the baseline model never receives this package."""
    sys.path.insert(0, str(Path(factory) / "src"))


def settings(protocol):
    provider = protocol["provider"]
    config = {
        "model_provider": "wavepulse",
        "model_providers.wavepulse.name": "WavePulse",
        "model_providers.wavepulse.base_url": provider["base_url"],
        "model_providers.wavepulse.env_key": "WAVEPULSE_API_KEY",
        "model_providers.wavepulse.wire_api": "responses",
        "model_providers.wavepulse.requires_openai_auth": False,
        "model_reasoning_effort": protocol["reasoning_effort"],
        "model_auto_compact_token_limit": protocol["compact_token_limit"],
        "approval_policy": "never",
        "features.apps": False,
        "features.goals": True,
        "features.multi_agent": False,
        "mcp_servers": {},
        "service_tier": "default",
    }
    args = []
    for key, value in config.items():
        args.extend(["-c", key + "=" + ("{}" if value == {} else json.dumps(value))])
    return args


def prepare(args):
    configure_factory(args.factory)
    from prepare import create_trial

    task = read(args.root / "seeds" / args.driver / "task.json")
    destination = args.root / "comparisons" / args.trial / args.driver
    if destination.exists():
        raise ValueError("Comparison already exists; use a new trial ID")
    for method in ("dpf", "codex"):
        if trial_path(args.root, args.driver, args.trial, method).exists():
            raise ValueError("Trial already exists: " + method)
    pricing = read(args.pricing)
    rates = pricing["models"][args.model]
    if any(type(rate) not in (int, float) or rate < 0 for rate in rates.values()):
        raise ValueError("Pricing rates must be nonnegative numbers")
    protocol = {
        "schema_version": 1,
        "created_at": now(),
        "driver": args.driver,
        "trial": args.trial,
        "root": str(args.root),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "compact_token_limit": 224000,
        "provider": {"name": "wavepulse", "base_url": "https://ai.wavepulse.live/v1"},
        "codex_bin": args.codex_bin,
        "codex_version": subprocess.check_output([args.codex_bin, "--version"], text=True).strip(),
        "factory": str(args.factory.resolve()),
        "qemu": str(args.qemu.resolve()),
        "factory_revision": subprocess.check_output(
            ["git", "-C", str(args.factory), "rev-parse", "HEAD"], text=True
        ).strip(),
        "limits": {"usd": args.max_cost_usd, "seconds": args.max_seconds},
        "pricing": pricing,
        "task": task,
        "knowledge_condition": "cold-external-kb",
        "shared_assets": "one verified suite; separate worktrees and build directories",
        "baseline": "native Codex goal; standard tools only, no Factory-written model tools",
        "workspace_policy": "mount/PID namespace; task-only Docker broker; no host home/socket",
    }
    destination.mkdir(parents=True)
    write(destination / "protocol.json", protocol)
    for method in ("dpf", "codex"):
        create_trial(args.root, args.driver, method, args.qemu, args.factory, args.trial)
    print("Prepared comparison; no model called:", destination / "protocol.json")


def trial_path(root, driver, trial, method):
    return root / "trials" / method / driver / trial


def dpf_usage(run):
    jobs = []
    for path in sorted((run / ".dpf/codex").glob("*.metrics.json")):
        try:
            jobs.append(read(path))
        except (json.JSONDecodeError, OSError):
            # A writer may be between truncate and flush; this is unknown, not zero or a gate.
            jobs.append({"usage": None})
    return usage_from_jobs(jobs)


def run_dpf(protocol, trial, directory, configured_codex):
    limits = protocol["limits"]
    env = dict(os.environ)
    env.pop("DPF_SHARED_KB", None)
    env["CODEX_HOME"] = str(directory / "model-home")
    argv = [
        str(trial / "launch.sh"),
        "--model",
        protocol["model"],
        "--codex-bin",
        str(configured_codex),
        "--no-analysis-review",
        "--no-final-evidence-review",
    ]
    # Both methods use the identical frozen price table and external cost monitor.
    rates = protocol["pricing"]["models"][protocol["model"]]
    started = time.monotonic()
    with (directory / "controller.log").open("w") as log:
        process = subprocess.Popen(
            argv, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True
        )
        reason = "PROCESS_EXIT"
        try:
            while process.poll() is None:
                usage, _ = dpf_usage(trial / "run")
                cost = quote(usage, rates)
                if (
                    limits["seconds"] is not None
                    and time.monotonic() - started >= limits["seconds"]
                ):
                    reason = "TIME_LIMIT"
                    break
                if limits["usd"] is not None and cost is not None and cost >= limits["usd"]:
                    reason = "COST_LIMIT"
                    break
                time.sleep(1)
        finally:
            terminate(process)
    usage, unknown = dpf_usage(trial / "run")
    return {
        "stop_reason": reason,
        "exit_code": process.returncode,
        "usage": usage,
        "unknown_usage_calls": unknown,
    }


def run_codex(protocol, trial, directory, configured_codex):
    rates = protocol["pricing"]["models"][protocol["model"]]
    argv = [str(configured_codex), "app-server", "--listen", "stdio://"]
    client = GoalClient(argv, directory)
    reason = "CLIENT_ERROR"
    try:
        started = time.monotonic()
        client.start(trial, protocol, (trial / "PROMPT.md").read_text())
        reason = client.wait(started, protocol["limits"], rates)
        client.stop(reason)
    finally:
        state = client.state
        write(
            directory / "goal-state.json",
            {
                "goal": state.goal,
                "usage": state.usage,
                "turns_started": state.turns_started,
                "stop_reason": reason,
            },
        )
        client.close()
    return {
        "stop_reason": reason,
        "usage": state.usage,
        "unknown_usage_calls": int(
            state.turns_started > 0 and (state.usage is None or state.turn_active)
        ),
        "native_goal": state.goal,
        "turns_started": state.turns_started,
    }


def execute(args):
    protocol = read(args.protocol)
    if args.method == "dpf":
        configure_factory(protocol["factory"])
    directory = args.protocol.parent / args.method
    directory.mkdir(exist_ok=False)
    record = {
        "method": args.method,
        "started_at": now(),
        "ended_at": None,
        "total_seconds": None,
        "protocol": str(args.protocol),
        "state": "RUNNING",
    }
    write(directory / "run.json", record)
    started = time.monotonic()
    try:
        if not os.environ.get("WAVEPULSE_API_KEY"):
            raise ValueError("Set WAVEPULSE_API_KEY from the ignored local credential file")
        version = subprocess.check_output([protocol["codex_bin"], "--version"], text=True).strip()
        if version != protocol["codex_version"]:
            raise ValueError("Codex version differs from the comparison protocol")
        trial = trial_path(
            Path(protocol["root"]), protocol["driver"], protocol["trial"], args.method
        )
        with isolated_worker(protocol, trial, directory, args.method) as prefix:
            # The controller's telemetry and the worker read the same secret-free settings.
            (directory / "model-home/config.toml").write_text(
                "\n".join(settings(protocol)[1::2]) + "\n"
            )
            configured_codex = directory / "codex-isolated.sh"
            configured_codex.write_text(
                "#!/bin/sh\nset -eu\nexec " + shlex.join([*prefix, *settings(protocol)]) + ' "$@"\n'
            )
            configured_codex.chmod(0o755)
            runner = run_dpf if args.method == "dpf" else run_codex
            record.update(runner(protocol, trial, directory, configured_codex))
        record["translation_ended_at"] = now()
        record["translation_seconds"] = round(time.monotonic() - started, 3)
        evaluation_started = time.monotonic()
        final_check(protocol, trial, directory, args.method)
        record["evaluation_seconds"] = round(time.monotonic() - evaluation_started, 3)
        record["state"] = "FINISHED"
    except (
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        subprocess.SubprocessError,
        KeyboardInterrupt,
    ) as error:
        record.update(state="ERROR", error=f"{type(error).__name__}: {error}")
    finally:
        record.update(ended_at=now(), total_seconds=round(time.monotonic() - started, 3))
        write(directory / "run.json", record)
    collect(args.protocol, args.method)
    print("Run record:", directory / "run.json")
    if record["state"] == "ERROR":
        raise ValueError(record["error"])


def latest_native(protocol):
    root = Path(protocol["root"])
    paths = list(
        (root / "results/codex" / protocol["driver"] / protocol["trial"]).glob("*/check.json")
    )
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime_ns)


def factory_statistics(trial):
    from driver_port_factory.composition import open_project
    from driver_port_factory.control.statistics import project_statistics

    project = open_project(trial / "run", read_only=True, verify_artifacts=False)
    stats = project_statistics(project)
    return stats


def dpf_result(protocol, trial):
    stats = factory_statistics(trial)
    report_ref = stats["evidence"]["reports"].get("public_qemu_report")
    report = read(report_ref["path"]) if report_ref else {}
    count = len(protocol["task"]["tests"]) + len(protocol["task"]["benchmarks"])
    if not report:
        return empty_outcome(count), stats
    from driver_port_factory.acquisition.repository import load_repository_acquisition
    from driver_port_factory.composition import open_project

    project = open_project(trial / "run", read_only=True, verify_artifacts=False)
    target = project.root / load_repository_acquisition(project).target_worktree.path
    build_path = target / ".dpf-output/platform/current-build.json"
    build = read(build_path) if build_path.exists() else {}
    return dpf_outcome(report, build, count), stats


def final_check(protocol, trial, directory, method):
    """One operator evaluation; existing Factory final batches are reused even when they fail."""
    from check import check

    if method == "dpf":
        from driver_port_factory.acquisition.repository import load_repository_acquisition
        from driver_port_factory.composition import open_project

        project = open_project(trial / "run", read_only=True, verify_artifacts=False)
        stats = factory_statistics(trial)
        ref = stats["evidence"]["reports"].get("public_qemu_report")
        count = len(protocol["task"]["tests"]) + len(protocol["task"]["benchmarks"])
        if ref and complete_final_batch(read(ref["path"]), count):
            write(directory / "final-receipt.json", {"kind": "dpf", "path": ref["path"]})
            return
        target = project.root / load_repository_acquisition(project).target_worktree.path
    else:
        target = trial / "target"
    check(Path(protocol["root"]), protocol["driver"], target, directory / "evaluation", "check")
    write(
        directory / "final-receipt.json",
        {
            "kind": "native",
            "path": str(directory / "evaluation/check.json"),
        },
    )


def collect(protocol_path, method):
    protocol = read(protocol_path)
    if method == "dpf":
        configure_factory(protocol["factory"])
    directory = protocol_path.parent / method
    record = read(directory / "run.json")
    root = Path(protocol["root"])
    trial = trial_path(root, protocol["driver"], protocol["trial"], method)
    cases = [*protocol["task"]["tests"], *protocol["task"]["benchmarks"]]
    outcome = empty_outcome(len(cases))
    if method == "dpf" and (trial / "run/.dpf/project.json").exists():
        usage, unknown = dpf_usage(trial / "run")
        record.update(usage=usage, unknown_usage_calls=unknown)
        try:
            outcome, stats = dpf_result(protocol, trial)
            write(directory / "factory-statistics.json", stats)
        except (OSError, ValueError, RuntimeError) as error:
            record["collection_error"] = str(error)
    elif method == "codex":
        state_path = directory / "goal-state.json"
        if state_path.exists():
            state = read(state_path)
            record.update(usage=state["usage"], native_goal=state["goal"])
        receipt = latest_native(protocol)
        if receipt:
            outcome = native_outcome(read(receipt), cases)
            outcome["receipt"] = str(receipt)
    final_ref = directory / "final-receipt.json"
    if final_ref.exists():
        ref = read(final_ref)
        if ref["kind"] == "native":
            outcome = native_outcome(read(ref["path"]), cases)
        outcome["receipt"] = ref["path"]
    usage = record.get("usage")
    rates = protocol["pricing"]["models"][protocol["model"]]
    cost = quote(usage, rates)
    unknown = record.get("unknown_usage_calls", int(usage is None))
    result = (
        record
        | outcome
        | {
            "driver": protocol["driver"],
            "suite_kind": protocol["task"].get("suite_kind", "upstream-native"),
            "source_revision": protocol["task"]["upstream_linux"],
            "target_revision": protocol["task"]["upstream_asterinas"],
            "image_id": protocol["task"]["environment"]["image_id"],
            "trial": protocol["trial"],
            "model": protocol["model"],
            "reasoning_effort": protocol["reasoning_effort"],
            "base_url": protocol["provider"]["base_url"],
            "usd_estimate": cost,
            "cost_status": "unknown" if cost is None else ("partial" if unknown else "estimated"),
            "usage_status": "unknown" if usage is None else ("partial" if unknown else "known"),
            "pricing": protocol["pricing"],
            "unknown_usage_calls": unknown,
            "baseline_identity_confirmed": method == "dpf" or bool(record.get("native_goal")),
            "case_unit": protocol["task"].get(
                "case_unit", "fixed upstream test executable or benchmark script"
            ),
        }
    )
    write(directory / "result.json", result)
    return result


def export(args):
    rows = []
    for path in sorted(args.root.glob("comparisons/*/*/*/result.json")):
        result = read(path)
        usage = result.get("usage") or {}
        row = {key: value for key, value in result.items() if not isinstance(value, (dict, list))}
        row.update(
            {
                key: usage.get(key)
                for key in ("input_tokens", "cached_input_tokens", "output_tokens")
            }
        )
        rows.append(row)
    keys = sorted({key for row in rows for key in row})
    with args.output.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    print("Exported", len(rows), "runs:", args.output)
    from comparison_summary import summarize

    write(args.output.with_suffix(".summary.json"), summarize(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("prepare", help="prepare one driver pair; no model call")
    init.add_argument("--root", type=Path, required=True)
    init.add_argument("--driver", required=True)
    init.add_argument("--trial", default="01")
    init.add_argument("--qemu", type=Path, required=True)
    init.add_argument("--factory", type=Path, default=REPO)
    init.add_argument("--model", default="gpt-5.6-luna")
    init.add_argument("--reasoning-effort", default="medium")
    init.add_argument("--codex-bin", default="codex")
    init.add_argument(
        "--pricing", type=Path, default=REPO / "configs/experiments/local-pricing.json"
    )
    init.add_argument("--max-cost-usd", type=float, help="Omit for no cost limit")
    init.add_argument("--max-seconds", type=float, help="Omit for no translation time limit")
    run = commands.add_parser("run", help="explicitly starts paid translation")
    run.add_argument("protocol", type=Path)
    run.add_argument("--method", choices=("dpf", "codex"), required=True)
    gather = commands.add_parser("collect", help="read existing results; never rerun tests")
    gather.add_argument("protocol", type=Path)
    gather.add_argument("--method", choices=("dpf", "codex"), required=True)
    dump = commands.add_parser("export", help="one row per run, including failures")
    dump.add_argument("--root", type=Path, required=True)
    dump.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        args.root = args.root.resolve()
        if any(value is not None and value <= 0 for value in (args.max_cost_usd, args.max_seconds)):
            parser.error("Budgets must be positive")
        prepare(args)
    elif args.action == "run":
        execute(args)
    elif args.action == "collect":
        print(json.dumps(collect(args.protocol, args.method), indent=2))
    else:
        export(args)
