"""Single awaited platform operation per worker request, backed by the normal executor."""

import json
import uuid
from pathlib import Path

from ..core.models import StageStatus, WorkflowError
from . import activity, formatting, service

DELIVERY = {
    "target_framework_enablement",
    "driver_implementation",
    "artifact_preparation",
    "public_qemu_validation",
}
STAGES = DELIVERY | {"environment_recovery", "target_platform_study", "migration_contracts"}
FIELDS = {
    "bootstrap": {"image", "accelerator", "probe"},
    "build": set(),
    "format": {"packages", "write"},
    "run_case": {"case"},
    "integration": {"package"},
}


def tool():
    return {
        "name": "platform",
        "description": (
            "Execute one platform operation and WAIT for the final result. "
            "No shell launch, session ID or write_stdin polling. "
            "bootstrap: image, accelerator, probe; build: no arguments; "
            "format: packages, optional write; run_case: worktree case JSON path. "
            "integration: optional existing package, returns source-backed wiring on demand. "
            "Use the pinned environment; never edit concurrently. Results are observations, "
            "not semantic or stage acceptance. Logs remain available in the monitor."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": list(FIELDS)},
                "image": {"type": "string"},
                "accelerator": {"type": "string", "enum": ["kvm", "tcg"]},
                "probe": {"type": "string"},
                "packages": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "write": {"type": "boolean"},
                "case": {"type": "string"},
                "package": {"type": "string"},
            },
        },
    }


def authorize(project, job_id):
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("noncanonical")
    except (ValueError, TypeError, AttributeError) as error:
        raise WorkflowError("Invalid platform job identity") from error
    paths = list((project.control / "codex").glob(f"*-{job_id}.metrics.json"))
    if len(paths) != 1:
        raise WorkflowError("Platform tool requires the active job identity")
    job = json.loads(paths[0].read_text())
    stage = job.get("stage")
    if (
        job.get("invocation_state") != "RUNNING"
        or stage not in STAGES
        or not any(
            s.name.value == stage and s.status is StageStatus.RUNNING for s in project.stages()
        )
    ):
        raise WorkflowError("Platform job or its stage is not running")
    service.active(project, environment=stage == "environment_recovery")
    if not service.required(project):
        raise WorkflowError("No managed platform configured; use the frozen route")
    return stage


def arguments(value):
    if not isinstance(value, dict) or not isinstance(value.get("action"), str):
        raise WorkflowError("Platform request requires an action")
    action = value["action"]
    if action not in FIELDS or set(value) - {"action"} - FIELDS[action]:
        raise WorkflowError("Unsupported platform action or arguments")
    required = FIELDS[action] - {"write", "package"}
    if not required <= value.keys():
        raise WorkflowError("Missing platform arguments: " + ", ".join(sorted(required)))
    for name in required - {"packages"}:
        if not isinstance(value[name], str) or not value[name].strip():
            raise WorkflowError(f"Platform {name} must be a nonempty string")
    if action == "bootstrap" and value["accelerator"] not in {"kvm", "tcg"}:
        raise WorkflowError("Select kvm or tcg explicitly")
    return action


def run(project, job_id, value):
    action = arguments(value)
    stage = authorize(project, job_id)
    if (action == "bootstrap") != (stage == "environment_recovery"):
        raise WorkflowError("Environment uses bootstrap; implementation uses platform operations")
    with activity.record(project, job_id, action):
        if action == "bootstrap":
            from ..environment.bootstrap import prepare

            result = prepare(project, Path(value["probe"]), value["image"], value["accelerator"])
        elif action == "build":
            built = service.build(project)
            result = {key: built[key] for key in ("status", "receipt", "dependencies")}
            result["scope"] = "Build only; not driver acceptance"
        elif action == "format":
            result = formatting.run(project, value["packages"], write=value.get("write", False))
        elif action == "run_case":
            result = service.run_case(project, Path(value["case"]))
        else:
            from .integration import example

            result = example(project, value.get("package"))
    return json.dumps(result, ensure_ascii=False)


def context(project):
    """Worker-facing interface: CLI remains available to harnesses, not the default worker path."""
    value = service.context(project)
    if not value:
        return value
    value = {
        key: item
        for key, item in value.items()
        if key not in {"prepare", "verify", "build", "format", "run_case", "format_interface"}
    }
    value["tool"] = "driver_checks.platform"
    value["actions"] = {
        "build": {"action": "build"},
        "format": {"action": "format", "packages": ["<affected-package>"], "write": True},
        "run_case": {"action": "run_case", "case": ".dpf-output/harness/<case>.json"},
        "integration": {"action": "integration"},
    }
    value["execution"] = (
        "Await the platform tool once; no shell background command or write_stdin polling. "
        "The controller waits, records failure/timeout and cleans its container. "
        "The human monitor tails execution logs without invoking the model. "
        "For delivery harness scripts, generated .dpf-output/harness/platform/ entrypoints "
        "remain available; run-case.sh takes a JSON case path."
    )
    if "case_interface" in value:
        value["case_interface"] = {
            **value["case_interface"],
            "invoke": "driver_checks.platform action=run_case, case=<worktree JSON path>",
        }
    return value
