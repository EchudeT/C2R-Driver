"""Descriptive context-strategy measurements; never infer quality from stage PASS."""

import json
import sqlite3

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..codex.accounting import FIELDS, estimate
from ..codex.context_policy import read_policy
from ..core.models import ArtifactDirection
from ..migration.contracts import MigrationStage
from .runtime import controller_status
from .statistics import project_statistics


def aggregate(jobs: list[dict]) -> dict:
    usage = {field: sum(job["usage"][field] for job in jobs if job["usage"] is not None)
             for field in FIELDS}
    unknown = sum(job["usage"] is None for job in jobs)
    unpriced = sum(job["estimate"] is None for job in jobs)
    return {
        "calls": len(jobs), "known_usage": usage,
        "unknown_usage_calls": unknown, "unpriced_calls": unpriced,
        "known_estimated_usd": round(sum(
            job["estimate"]["usd"] for job in jobs if job["estimate"] is not None), 8),
        "cost_complete": bool(jobs) and not unpriced and not unknown,
        "codex_seconds": round(sum(job["elapsed_seconds"] for job in jobs), 3),
        "checkpoint_only_calls": sum(job["timing_status"] != "complete" for job in jobs),
        "known_usage_cache_hit_fraction": (
            usage["cached_input_tokens"] / usage["input_tokens"]
            if usage["input_tokens"] else None),
        "context_actions": {
            action: sum((job.get("context_action") or "unrecorded") == action for job in jobs)
            for action in sorted({job.get("context_action") or "unrecorded" for job in jobs})
        },
        "cache_cost_sensitivity": cache_cost_sensitivity(jobs),
    }


def cache_cost_sensitivity(jobs: list[dict]) -> dict:
    """An accounting counterfactual, not a prediction of costs after resetting."""
    measured, uncached, covered = 0.0, 0.0, 0
    for job in jobs:
        usage, quote = job.get("usage"), job.get("estimate")
        if usage is None or quote is None:
            continue
        tier = job.get("service_tier", "standard")
        same_rates = estimate(usage, job.get("model"), tier)
        without_cache = estimate({**usage, "cached_input_tokens": 0}, job.get("model"), tier)
        if same_rates != quote or without_cache is None:
            continue  # Do not mix frozen historical rates with a new rate table.
        measured += quote["usd"]
        uncached += without_cache["usd"]
        covered += 1
    return {
        "covered_calls": covered, "uncovered_calls": len(jobs) - covered,
        "estimated_usd_with_reported_cache": round(measured, 8) if covered else None,
        "estimated_usd_if_same_tokens_uncached": round(uncached, 8) if covered else None,
        "estimated_cache_discount_usd": round(uncached - measured, 8) if covered else None,
        "note": "Same tokens and rate metadata, changing only cache discount. This is not "
                "the cost of resetting: new context, rereads, output and cache hits may differ.",
    }


def _identity(project, jobs) -> dict:
    repositories = {}
    if project.stage(AcquisitionStage.REPOSITORY_ACQUISITION).status.value == "PASS":
        manifest = project.load_json_artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION, AcquisitionArtifact.REPOSITORY_MANIFEST)
        repositories = {item["role"]: {key: item[key] for key in
                        ("resolved_commit", "tree_id", "platform")}
                        for item in manifest["checkouts"]}
    contracts = {ref.kind: ref.digest for ref in project.current_artifact_refs(
        stage=MigrationStage.CONTRACTS, direction=ArtifactDirection.OUTPUT)
        if ref.kind in {"migration_contracts", "test_port_matrix"}}
    return {
        "scope": {name: getattr(project.config, name) for name in
                  ("source_platform", "target_platform", "driver_name")},
        "evaluation_mode": project.config.evaluation_mode.value,
        "reviews": {name: getattr(project.config, name) for name in
                    ("enable_analysis_review", "enable_final_evidence_review")},
        "repositories": repositories, "current_contract_digests": contracts,
        "models_and_tiers": sorted({json.dumps([j["model"], j["service_tier"]]) for j in jobs}),
        "prompt_policy_digests": sorted({j["policy_sha256"] for j in jobs
                                         if j.get("policy_sha256")}),
        "unrecorded_model_calls": sum(not j.get("model") for j in jobs),
        "unrecorded_prompt_policy_calls": sum(not j.get("policy_sha256") for j in jobs),
        "compaction_limits": sorted({j["auto_compact_token_limit"] for j in jobs
                                     if j.get("auto_compact_token_limit") is not None}),
    }


def _history(project, stage: str | None) -> dict:
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT sequence,created_at,event_type,payload FROM events "
            "WHERE event_type IN ('run.continuation','run.session_reset','run.context_policy') "
            "ORDER BY sequence").fetchall()
    events = [{"sequence": seq, "created_at": stamp, "event_type": kind,
               "payload": json.loads(raw)} for seq, stamp, kind, raw in rows]
    continuations = [e for e in events if e["event_type"] == "run.continuation"
                     and (stage is None or e["payload"]["stage"] == stage)]
    return {
        "context_events": [e for e in events if e["event_type"] != "run.continuation"],
        "continuation_observations": len(continuations),
        "repeated_unchanged_observations": sum(
            e["payload"].get("consecutive", 1) > 1 for e in continuations),
        "max_consecutive_unchanged": max(
            (e["payload"].get("consecutive", 1) for e in continuations), default=0),
        "note": "Unchanged fingerprints are mechanical observations, not semantic failures. "
                "Legacy runs may have no continuation events. Context events cover the whole run.",
    }


def context_report(project, *, stage: str | None = None) -> dict:
    if stage is not None:
        project.workflow.parse_stage(stage)
    stats = project_statistics(project)
    jobs = [job for job in stats["jobs"] if stage is None or job["stage"] == stage]
    policies, epochs = {}, {}
    for job in jobs:
        policy = job.get("context_policy") or {}
        name = f"{policy['name']}@{policy['version']}" if policy else "unrecorded"
        policies.setdefault(name, []).append(job)
        # A thread belongs to only one epoch; missing thread IDs must not be merged.
        epoch = job.get("context_epoch") or job.get("thread_id") or job["job_id"]
        epochs.setdefault(epoch, []).append(job)
    return {
        "schema_version": 1, "project": str(project.root), "stage_filter": stage,
        "controller": controller_status(project), "configured_policy": read_policy(project),
        "identity": _identity(project, jobs), "totals": aggregate(jobs),
        "by_policy": {key: aggregate(value) for key, value in policies.items()},
        "by_epoch": {key: aggregate(value) for key, value in epochs.items()},
        "history": _history(project, stage), "jobs": jobs,
        "session_lineage": session_lineage(stats["jobs"], stage=stage),
        "stages": stats["stages"], "evidence": stats["evidence"],
        "quality": {"required_obligations_omitted": None, "repeated_investigations": None,
                    "assessment": "Requires a common functional oracle and report review; "
                                  "not inferred from report keywords or stage PASS."},
        "cost_note": stats["note"],
    }


def session_lineage(jobs: list[dict], *, stage: str | None = None) -> dict:
    """Expose recorded continuity without claiming to know effective model context."""
    seen = {}
    calls = []
    for job in jobs:
        thread = job.get("thread_id")
        prior = seen.get(thread, []) if thread else []
        resumed = job.get("resumed")
        continuity = (
            "fresh" if resumed is False else
            "resume_with_prior_record" if resumed is True and prior else
            "resume_without_prior_record" if resumed is True else "unknown")
        if stage is None or job["stage"] == stage:
            calls.append({
                "job_id": job["job_id"], "stage": job["stage"], "thread_id": thread,
                "continuity": continuity,
                "prior_recorded_calls_same_thread": len(prior),
                "prior_recorded_stages_same_thread": list(dict.fromkeys(prior)),
            })
        if thread:
            seen.setdefault(thread, []).append(job["stage"])
    return {
        "calls": calls,
        "observed_thread_count": len({c["thread_id"] for c in calls if c["thread_id"]}),
        "effective_context": "unknown",
        "observed_compactions": None,
        "note": "Prior records cover the whole run even with a stage filter. Resume metadata "
                "does not prove which messages survived compaction. Configured compaction "
                "limits and cumulative input tokens do not count observed compactions. "
                "Different threads may be independent workers or reviewers, not resets.",
    }


def compare_reports(left: dict, right: dict) -> dict:
    checks = {}
    for field in ("scope", "evaluation_mode", "reviews", "repositories",
                  "current_contract_digests", "models_and_tiers", "prompt_policy_digests",
                  "compaction_limits"):
        a, b = left["identity"][field], right["identity"][field]
        checks[field] = "unknown" if not a or not b else "same" if a == b else "different"
    checks["stage_filter"] = ("same" if left["stage_filter"] == right["stage_filter"]
                              else "different")
    complete = all(report["totals"]["cost_complete"] for report in (left, right))
    return {
        "left": left, "right": right, "identity_checks": checks,
        "comparison_kind": "descriptive_only",
        "known_estimated_usd_difference_right_minus_left": round(
            right["totals"]["known_estimated_usd"] -
            left["totals"]["known_estimated_usd"], 8),
        "both_costs_complete": complete,
        "savings_claim_supported": False,
        "limitations": [
            "No quality-adjusted savings claim: functional equivalence is not established.",
            "Current contract digests do not prove identical inputs throughout either run.",
            ("Environment, provider, budget, cache state, human intervention and execution "
             "order need a separately frozen experiment protocol."),
            "Unknown usage, unpriced calls and unfinished checkpoints are not zero.",
        ],
    }
