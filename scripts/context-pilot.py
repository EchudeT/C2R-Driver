#!/usr/bin/env python3
"""Bounded text-only context replay pilot; dry-run unless --execute is supplied.

Uses the configured Responses provider. Never executes model tools, resumes an
existing thread, or edits a driver run. A reserved call is never retried here.
The budget is a conservative public-rate estimate, not a provider invoice cap.
"""

import argparse
import fcntl
import hashlib
import json
import math
import os
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from driver_port_factory.codex.accounting import FIELDS, RATES, estimate, usage_delta


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def load_protocol(path):
    protocol = json.loads(path.read_text())
    for record in protocol["files"].values():
        content = (path.parent / record["path"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != record["sha256"]:
            raise ValueError(f"pilot input digest mismatch: {record['path']}")
    return protocol


def payload_for(protocol, root, arm):
    def content(key):
        return (root / protocol["files"][key]["path"]).read_text()
    if "arms" in protocol:
        material = "\n\n".join(content(key) for key in protocol["arms"][arm])
    else:
        if arm not in {"history_replay", "factual_handoff"}:
            raise ValueError("unknown pilot arm")
        history = content("history") if arm == "history_replay" else content("handoff")
        material = history + "\n\n" + content("current")
    return {
        "model": protocol["model"], "store": False, "stream": True,
        "max_output_tokens": protocol["max_output_tokens"], "service_tier": "default",
        "reasoning": {"effort": protocol["reasoning_effort"]},
        "instructions": protocol["instructions"],
        "input": [{"role": "user", "content": material + "\n\n" + protocol["task"]}],
    }


def reserve_usd(payload):
    # UTF-8 byte count overbounds ordinary text tokens; add framing headroom.
    # Reserve at 2x Standard with no cache discount, including all output tokens.
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    if size > 256_000:
        raise ValueError("pilot input exceeds the fixed 256 KB limit")
    inp, _, output = RATES[payload["model"]]
    return round(2 * ((size + 4096) * inp + payload["max_output_tokens"] * output) / 1e6, 6)


def connection():
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    config = tomllib.loads((home / "config.toml").read_text())
    name = config.get("model_provider", "openai")
    provider = config.get("model_providers", {}).get(name, {})
    if provider.get("wire_api", "responses") != "responses":
        raise ValueError("pilot requires a Responses-compatible provider")
    key = os.environ.get(provider.get("env_key", "OPENAI_API_KEY"))
    if not key:
        raise ValueError("configured provider API credential is not available in the environment")
    url = provider.get("base_url", "https://api.openai.com/v1").rstrip("/") + "/responses"
    return url, key, name


def request_response(url, key, payload, output):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                 "User-Agent": "driver-port-factory-context-pilot/1"}, method="POST")
    started = time.monotonic()
    final = None
    with urllib.request.urlopen(request, timeout=120) as response, output.open("w") as stream:
        for raw in response:
            if time.monotonic() - started > 180:
                raise TimeoutError("pilot call exceeded its deadline")
            if not raw.startswith(b"data: ") or raw.strip() == b"data: [DONE]":
                continue
            event = json.loads(raw[6:])
            stream.write(json.dumps(event) + "\n")
            stream.flush()
            terminal = {"response.completed", "response.incomplete", "response.failed"}
            if event.get("type") in terminal:
                final = event["response"]
    if final is None:
        raise ValueError("provider stream has no terminal response; usage remains unknown")
    return final


def response_text(events_path):
    """Some relays omit output in the terminal object; preserve streamed text."""
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    return "".join(event.get("delta", "") for event in events
                   if event.get("type") == "response.output_text.delta")


def run_call(directory, protocol, index, arm, *, budget):
    if protocol.get("execution_blocked"):
        raise ValueError("pilot protocol blocks execution: " + protocol["execution_blocked"])
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("pilot budget must be finite and positive")
    payload = payload_for(protocol, directory, arm)
    prefix = directory / f"call-{index:02d}-{arm}"
    metrics_path = prefix.with_suffix(".metrics.json")
    protocol_digest = hashlib.sha256((directory / "protocol.json").read_bytes()).hexdigest()
    if metrics_path.exists():
        if json.loads(metrics_path.read_text())["protocol_sha256"] != protocol_digest:
            raise ValueError("protocol changed after a recorded call; use a new pilot directory")
        return  # A failed/unfinished attempt remains charged against the reserve.
    spent = sum(json.loads(path.read_text())["reserved_usd"]
                for path in directory.glob("call-*.metrics.json"))
    for path in directory.glob("call-*.metrics.json"):
        prior = json.loads(path.read_text())
        if prior.get("stop_reason") or prior["state"] != "COMPLETED" or prior["estimate"] is None:
            raise ValueError("prior uncertain call prevents further provider access")
    reserve = reserve_usd(payload)
    if spent + reserve > budget:
        raise ValueError("pilot conservative reservation would exceed the authorized budget")
    url, key, provider = connection()
    metrics = {"arm": arm, "index": index, "reserved_usd": reserve, "state": "RUNNING",
               "model": payload["model"], "provider": provider, "usage": None, "estimate": None,
               "protocol_sha256": protocol_digest}
    write_json(prefix.with_suffix(".request.json"), payload)
    write_json(metrics_path, metrics)
    started = time.monotonic()
    try:
        final = request_response(url, key, payload, prefix.with_suffix(".events.jsonl"))
        write_json(prefix.with_suffix(".response.json"), final)
        prefix.with_suffix(".answer.txt").write_text(
            response_text(prefix.with_suffix(".events.jsonl")))
        metrics["requested_max_output_tokens"] = payload["max_output_tokens"]
        metrics["reported_max_output_tokens"] = final.get("max_output_tokens")
        raw = final.get("usage")
        if raw:
            reported = {
                "input_tokens": raw["input_tokens"], "output_tokens": raw["output_tokens"],
                "cached_input_tokens": raw.get("input_tokens_details", {}).get("cached_tokens"),
            }
            metrics["usage"] = usage_delta(reported, dict.fromkeys(FIELDS, 0))
            metrics["estimate"] = estimate(metrics["usage"], payload["model"],
                                           final.get("service_tier") or "default")
        metrics["served_model"] = final.get("model")
        metrics["state"] = final.get("status", "unknown").upper()
        metrics["limit_policy"] = protocol.get("limit_policy", "confirmed")
        metrics["limit_warning"] = response_stop_reason(final, payload)
        metrics["stop_reason"] = experiment_stop_reason(final, payload, protocol)
    except Exception as error:
        # Provider bodies and URLs may contain sensitive operational details.
        metrics["state"] = "FAILED"
        metrics["error_type"] = type(error).__name__
        if isinstance(error, urllib.error.HTTPError):
            metrics["http_status"] = error.code
        raise
    finally:
        metrics["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(metrics_path, metrics)
        print(json.dumps(metrics), flush=True)


def response_stop_reason(final, payload):
    actual = (final.get("usage") or {}).get("output_tokens")
    if type(actual) is int and actual > payload["max_output_tokens"]:
        return "provider_exceeded_output_cap"
    if final.get("model") != payload["model"]:
        return "provider_model_identity_mismatch"
    if final.get("max_output_tokens") != payload["max_output_tokens"]:
        return "provider_did_not_confirm_output_cap"
    return None


def experiment_stop_reason(final, payload, protocol):
    reason = response_stop_reason(final, payload)
    # Explicit protocol authorization only relaxes absent echo, never observed
    # overruns, model substitution, missing usage or failed/incomplete responses.
    if (protocol.get("limit_policy") == "observe_actual_usage"
            and reason == "provider_did_not_confirm_output_cap"):
        return None
    return reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--budget-usd", type=float, default=0)
    args = parser.parse_args()
    if args.execute and (not math.isfinite(args.budget_usd) or args.budget_usd <= 0):
        raise ValueError("pilot budget must be finite and positive")
    protocol = load_protocol(args.directory / "protocol.json")
    expected = sum(reserve_usd(payload_for(protocol, args.directory, arm))
                   for arm in protocol["order"])
    print(json.dumps({"planned_calls": len(protocol["order"]), "reserved_usd": expected,
                      "execute": args.execute}), flush=True)
    if not args.execute:
        return
    if protocol.get("execution_blocked"):
        raise ValueError("pilot protocol blocks execution: " + protocol["execution_blocked"])
    if expected > args.budget_usd or args.budget_usd > protocol["authorized_budget_usd"]:
        raise ValueError("requested plan does not fit the frozen authorized budget")
    with (args.directory / ".pilot.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for index, arm in enumerate(protocol["order"]):
            run_call(args.directory, protocol, index, arm, budget=args.budget_usd)
            saved = args.directory / f"call-{index:02d}-{arm}.metrics.json"
            metrics = json.loads(saved.read_text())
            if (metrics.get("stop_reason") or metrics["state"] != "COMPLETED"
                    or metrics["estimate"] is None):
                raise ValueError("pilot stopped: uncertain provider limits, outcome, or usage")


if __name__ == "__main__":
    main()
