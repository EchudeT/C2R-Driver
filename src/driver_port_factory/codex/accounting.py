"""Local token accounting; no extra model requests or credentials in telemetry."""

import json
import os
import tomllib
from datetime import datetime
from pathlib import Path

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
PRICE_SOURCE = "https://developers.openai.com/api/docs/pricing"
PRICE_DATE = "2026-09-19"
# USD / million tokens: uncached input, cached input, output (Standard, short context).
RATES = {
    "gpt-6-astra": (10, 1, 50),
    "gpt-5.6-sol": (4, 0.4, 20),
    "gpt-5.6-terra": (2, 0.2, 12),
    "gpt-5.6-luna": (0.2, 0.02, 1.2),
}


def model_settings(model: str | None) -> dict:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    path = home / "config.toml"
    config = tomllib.loads(path.read_text()) if path.is_file() else {}
    return {
        "model": model or config.get("model"),
        "service_tier": config.get("service_tier") or "standard",
    }


def usage_delta(current: dict | None, previous: dict | None) -> dict | None:
    if current is None or previous is None:
        return None
    result = {}
    for field in FIELDS:
        value, baseline = current.get(field), previous.get(field)
        if type(value) is not int or type(baseline) is not int or value < baseline:
            return None
        result[field] = value - baseline
    if result["cached_input_tokens"] > result["input_tokens"]:
        return None
    # Cache-write accounting is not inferred from counters whose inclusion semantics
    # are unknown. Keep tokens but decline a quote when such writes are reported.
    writes = current.get("cache_write_input_tokens", 0)
    prior_writes = previous.get("cache_write_input_tokens", 0)
    if (type(writes) is not int or type(prior_writes) is not int or
            prior_writes < 0 or writes < prior_writes):
        return None
    result["cache_write_input_tokens"] = writes - prior_writes
    return result


def estimate(usage: dict | None, model: str | None, tier: str = "standard") -> dict | None:
    if usage is None or model not in RATES or usage.get("cache_write_input_tokens", 0):
        return None
    multiplier = {
        "standard": 1,
        "default": 1,
        "fast": 2,
        "priority": 2,
        "flex": 0.5,
        "batch": 0.5,
    }.get(tier)
    if multiplier is None:
        return None
    inp, cached, out = RATES[model]
    normal = usage["input_tokens"] - usage["cached_input_tokens"]
    input_cost = normal * inp + usage["cached_input_tokens"] * cached
    output_cost = usage["output_tokens"] * out
    return {
        "usd": round((input_cost + output_cost) * multiplier / 1e6, 8),
        "model": model,
        "service_tier": tier,
        "source": PRICE_SOURCE,
        "price_date": PRICE_DATE,
        "basis": "short context assumed with 224k auto-compaction; "
        "public API rates, not relay invoice",
    }


def read_jobs(directory: Path) -> list[dict]:
    """Small sidecars plus first event only; never load full conversation logs."""
    jobs = []
    for path in sorted(directory.glob("*.metrics.json")):
        data = json.loads(path.read_text())
        if not data.get("thread_id"):
            events = path.with_name(path.name.replace(".metrics.json", ".events.jsonl"))
            if events.is_file():
                with events.open() as stream:
                    first = json.loads(stream.readline())
                data["thread_id"] = first.get("thread_id")
        data["sort_time"] = (
            datetime.fromisoformat(data["started_at"]).timestamp()
            if data.get("started_at")
            else path.stat().st_mtime
        )
        data["metrics_path"] = str(path)
        jobs.append(data)
    # File timestamps are also available for historical records without clock metadata.
    return sorted(jobs, key=lambda j: (j["sort_time"], j["metrics_path"]))


def latest_usage(directory: Path, thread_id: str | None) -> dict | None:
    if thread_id is None:
        return dict.fromkeys(FIELDS, 0)
    for job in reversed(read_jobs(directory)):
        if job.get("thread_id") == thread_id:
            raw = job.get("reported_usage", [])
            return raw[-1] if raw else None
    return None
