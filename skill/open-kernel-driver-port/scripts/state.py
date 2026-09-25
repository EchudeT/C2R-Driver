#!/usr/bin/env python3
"""Small deterministic checkpoint/state helper for the driver-port workflow."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PHASES = [
    "intake", "acquisition", "environment", "knowledge", "target-study",
    "source-closure", "contracts", "tests", "translation", "artifact",
    "qemu", "audit",
]


def state_path(workspace: str) -> Path:
    return Path(workspace).expanduser().resolve() / "state" / "driver-port.json"


def load(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {
            "schema": 1, "phase": "intake", "status": "NOT_STARTED",
            "completed": [], "decisions": {}, "pending_question": None,
            "evidence_ids": [],
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1:
        raise ValueError(f"unsupported state schema: {value.get('schema')}")
    return value


def save(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    value["updated_at"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    advance = sub.add_parser("advance")
    advance.add_argument("phase", choices=PHASES)
    advance.add_argument("--status", default="IN_PROGRESS")
    checkpoint = sub.add_parser("checkpoint")
    checkpoint.add_argument("key")
    checkpoint.add_argument("value")
    question = sub.add_parser("question")
    question.add_argument("text")
    question.add_argument("--key", required=True)
    answer = sub.add_parser("answer")
    answer.add_argument("--key", required=True)
    answer.add_argument("value")
    evidence = sub.add_parser("evidence")
    evidence.add_argument("ids", nargs="+")
    args = parser.parse_args()

    path = state_path(args.workspace)
    try:
        value = load(path)
        if args.command == "status":
            value["next_phase"] = next(
                (phase for phase in PHASES if phase not in value["completed"]),
                "audit",
            )
            print(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False))
            return 0
        if args.command == "advance":
            value["phase"] = args.phase
            value["status"] = args.status
            if args.status in {"PASS", "COMPLETE"} and args.phase not in value["completed"]:
                value["completed"].append(args.phase)
        elif args.command == "checkpoint":
            value.setdefault("decisions", {})[args.key] = args.value
        elif args.command == "question":
            pending = value.get("pending_question")
            if pending and pending.get("key") == args.key and pending.get("text") == args.text:
                print(json.dumps({"status": "ALREADY_ISSUED", **pending}, ensure_ascii=False))
                return 0
            value["pending_question"] = {"key": args.key, "text": args.text}
        elif args.command == "answer":
            pending = value.get("pending_question")
            if not pending or pending.get("key") != args.key:
                raise ValueError(f"no matching pending question: {args.key}")
            value.setdefault("decisions", {})[args.key] = args.value
            value["pending_question"] = None
        elif args.command == "evidence":
            existing = value.setdefault("evidence_ids", [])
            for item in args.ids:
                if item not in existing:
                    existing.append(item)
        save(path, value)
        print(json.dumps({"status": "SAVED", "path": str(path), **value},
                         indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
