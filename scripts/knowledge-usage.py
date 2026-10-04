#!/usr/bin/env python3
"""Read-only retrieval navigation audit. Command counts do not establish adoption or savings."""

import argparse
import collections
import json
import re
from pathlib import Path

COMMAND = re.compile(
    r"driver_port_factory\.cli\s+knowledge\s+"
    r"(search-batch|shared-search|search|rag|show|packet|check-probes)\b"
)


def audit(root):
    rows = []
    counts = collections.defaultdict(collections.Counter)
    for path in sorted((root / ".dpf/codex").glob("*.events.jsonl")):
        stage = path.name.rsplit("-", 5)[0]
        metrics_path = path.with_name(path.name.replace(".events.jsonl", ".metrics.json"))
        if metrics_path.is_file():
            stage = json.loads(metrics_path.read_text()).get("stage", stage)
        counts[stage]  # Include stages with zero observed retrieval commands.
        for number, line in enumerate(path.read_text().splitlines(), 1):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue  # A read-only live snapshot can end inside a JSONL write.
            item = event.get("item", {})
            if event.get("type") != "item.completed" or item.get("type") != "command_execution":
                continue
            command = item.get("command", "")
            matches = COMMAND.findall(command)
            if not matches:
                continue
            help_only = "--help" in command
            for name in matches:
                counts[stage][name + (":help" if help_only else ":command")] += 1
            rows.append(
                {
                    "stage": stage,
                    "events": str(path),
                    "line": number,
                    "matched_commands": matches,
                    "help_present": help_only,
                    "exit_code": item.get("exit_code"),
                    "command": command,
                    "output_excerpt": item.get("aggregated_output", "")[:1200],
                    "adoption": "NOT_ASSESSED",
                }
            )
    return {
        "scope": "Completed command matches and event locations; adoption not assessed",
        "limits": "Counts include compound command text and cannot prove each subcommand ran, "
        "that evidence was read or used, or that cost was saved. Shell helpers, MCP retrieval and "
        "direct report/original reads are not counted. Inspect cited outputs and implementation "
        "decisions for adoption. Zero here does not prove no knowledge reuse.",
        "stages": {stage: dict(value) for stage, value in counts.items()},
        "commands": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    args = parser.parse_args()
    if not (args.workspace / ".dpf/codex").is_dir():
        parser.error("workspace has no .dpf/codex event directory")
    print(json.dumps(audit(args.workspace), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
