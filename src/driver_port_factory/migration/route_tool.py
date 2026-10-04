"""One analysis protocol: inspect references, probe a premise, or revise a local route."""

import json
import uuid

from ..core.models import StageStatus, WorkflowError
from . import route


def authorize(project, job_id):
    try:
        if str(uuid.UUID(job_id)) != job_id:
            raise ValueError("noncanonical")
    except (ValueError, TypeError, AttributeError) as error:
        raise WorkflowError("Invalid analysis job identity") from error
    paths = list((project.control / "codex").glob(f"*-{job_id}.metrics.json"))
    if len(paths) != 1:
        raise WorkflowError("Analysis tool requires an active job")
    value = json.loads(paths[0].read_text())
    stage = value.get("stage")
    if (
        value.get("invocation_state") != "RUNNING"
        or stage not in {"target_platform_study", "driver_implementation"}
        or not any(
            s.name.value == stage and s.status is StageStatus.RUNNING for s in project.stages()
        )
    ):
        raise WorkflowError("Analysis tool requires its running analysis or implementation stage")
    return stage


def tool():
    return {
        "name": "analysis",
        "description": "Use the single Markdown report and adjacent .route.json reference index. "
        "inspect validates a draft (no acceptance); probe runs a question-bound script once "
        "in the verified platform; revise publishes an implementation route correction while "
        "preserving frozen obligations/oracles. No new planning or reviewer phase. "
        "probe requires premise ID, script path in target worktree, optional timeout 1..300. "
        "Read the returned observation; exit 0 alone does not prove a semantic premise.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["action", "report"],
            "properties": {
                "action": {"type": "string", "enum": ["inspect", "probe", "revise"]},
                "report": {"type": "string"},
                "premise": {"type": "string"},
                "script": {"type": "string"},
                "timeout": {"type": "integer"},
            },
        },
    }


def run(project, job_id, arguments):
    stage = authorize(project, job_id)
    if not isinstance(arguments, dict):
        raise WorkflowError("Analysis arguments must be an object")
    action = arguments.get("action")
    allowed = {"action", "report"} | (
        {"premise", "script", "timeout"} if action == "probe" else set()
    )
    if action not in {"inspect", "probe", "revise"} or set(arguments) - allowed:
        raise WorkflowError("Unknown analysis operation or arguments")
    report = (project.root / arguments["report"]).resolve()
    if not report.is_relative_to(project.root):
        raise WorkflowError("Analysis report must be a workspace file")
    if action == "revise":
        result = route.revise(project, job_id, report)
    elif action == "probe":
        from ..platform import activity
        from .route_probe import run as probe

        with activity.record(project, job_id, "premise-probe"):
            result = probe(
                project,
                report,
                arguments["premise"],
                arguments["script"],
                timeout=arguments.get("timeout", 120),
            )
    else:
        index, text = route.read_index(report), report.read_text()
        index = route.freeze(project, index, text, ready=False)["index"]
        result = {
            "status": "REFERENCES_VALID_NOT_ACCEPTED",
            "stage": stage,
            "open_premises": [p["id"] for p in index["premises"] if p["status"] == "open"],
        }
    return json.dumps(result, ensure_ascii=False)
