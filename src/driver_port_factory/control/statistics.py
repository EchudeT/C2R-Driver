"""Stage wall time from the existing ledger; per-call token deltas from sidecars."""

import json
import sqlite3
from datetime import UTC, datetime

from ..codex.accounting import FIELDS, PRICE_DATE, PRICE_SOURCE, estimate, read_jobs, usage_delta


def stage_times(events, now: datetime) -> dict:
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
            row["elapsed_seconds"] += max(0, (stamp - active.pop(stage)).total_seconds())
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
        result[stage]["elapsed_seconds"] += max(0, (now - stamp).total_seconds())
    for stage, stamp in waiting.items():
        result[stage]["waiting_seconds"] += max(0, (now - stamp).total_seconds())
    return result


def project_statistics(project, *, pricing_model=None, pricing_tier=None) -> dict:
    now = datetime.now(UTC)
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        times = stage_times(
            connection.execute(
                "SELECT created_at,event_type,payload FROM events ORDER BY sequence"
            ),
            now,
        )
    rows = {}
    for stage in project.stages():
        rows[stage.name.value] = {
            "stage": stage.name.value,
            "status": stage.status.value,
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
    previous, jobs = {}, []
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
        elapsed = job.get("elapsed_seconds", 0)
        if job.get("started_at") and not job.get("completed_at"):
            elapsed = max(0, (now - datetime.fromisoformat(job["started_at"])).total_seconds())
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
        jobs.append(
            {
                "stage": job["stage"],
                "job_id": job["job_id"],
                "thread_id": thread,
                "usage": usage,
                "estimate": quote,
                "model": model,
                "elapsed_seconds": elapsed,
                "usage_status": "known" if usage is not None else "unknown",
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
        "price_source": PRICE_SOURCE,
        "price_date": PRICE_DATE,
        "pricing_model_for_missing_metadata": pricing_model,
        "note": "Wall time includes stopped controllers while RUNNING; user waits excluded. "
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
