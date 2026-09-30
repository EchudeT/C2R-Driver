#!/usr/bin/env python3
"""Offline run audit. Emit aggregates, never raw prompts or native tool arguments."""

import argparse
import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from driver_port_factory.codex.accounting import read_jobs
from driver_port_factory.codex.context_logs import inspect_snapshot, verified_chunks
from driver_port_factory.composition import open_project
from driver_port_factory.control.statistics import project_statistics


def native_records(root, snapshot):
    pending = b""
    for chunk in verified_chunks(root, snapshot):
        lines = (pending + chunk).split(b"\n")
        pending = lines.pop()
        for line in lines:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                yield value


def record_time(record):
    try:
        return datetime.fromisoformat(record.get("timestamp", "").replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def output_text(payload):
    output = payload.get("output", "")
    if isinstance(output, str):
        return output
    return "\n".join(part.get("text", "") for part in output if isinstance(part, dict))


def interaction_audit(records, jobs):
    """Attribute outer calls by sidecar intervals; never interpret hidden reasoning."""
    outputs = {r["payload"].get("call_id"): output_text(r["payload"]) for r in records
               if r.get("type") == "response_item" and r["payload"].get("type") in
               {"custom_tool_call_output", "function_call_output"}}
    calls = [(record_time(r), r["payload"]) for r in records
             if r.get("type") == "response_item" and r["payload"].get("type") in
             {"custom_tool_call", "function_call"}]
    result = []
    for job in jobs:
        if not job.get("started_at") or not job.get("completed_at"):
            continue  # Do not silently assign overlapping open intervals.
        start = datetime.fromisoformat(job["started_at"])
        end = datetime.fromisoformat(job["completed_at"])
        selected = [payload for stamp, payload in calls if stamp and start <= stamp <= end]
        counts = Counter()
        for payload in selected:
            value = payload.get("input", payload.get("arguments", ""))
            output = outputs.get(payload.get("call_id"), "")
            counts["tool_calls"] += 1
            counts["output_characters"] += len(output)
            counts["truncated_outputs"] += "Warning: truncated output" in output
            counts["unmatched_outputs"] += payload.get("call_id") not in outputs
            if "tools.write_stdin(" in value:
                counts["poll_wrappers"] += 1
                hints = re.findall(r'yield_time_ms[\"\']?\s*:\s*(\d+)', value)
                counts["poll_wrappers_requesting_under_5s"] += any(int(h) < 5000 for h in hints)
        result.append({"job_id": job.get("job_id"), "stage": job.get("stage"), **counts})
    return result


def native_audit(path, start, end):
    """Use the same outer-call metrics for a bounded standalone Skill session."""
    start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    if not start_time.tzinfo or not end_time.tzinfo or start_time > end_time:
        raise ValueError("start/end must be ordered timestamps with timezones")
    records, compactions, invalid_lines = [], 0, 0
    calls = set()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for line in stream:
            digest.update(line)
            try:
                record = json.loads(line)
            except ValueError:
                invalid_lines += 1
                continue
            if not isinstance(record, dict):
                invalid_lines += 1
                continue
            stamp = record_time(record)
            inside = bool(stamp and stamp.tzinfo and start_time <= stamp <= end_time)
            if inside and record.get("type") == "compacted":
                compactions += 1
            payload = record.get("payload") or {}
            if record.get("type") != "response_item" or not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if inside and kind in {"function_call", "custom_tool_call"}:
                calls.add(payload.get("call_id"))
                records.append(record)
            elif (kind in {"function_call_output", "custom_tool_call_output"}
                  and payload.get("call_id") in calls):
                records.append(record)  # Include delayed output for a selected call.
    interactions = interaction_audit(records, [{"job_id": "native_interval", "stage": None,
        "started_at": start_time.isoformat(), "completed_at": end_time.isoformat()}])[0]
    return {"schema_version": 1, "source_sha256": digest.hexdigest(),
            "start": start_time.isoformat(), "end": end_time.isoformat(),
            "interactions": interactions, "compactions": compactions,
            "invalid_lines": invalid_lines,
            "limitations": ["Outer calls only, using the same metrics as factory sidecar intervals. "
                "Output characters are not tokens or costs. Short requested waits are not measured "
                "waste. Hidden reasoning and message contents are not inspected or exported. "
                "This interval does not remove the benefit of preexisting session context."]}


def thread_summary(root, thread, snapshot):
    verified = inspect_snapshot(root, snapshot)
    tools, calls = Counter(), Counter()
    for record in native_records(root, snapshot):
        payload = record.get("payload") or {}
        if (record.get("type") == "response_item" and isinstance(payload, dict)
                and payload.get("type") in {"function_call", "custom_tool_call"}):
            name = payload.get("name", "unknown")
            tools[name] += 1
            args = payload.get("arguments", payload.get("input"))
            # Hash internally for counting only; do not export arguments or their hashes.
            digest = hashlib.sha256(json.dumps([name, args], sort_keys=True).encode()).hexdigest()
            calls[digest] += 1
    return {"thread_id": thread, "bytes": snapshot["bytes"], "sha256": snapshot["sha256"],
            "captured_at": snapshot["captured_at"], **verified, "tool_calls": dict(tools),
            "exact_repeated_calls": sum(count - 1 for count in calls.values()),
            "repeat_note": "Exact outer call repeats may be polls or necessary rechecks, "
                           "not proven waste. Nested exec calls are not separately counted."}


def audit(root):
    project = open_project(root, read_only=True, verify_artifacts=False)
    statistics = project_statistics(project)
    log_root = project.control / "codex/context-logs"
    latest, manifests, errors = {}, [], []
    for path in sorted(log_root.glob("*.json")):
        try:
            record = json.loads(path.read_text())
            snapshots = record["snapshots"]
            manifests.append({"job_id": record["job_id"], "status": record["status"],
                              "snapshots": len(snapshots)})
            thread = record.get("thread_id")
            if thread and snapshots:
                snapshot = snapshots[-1]
                if snapshot["captured_at"] > latest.get(thread, {}).get("captured_at", ""):
                    latest[thread] = snapshot
        except (OSError, ValueError, KeyError, TypeError):
            errors.append({"manifest": path.name, "status": "unreadable"})
    threads = []
    records_by_thread = {}
    for thread, snapshot in latest.items():
        try:
            records = list(native_records(log_root, snapshot))
            records_by_thread[thread] = records
            threads.append(thread_summary(log_root, thread, snapshot))
        except (OSError, ValueError, KeyError, TypeError):
            errors.append({"thread_id": thread, "status": "snapshot_audit_failed"})
    fields = ("job_id", "stage", "call_reason", "elapsed_seconds", "thread_id",
              "context_policy", "context_action", "context_epoch", "usage", "estimate")
    jobs = [{key: job.get(key) for key in fields} for job in statistics["jobs"]]
    with_manifests = {entry["job_id"] for entry in manifests}
    jobs_with_time = read_jobs(project.control / "codex")
    interactions = []
    for thread, records in records_by_thread.items():
        interactions.extend(interaction_audit(records, [
            job for job in jobs_with_time if job.get("thread_id") == thread]))
    return {"schema_version": 1, "run": str(root.resolve()),
            "totals": statistics["totals"], "stages": statistics["stages"],
            "by_call_reason": statistics["by_call_reason"], "jobs": jobs,
            "context_actions": dict(Counter(job["context_action"] for job in jobs)),
            "context_decisions": dict(Counter(
                (job["context_policy"] or {}).get("reason", "unrecorded") for job in jobs)),
            "manifests": manifests, "threads": threads, "errors": errors,
            "interactions": interactions,
            "jobs_without_manifest": [j["job_id"] for j in jobs
                                      if j["job_id"] not in with_manifests],
            "evidence": statistics["evidence"],
            "limitations": [statistics["note"],
                            ("Interaction counts use completed sidecar time intervals and match "
                             "outer tool call IDs. Character counts are not tokens; polling "
                             "hints are requested waits, not measured latency or wasted cost. "
                             "Truncation counts recognize the retained warning text only."),
                            ("One latest retained snapshot per thread; cumulative snapshots "
                             "are never summed. Missing logs mean unknown, not zero."),
                            ("No causal cost/quality conclusion or automatic rejection. "
                             "No provider calls, run mutations, prompts or tool output exported.")]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="?")
    parser.add_argument("--native-log", type=Path)
    parser.add_argument("--start", help="Inclusive ISO timestamp with timezone")
    parser.add_argument("--end", help="Inclusive ISO timestamp with timezone")
    args = parser.parse_args()
    if args.native_log and not args.run and args.start and args.end:
        try:
            result = native_audit(args.native_log, args.start, args.end)
        except ValueError as error:
            parser.error(str(error))
    elif args.run and not any((args.native_log, args.start, args.end)):
        result = audit(args.run)
    else:
        parser.error("provide RUN, or --native-log PATH --start TIME --end TIME")
    print(json.dumps(result, ensure_ascii=False, indent=2))
