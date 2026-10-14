"""Optional network observation replay, outside public acceptance and its PASS cache."""

import json
import re
import uuid
from pathlib import Path

from ..core.models import WorkflowError
from ..platform import activity, executor, native, service


def available(task):
    """Use declared network capability, device family, or an explicit backend binding."""
    model = task.get("qemu_model", "")
    return (
        bool(task.get("network_required"))
        or task.get("device_type") == "Network"
        or (
            isinstance(model, str)
            and re.search(r"(?:^|,)netdev=[A-Za-z0-9_-]+(?:,|$)", model) is not None
        )
    )


def tool():
    return {
        "name": "network_diagnostics",
        "description": (
            "Diagnose one fixed native network case on the current built ISO. "
            "Optional literal trace_events from the fixed QEMU's -trace help; "
            "PCAP is always captured using QEMU filter-dump. Keeps native guest/peer "
            "commands and assertions; no build, fallback or test modification. "
            "Returns bounded packet/trace observations and full log paths. "
            "Diagnostic replay is not acceptance and never updates retained PASS. "
            "No concurrent edits/builds. Logs stop at 16 MiB per file."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["case"],
            "properties": {
                "case": {"type": "string"},
                "trace_events": {"type": "array", "items": {"type": "string"}},
                "netdev": {"type": "string"},
            },
        },
    }


def request(profile, arguments):
    if not isinstance(arguments, dict) or set(arguments) - {"case", "trace_events", "netdev"}:
        raise WorkflowError("Use case, optional trace_events and netdev")
    config = profile.get("native")
    if not config or not available(config["task"]):
        raise WorkflowError("Network diagnostics requires the configured native network route")
    name = arguments.get("case")
    native.validate_case(profile, {"native_case": name})
    if name == "boot":
        raise WorkflowError("Choose a driver workload, not baseline boot")
    events = arguments.get("trace_events", [])
    if not isinstance(events, list) or any(
        not isinstance(e, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", e) for e in events
    ):
        raise WorkflowError("trace_events must be literal names from QEMU -trace help")
    if "netdev" in arguments and (
        not isinstance(arguments["netdev"], str)
        or not re.fullmatch(r"[A-Za-z0-9_-]+", arguments["netdev"])
    ):
        raise WorkflowError("netdev must name an existing network backend")
    return {
        "case": name,
        "runtime": config["task"].get("runtime_script", "runtime.py"),
        "trace_events": events,
        **({"netdev": arguments["netdev"]} if "netdev" in arguments else {}),
    }


def run(project, job_id, arguments):
    from ..platform.worker import authorize

    stage = authorize(project, job_id)
    if stage != "driver_implementation":
        raise WorkflowError("Network diagnostics is optional implementation feedback")
    service.active(project)
    service.verified(project)
    with activity.record(project, job_id, "network_diagnostics"), service.locked(project):
        profile, worktree = service.load(project)
        value = request(profile, arguments)
        service.check_image(project, profile)
        service.presence(project)
        native.verify_inputs(Path(profile["native"]["root"]), worktree)
        directory = worktree / ".dpf-output/network-diagnostics" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        path = directory / "request.json"
        path.write_text(json.dumps(value) + "\n")
        artifact = worktree / ".dpf-output/runtime-artifact"
        result = executor.container(
            profile,
            worktree,
            directory,
            [
                "python3",
                "/dpf-runner/network_diagnostics.py",
                str(path),
                str(artifact),
                str(directory),
            ],
            build=False,
            cache_key=profile["cache_key"],
            timeout=200,
            artifact=artifact,
        )
        native.verify_inputs(Path(profile["native"]["root"]), worktree)
        service.presence(project)
        receipt = directory / "diagnostics.json"
        if result.exit_code or result.timed_out or not receipt.exists():
            raise WorkflowError(f"Diagnostic execution failed; inspect {directory}")
        observations = json.loads(receipt.read_text())
        observations.update(receipt=str(receipt), pcap=str(directory / "packets.pcap"))
        return json.dumps(observations, ensure_ascii=False)
