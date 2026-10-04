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
    "bootstrap": set(),
    "build": set(),
    "format": {"packages", "write"},
    "run_case": {"case"},
    "check_boot_log": {"capture", "contains"},
    "integration": {"package"},
    "scaffold": {"package", "template", "dependencies", "owner_source"},
    "register_case": {"id", "case", "contracts"},
}


def tool():
    return {
        "name": "platform",
        "description": (
            "Execute one platform operation and WAIT for the final result. "
            "No shell launch, session ID or write_stdin polling. "
            "bootstrap and build: no arguments; bootstrap uses the operator-configured Docker route. "
            "format: packages, optional write; run_case: inline case object or worktree JSON path. "
            "check_boot_log: capture T1 and contains (list of boot messages); rechecks stored "
            "pre-input output only, without executing or converting a failed run to PASS. "
            "register_case: id, case (object or JSON path), contracts; prepares suite scripts "
            "without running or accepting the case. "
            "integration: optional existing package, returns source-backed wiring on demand. "
            "scaffold: new package, existing component template, optional workspace dependencies "
            "and owner_source; creates wiring with a TODO initializer, never driver logic. "
            "Use the pinned environment; never edit concurrently. Results are observations, "
            "not semantic or stage acceptance. Logs remain available in the monitor."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action"],
            "properties": {
                "action": {"type": "string", "enum": list(FIELDS)},
                "packages": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "write": {"type": "boolean"},
                "case": {"oneOf": [{"type": "string"}, {"type": "object"}]},
                "capture": {"type": "string"},
                "contains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": 32,
                },
                "package": {"type": "string"},
                "template": {"type": "string"},
                "id": {"type": "string"},
                "contracts": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "owner_source": {"type": "string"},
                "dependencies": {"type": "array", "items": {"type": "string"}},
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
    optional = {"write", "owner_source", "dependencies"}
    if action == "integration":
        optional.add("package")
    required = FIELDS[action] - optional
    if not required <= value.keys():
        raise WorkflowError("Missing platform arguments: " + ", ".join(sorted(required)))
    for name in required - {"packages", "contains", "case", "contracts"}:
        if not isinstance(value[name], str) or not value[name].strip():
            raise WorkflowError(f"Platform {name} must be a nonempty string")
    return action


def run(project, job_id, value):
    action = arguments(value)
    stage = authorize(project, job_id)
    if (action == "bootstrap") != (stage == "environment_recovery"):
        raise WorkflowError("Environment uses bootstrap; implementation uses platform operations")
    if action in {"scaffold", "register_case"} and stage not in DELIVERY:
        raise WorkflowError(
            "Scaffolding and case registration belong to implementation, not analysis"
        )
    with activity.record(project, job_id, action):
        if action == "bootstrap":
            from ..environment.bootstrap import prepare

            result = prepare(project)
        elif action == "build":
            built = service.build(project)
            result = {key: built[key] for key in ("status", "receipt", "dependencies")}
            result["scope"] = "Build only; not driver acceptance"
        elif action == "format":
            result = formatting.run(project, value["packages"], write=value.get("write", False))
        elif action == "run_case":
            result = run_case(project, value["case"])
        elif action == "register_case":
            from .suite import register

            result = register(project, value["id"], value["case"], value["contracts"])
        elif action == "scaffold":
            from .scaffold import run as scaffold

            result = scaffold(
                project,
                value["package"],
                value["template"],
                dependencies=value.get("dependencies", []),
                owner_source=value.get("owner_source", "kernel/core/src/init.rs"),
            )
        elif action == "check_boot_log":
            from .log_checks import check

            result = check(project, value["capture"], value["contains"])
        else:
            from .integration import example

            result = example(project, value.get("package"))
    return json.dumps(result, ensure_ascii=False)


def run_case(project, case):
    if isinstance(case, dict):
        from .guest import validate_case

        try:
            validate_case(case)
        except ValueError as error:
            raise WorkflowError(str(error)) from error
        # The tool authors the transport file; the worker specifies the real stimulus/assertions.
        _, worktree = service.load(project)
        path = worktree / ".dpf-output/harness" / f"case-{uuid.uuid4().hex[:12]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(case, indent=2) + "\n")
    elif isinstance(case, str) and case:
        path = Path(case)
    else:
        raise WorkflowError("run_case needs a case object or worktree JSON path")
    result = service.run_case(project, path)
    return {**result, "case": str(path)}


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
    from .public_tests import context as public_test_context

    prepared = public_test_context(project)
    if prepared:
        value["prepared_public_tests"] = prepared
    value["actions"] = {
        "build": {"action": "build"},
        "format": {"action": "format", "packages": ["<affected-package>"], "write": True},
        "run_case": {"action": "run_case", "case": ".dpf-output/harness/<case>.json"},
        "register_case": {
            "action": "register_case",
            "id": "<case-id>",
            "case": "<JSON path or inline case object>",
            "contracts": ["C1"],
        },
        "integration": {"action": "integration"},
        "scaffold": {
            "action": "scaffold",
            "package": "<new-package>",
            "template": "<existing-component>",
            "dependencies": ["<workspace-dependency>"],
        },
        "check_boot_log": {
            "action": "check_boot_log",
            "capture": "T1",
            "contains": ["<actual boot message>"],
        },
    }
    value["delivery_entrypoints"] = {
        "generated": [".dpf-output/implementation-smoke.sh", ".dpf-output/public-qemu.sh"],
        "use": "Register required cases once. Use driver_checks.check cases=[id] during "
        "implementation; final entrypoints use all registered cases. Do not handwrite wrappers. "
        "Unregistered run_case calls are exploratory, not automatically suite members.",
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
            "invoke": "driver_checks.platform action=run_case, "
            "case=<inline case object or JSON path>",
        }
    return value
