"""Stage wall time from the existing ledger; per-call token deltas from sidecars."""

import json
import sqlite3
from datetime import UTC, datetime

from ..codex.accounting import FIELDS, PRICE_DATE, PRICE_SOURCE, estimate, read_jobs, usage_delta


def job_elapsed(job: dict, now: datetime, *, controller_active: bool) -> float:
    """Only an explicitly live invocation may accrue time between checkpoints.

    Legacy interrupted calls have no completed_at. Their recorded elapsed is a
    lower bound, not permission to charge all subsequent controller downtime.
    """
    elapsed = max(0, job.get("elapsed_seconds", 0))
    if (controller_active and job.get("invocation_state") == "RUNNING"
            and job.get("started_at") and not job.get("completed_at")):
        return max(elapsed, (now - datetime.fromisoformat(job["started_at"])).total_seconds())
    return elapsed


def stage_times(events, now: datetime, intervals=None) -> dict:
    def elapsed(start, end):
        if intervals is None:
            return max(0, (end - start).total_seconds())
        return sum(max(0, (min(end, right) - max(start, left)).total_seconds())
                   for left, right in intervals)
    result, active, waiting = {}, {}, {}
    for event in events:
        payload = json.loads(event["payload"])
        stage = payload.get("stage")
        if not stage:
            continue
        row = result.setdefault(
            stage, {"elapsed_seconds": 0.0, "waiting_seconds": 0.0, "attempts": 0}
        )
        stamp = datetime.fromisoformat(event["created_at"])
        kind = event["event_type"]
        if stage in active and kind in {
            "stage.completed",
            "stage.retried",
            "stage.waiting_for_user",
        }:
            row["elapsed_seconds"] += elapsed(active.pop(stage), stamp)
        if stage in waiting and kind in {
            "stage.completed",
            "stage.retried",
            "stage.resumed_after_user",
        }:
            row["waiting_seconds"] += max(0, (stamp - waiting.pop(stage)).total_seconds())
        if kind == "stage.started":
            active[stage] = stamp
            row["attempts"] += 1
        elif kind == "stage.resumed_after_user":
            active[stage] = stamp
        elif kind == "stage.waiting_for_user":
            waiting[stage] = stamp
    for stage, stamp in active.items():
        result[stage]["elapsed_seconds"] += elapsed(stamp, now)
    for stage, stamp in waiting.items():
        result[stage]["waiting_seconds"] += max(0, (now - stamp).total_seconds())
    return result


def project_statistics(project, *, pricing_model=None, pricing_tier=None) -> dict:
    from .evidence import evidence_summary
    now = datetime.now(UTC)
    from .runtime import controller_status
    controller = controller_status(project)["state"]
    stopped = controller == "STOPPED"
    path = project.control / "controller.json"
    record = json.loads(path.read_text()) if path.exists() else {}
    if stopped and record.get("completed_at"):
        now = datetime.fromisoformat(record["completed_at"])
    intervals = None
    if "intervals" in record:
        intervals = [(datetime.fromisoformat(a), datetime.fromisoformat(b))
                     for a, b in record["intervals"]]
        intervals.append((datetime.fromisoformat(record["started_at"]), now))
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        times = stage_times(
            connection.execute(
                "SELECT created_at,event_type,payload FROM events ORDER BY sequence"
            ),
            now,
            intervals,
        )
    rows = {}
    for stage in project.stages():
        rows[stage.name.value] = {
            "stage": stage.name.value,
            "status": stage.status.value,
            "execution_state": "STOPPED" if stopped and stage.status.value == "RUNNING" else stage.status.value,
            "acceptance": stage.message if (stage.message or "").startswith("WORKER_ACCEPTED:") else None,
            **times.get(
                stage.name.value, {"elapsed_seconds": 0.0, "waiting_seconds": 0.0, "attempts": 0}
            ),
            "codex_calls": 0,
            "codex_seconds": 0.0,
            "usage": dict.fromkeys(FIELDS, 0),
            "unknown_usage_calls": 0,
            "unpriced_calls": 0,
            "usd": 0.0,
        }
    previous, jobs, reasons = {}, [], {}
    for job in read_jobs(project.control / "codex"):
        row = rows.get(job.get("stage"))
        if row is None:
            continue
        thread = job.get("thread_id")
        raw = job.get("reported_usage", [])
        current = raw[-1] if raw else None
        baseline = previous.get(thread) if job.get("resumed") else dict.fromkeys(FIELDS, 0)
        if "usage_baseline" in job:
            baseline = job["usage_baseline"]
        usage = usage_delta(current, baseline) if thread else None
        if thread:
            previous[thread] = current
        model = job.get("model") or pricing_model
        tier = pricing_tier or job.get("service_tier") or "standard"
        quote = (
            job["estimate"]
            if "estimate" in job and not pricing_tier and (job.get("model") or not pricing_model)
            else estimate(usage, model, tier)
        )
        row["codex_calls"] += 1
        # A stale RUNNING call from a killed controller is not live in a later run.
        same_controller = job.get("controller_started_at") == record.get("started_at")
        elapsed = job_elapsed(job, now, controller_active=(
            controller == "ACTIVE" and same_controller and bool(record.get("started_at"))))
        row["codex_seconds"] += elapsed
        if usage is None:
            row["unknown_usage_calls"] += 1
        else:
            for field in FIELDS:
                row["usage"][field] += usage[field]
        if quote is None:
            row["unpriced_calls"] += 1
        else:
            row["usd"] += quote["usd"]
        reason = job.get("call_reason") or "unknown"
        group = reasons.setdefault(reason, {"codex_calls": 0, "codex_seconds": 0.0,
            "usd": 0.0, "unknown_usage_calls": 0, "unpriced_calls": 0,
            "usage": dict.fromkeys(FIELDS, 0)})
        group["codex_calls"] += 1
        group["codex_seconds"] += elapsed
        group["unknown_usage_calls"] += usage is None
        group["unpriced_calls"] += quote is None
        group["usd"] += quote["usd"] if quote else 0
        if usage is not None:
            for field in FIELDS:
                group["usage"][field] += usage[field]
        jobs.append(
            {
                "stage": job["stage"],
                "job_id": job["job_id"],
                "call_reason": reason,
                "thread_id": thread,
                "resumed": job.get("resumed"),
                "usage": usage,
                "estimate": quote,
                "model": model,
                "elapsed_seconds": elapsed,
                "usage_status": "known" if usage is not None else "unknown",
                "timing_status": "complete" if job.get("completed_at") else "checkpoint_only",
                "context_policy": job.get("context_policy"),
                "context_epoch": job.get("context_epoch"),
                "context_action": job.get("context_action"),
                "context_handoff": job.get("context_handoff"),
                "session_key": job.get("session_key"),
                "service_tier": tier,
                "prompt_bytes": job.get("prompt_bytes"),
                "first_response_seconds": job.get("first_response_seconds"),
                "auto_compact_token_limit": job.get("auto_compact_token_limit"),
                "policy_sha256": job.get("policy_sha256"),
                "invocation_state": job.get("invocation_state"),
            }
        )
    totals = {
        field: sum(row[field] for row in rows.values())
        for field in (
            "elapsed_seconds",
            "waiting_seconds",
            "codex_seconds",
            "codex_calls",
            "unknown_usage_calls",
            "unpriced_calls",
            "usd",
        )
    }
    totals["usage"] = {field: sum(row["usage"][field] for row in rows.values()) for field in FIELDS}
    return {
        "schema_version": 1,
        "stages": list(rows.values()),
        "totals": totals,
        "jobs": jobs,
        "by_call_reason": reasons,
        "evidence": evidence_summary(project),
        "price_source": PRICE_SOURCE,
        "price_date": PRICE_DATE,
        "pricing_model_for_missing_metadata": pricing_model,
        "note": "Recorded controller downtime and user waits are excluded; "
        "unfinished calls use recorded "
        "checkpoint time unless confirmed live. Older runs may lack interval history. "
        "Retries included. Unknown usage is not zero. USD assumes short context with 224k "
        "auto-compaction; reasoning output is already included. Not a relay invoice.",
    }


def duration(seconds: float) -> str:
    if 0 < seconds < 1:
        return "<1s"
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


def cost_text(row: dict) -> str:
    if row["codex_calls"] and row["unpriced_calls"] == row["codex_calls"]:
        return "unknown"
    suffix = " (partial)" if row["unpriced_calls"] else ""
    return f"{row['usd']:.4f}{suffix}"
