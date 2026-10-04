import collections
import hashlib
import json
from pathlib import Path

base = Path("/home/unix/file/C2R-Driver/c2rust-migration-test-01")
destination = base / "driver-port-factory/docs/audits/target-study-cost-2026-10-03"
results = []
for number in ("04", "05", "06"):
    root = base / f"experiments/pvpanic-behavior-sol-medium-20261003-{number}"
    metrics_path = next((root / "run/.dpf/codex").glob("target_platform_study-*.metrics.json"))
    metrics = json.loads(metrics_path.read_text())
    event_path = metrics_path.with_name(metrics_path.name.replace(".metrics.json", ".events.jsonl"))
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    commands = [
        e["item"]
        for e in events
        if e.get("type") == "item.completed"
        and e.get("item", {}).get("type") == "command_execution"
    ]
    rollout = next((root / "model-home/sessions").rglob("*" + metrics["thread_id"] + ".jsonl"))
    requests, compactions, truncations = [], [], []
    for line in rollout.read_text().splitlines():
        row = json.loads(line)
        stamp, payload = row.get("timestamp", ""), row.get("payload", {})
        if not metrics["started_at"][:19] <= stamp <= metrics["completed_at"][:19] + "Z":
            continue
        if row["type"] == "token_usage_record":
            usage = payload["usage"]
            uncached = usage["input_tokens"] - usage["cached_input_tokens"]
            cost = (
                uncached * 4 + usage["cached_input_tokens"] * 0.4 + usage["output_tokens"] * 20
            ) / 1e6
            requests.append({"timestamp": stamp, "usage": usage, "uncached": uncached, "usd": cost})
        elif row["type"] == "compacted":
            compactions.append(stamp)
        elif row["type"] == "response_item" and payload.get("type") == "custom_tool_call_output":
            blocks = payload.get("output", [])
            if isinstance(blocks, str):
                blocks = json.loads(blocks)
            text = "\n".join(b.get("text", "") for b in blocks if isinstance(b, dict))
            if "Warning: truncated output" in text:
                truncations.append(stamp)
    observed = {
        field: sum(r["usage"][field] for r in requests)
        for field in ("input_tokens", "cached_input_tokens", "output_tokens")
    }
    assert observed == {field: metrics["usage"][field] for field in observed}
    assert abs(sum(r["usd"] for r in requests) - metrics["estimate"]["usd"]) < 1e-8
    groups = collections.Counter()
    for command in commands:
        text = command.get("command", "")
        group = (
            "knowledge_search"
            if " knowledge search " in text
            else "probe_replay"
            if " knowledge check-probes " in text
            else "other"
        )
        groups[group] += len(command.get("aggregated_output", ""))
    results.append(
        {
            "run": root.name,
            "metrics": str(metrics_path),
            "metrics_sha256": hashlib.sha256(metrics_path.read_bytes()).hexdigest(),
            "elapsed_seconds": metrics["elapsed_seconds"],
            "prompt_bytes": metrics["prompt_bytes"],
            "usage": metrics["usage"],
            "estimated_usd": metrics["estimate"]["usd"],
            "command_count": len(commands),
            "raw_command_output_characters": dict(groups),
            "compactions": compactions,
            "truncated_tool_responses": truncations,
            "requests": requests,
            "reports": [
                {"file": str(p), "bytes": p.stat().st_size}
                for p in (root / "run/work/stage-work/target_platform_study").glob("*.md")
            ],
        }
    )
output = {
    "method": (
        "Read-only local audit; no models, project mutation, retrieval rerun "
        "or experiment intervention."
    ),
    "limits": (
        "Repository rates 4/0.4/20 USD per million; not relay invoice. "
        "Token usage records count API requests once. "
        "Raw shell bytes are not model-visible token counts. No causal savings experiment."
    ),
    "runs": results,
}
(destination / "usage.json").write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
print(
    json.dumps(
        [
            {k: r[k] for k in ("run", "estimated_usd", "command_count", "compactions")}
            for r in results
        ]
    )
)
