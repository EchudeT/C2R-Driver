"""Read frozen experiment 04 only. No execution or model invocation."""

import collections
import json
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[3]
RUN = REPOSITORY.parent / "experiments/pvpanic-behavior-sol-medium-20261003-04/run"
metrics = json.loads(
    next((RUN / ".dpf/codex").glob("driver_implementation*.metrics.json")).read_text()
)
events = next((RUN / ".dpf/codex").glob("driver_implementation*.events.jsonl"))
items = [
    e["item"]
    for line in events.read_text().splitlines()
    if (e := json.loads(line))["type"] == "item.completed"
]
first_edit = next(n for n, item in enumerate(items) if item["type"] == "file_change")
checks = [i for i in items if i.get("tool") == "check"]
result = {
    "run": RUN.parent.name,
    "status": "STOPPED_INCOMPLETE",
    "usage": metrics["usage"],
    "estimate": metrics["estimate"],
    "elapsed_seconds": metrics["elapsed_seconds"],
    "prompt_bytes": metrics["prompt_bytes"],
    "completed_items": dict(collections.Counter(i["type"] for i in items)),
    "commands_before_first_source_edit": sum(
        i["type"] == "command_execution" for i in items[:first_edit]
    ),
    "command_output_characters": sum(len(i.get("aggregated_output", "")) for i in items),
    "checks_by_script": dict(collections.Counter(i["arguments"].get("script") for i in checks)),
    "limitations": (
        "Observed totals, not per-cause dollar attribution. "
        "Some repeated checks followed real source edits. No new optimized driver run."
    ),
}
Path(__file__).with_name("observed.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
