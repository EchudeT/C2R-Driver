"""Blocking worker tools over the existing experiment and probe executors.

Results describe execution, never approve a stage or accept uploaded PASS evidence.
The MCP process owns its commands; the existing cross-process experiment lock
serializes it with CLI and controller capture.
"""

import json
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..core.models import WorkflowError
from . import experiments
from .experiment_ack import _job, record_seen


def tools():
    return [
        {
            "name": "check",
            "description": (
                "Run a managed experiment and wait for its result; no polling. "
                "Choose registered cases, the full suite, or a worktree script. "
                "Build/format scripts are development feedback only. "
                "A passed case is retained for this project; fresh does not rerun it. "
                "Read returned raw logs as needed. "
                "Do not edit/build concurrently. This tool never grants stage PASS."
            ),
            "inputSchema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "level": {"type": "string", "enum": ["runtime", "development"]},
                    "script": {"type": "string"},
                    "cases": {"type": "array", "items": {"type": "string"}},
                    "timeout": {"type": "integer", "minimum": 1, "maximum": 86400},
                    "fresh": {"type": "boolean"},
                },
            },
        }
    ]


def _arguments(arguments):
    if not isinstance(arguments, dict) or set(arguments) - {
        "level",
        "script",
        "cases",
        "timeout",
        "fresh",
    }:
        raise WorkflowError("Use level, script or cases, timeout and fresh")
    level = arguments.get("level", "runtime")
    if level not in {"runtime", "development"}:
        raise WorkflowError("Unknown check level")
    timeout = arguments.get("timeout", 3600 if level == "runtime" else 120)
    if type(timeout) is not int or not 1 <= timeout <= 86400:
        raise WorkflowError("Check timeout must be an integer between 1 and 86400")
    fresh = arguments.get("fresh", False)
    if type(fresh) is not bool:
        raise WorkflowError("fresh must be a boolean")
    names = arguments.get("cases", [])
    if (
        not isinstance(names, list)
        or any(not isinstance(n, str) or not n for n in names)
        or len(names) != len(set(names))
    ):
        raise WorkflowError("cases must contain unique registered case IDs")
    script = arguments.get("script")
    if script is not None and (not isinstance(script, str) or not script):
        raise WorkflowError("script must be a nonempty worktree path")
    if script and names:
        raise WorkflowError("Choose script or cases, not both")
    if level == "development" and (not script or names or timeout > 3600):
        raise WorkflowError("Development checks require a script and timeout <= 3600")
    return level, timeout, fresh, names, script


def _file(worktree, name):
    path = (worktree / name).resolve()
    if not path.is_relative_to(worktree.resolve()) or not path.is_file():
        raise WorkflowError(f"Check input must be a regular worktree file: {name}")
    return path


def _selected(worktree, names, script, timeout):
    if script:
        return [{"id": Path(script).name, "script": script, "timeout_seconds": timeout}]
    cases = experiments.cases(worktree)
    if cases is None:
        if names:
            raise WorkflowError("No registered case manifest")
        return [
            {
                "id": "public-qemu.sh",
                "script": ".dpf-output/public-qemu.sh",
                "timeout_seconds": timeout,
            }
        ]
    known = {c["id"] for c in cases}
    if set(names) - known:
        raise WorkflowError("Unknown experiment case IDs: " + ", ".join(sorted(set(names) - known)))
    return [c for c in cases if not names or c["id"] in names]


def feedback(results):
    lines = ["Managed execution observations (not semantic acceptance):"]
    for item in results:
        command = item["observation"].command
        receipt = item["observation"].trace_path.parent / "receipt.json"
        lines.extend(
            [
                f"\n{item['id']}: {item['status']}; reused={item['reused']}",
                (
                    f"exit={command.exit_code}; timeout={command.timed_out}; "
                    f"launched={command.launched}"
                ),
                f"receipt: {receipt}",
                f"stdout: {command.stdout_path}",
                f"stderr: {command.stderr_path}",
            ]
        )
        if item["reused"]:
            lines.append("Retained earlier PASS under operator policy; original receipt/version "
                         "applies. No new execution or claim about later changes.")
        for log in item["observation"].logs[:3]:
            lines.append("observation: " + str(log.get("archive_path") or log.get("path", log)))
        if len(item["observation"].logs) > 3:
            lines.append("All observation paths are in the receipt; additional paths omitted here.")
    lines.append("Inspect actual assertions and limits; a selected case is not the entire suite.")
    return "\n".join(lines)


def development_feedback(value):
    """Keep full diagnostics in the receipt, not in every subsequent model context."""
    summary = {key: value[key] for key in ("status", "receipt", "same_input_prior_runs")}
    summary["feedback"] = []
    for item in value["feedback"]:
        row = {key: item[key] for key in ("role", "category", "exit_code")}
        row["outputs"] = [{"path": output["path"]} for output in item["outputs"]]
        if value["status"] != "COMMAND_OK":
            row["diagnostics"] = item["diagnostics"][:3]
            for target, output in zip(row["outputs"], item["outputs"], strict=True):
                target["tail"] = output["tail"][-600:]
        summary["feedback"].append(row)
    return (
        "Development observation; no runtime or stage PASS.\n"
        + json.dumps(summary)
        + (
            "\nFull diagnostics and input identity remain in the receipt. "
            "Inspect the relevant failure before retrying; passing checks need no log reread."
        )
    )


def check(project, job_id, arguments):
    _job(project, job_id)
    level, timeout, fresh, names, script = _arguments(arguments)
    target = load_repository_acquisition(project).target_worktree
    worktree = project.root / target.path
    if level == "development":
        from .probes import run

        value = run(project, str(_file(worktree, script)), timeout=timeout)
        return development_feedback(value)
    selected = _selected(worktree, names, script, timeout)
    runtime = _file(worktree, ".dpf-output/runtime-artifact")
    # Validate all selections before launching any command.
    paths = [_file(worktree, case["script"]) for case in selected]
    results = []
    for case, path in zip(selected, paths, strict=True):
        observed = experiments.execute(
            project,
            worktree=worktree,
            script_path=path,
            runtime_path=runtime,
            timeout_seconds=case.get("timeout_seconds", timeout),
            dependencies=case.get("dependencies"),
            environment=case.get("environment"),
            force=fresh,
            case_id=case["id"],
        )
        results.append(
            {
                "id": case["id"],
                "status": "PASS" if observed.passed else "FAIL",
                "reused": observed.reused,
                "observation": observed,
            }
        )
    record_seen(project, job_id, results)
    return feedback(results)
