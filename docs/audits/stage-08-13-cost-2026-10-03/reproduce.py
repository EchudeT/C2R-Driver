"""Offline accounting audit; reads observations without rerunning the experiment."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path


def cost(usage):
    cached = usage["cached_input_tokens"]
    return {
        "uncached_input": (usage["input_tokens"] - cached) * 4 / 1e6,
        "cached_input": cached * 0.4 / 1e6,
        "output": usage["output_tokens"] * 20 / 1e6,
    }


def audit(root, stage):
    metrics_path = next((root / "run/.dpf/codex").glob(stage + "-*.metrics.json"))
    metrics = json.loads(metrics_path.read_text())
    rollout = next((root / "model-home/sessions").rglob("*" + metrics["thread_id"] + ".jsonl"))
    start = datetime.fromisoformat(metrics["started_at"])
    end = datetime.fromisoformat(metrics["completed_at"])
    requests, pending = [], []
    compactions = 0
    for line in rollout.open():
        row = json.loads(line)
        if not start <= datetime.fromisoformat(row["timestamp"]) <= end:
            continue
        payload = row.get("payload", {})
        if row["type"] == "compacted":
            compactions += 1
        # Only public tool actions, never reasoning messages or private summaries.
        if row["type"] == "response_item" and payload.get("type") in {
            "custom_tool_call",
            "function_call",
        }:
            pending.append(
                {
                    "name": payload.get("name"),
                    "input": payload.get("input", payload.get("arguments", "")),
                }
            )
        if row["type"] != "token_usage_record":
            continue
        usage = payload["usage"]
        actions = "\n".join(p["input"] for p in pending)
        polling = "tools.write_stdin(" in actions and all(
            forbidden not in actions
            for forbidden in ("tools.exec_command(", "tools.apply_patch(", "tools.mcp__")
        )
        parts = cost(usage)
        requests.append(
            {
                "index": len(requests) + 1,
                "timestamp": row["timestamp"],
                "response_id": payload.get("response_id"),
                "usage": usage,
                "cost_usd": parts,
                "estimated_usd": sum(parts.values()),
                "polling_only": polling,
                "actions": pending,
            }
        )
        pending = []
    observed = {
        key: sum(r["usage"][key] for r in requests)
        for key in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    assert observed == {key: metrics["usage"][key] for key in observed}
    total = sum(r["estimated_usd"] for r in requests)
    assert abs(total - metrics["estimate"]["usd"]) < 1e-8
    assert len({r["response_id"] for r in requests}) == len(requests)
    polls = [r for r in requests if r["polling_only"]]
    return {
        "stage": stage,
        "metrics": str(metrics_path),
        "metrics_sha256": hashlib.sha256(metrics_path.read_bytes()).hexdigest(),
        "rollout": str(rollout),
        "elapsed_seconds": metrics["elapsed_seconds"],
        "resumed": metrics["resumed"],
        "compactions": compactions,
        "usage": observed,
        "cost_usd": cost(observed),
        "estimated_usd": total,
        "request_count": len(requests),
        "first_input_tokens": requests[0]["usage"]["input_tokens"],
        "last_input_tokens": requests[-1]["usage"]["input_tokens"],
        "polling_count": len(polls),
        "polling_estimated_usd": sum(r["estimated_usd"] for r in polls),
        "polling_indices": [r["index"] for r in polls],
        "requests": requests,
    }


def receipts(root):
    base = root / "run/work/target/.dpf-output"
    results = []
    for path in sorted(base.rglob("result.json")):
        if path.parent.parent.name != "command":
            continue
        data = json.loads(path.read_text())
        results.append(
            {
                "path": str(path.relative_to(root)),
                **{
                    key: data[key]
                    for key in ("started_at", "duration_milliseconds", "exit_code", "argv")
                },
            }
        )
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("experiment", type=Path)
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("usage.json"))
    args = parser.parse_args()
    root = args.experiment.resolve()
    runs = [audit(root, stage) for stage in ("environment_recovery", "driver_implementation")]
    result = {
        "method": "One token_usage_record per API request; sums verified against stage metrics.",
        "limits": (
            "Frozen repository rates: 4/0.4/20 USD per million uncached/cache/output tokens; "
            "not provider invoice. Tool actions identify the request output, not exclusive "
            "causal cost of that action. Polling cost and low-cache cost overlap. "
            "No counterfactual cost-saving experiment."
        ),
        "runs": runs,
        "command_receipts": receipts(root),
    }
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            [
                {key: value for key, value in run.items() if key not in {"requests", "rollout"}}
                for run in runs
            ],
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
