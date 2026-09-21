"""Worker decisions on checker findings, separate from raw execution evidence."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .models import ArtifactContent, ArtifactRef, WorkflowError


class CheckerDecisionRequired(WorkflowError):
    def __init__(self, path: Path, findings: list[str]):
        self.path = path
        super().__init__("Checker findings require worker judgment:\n" + "\n".join(findings))


class RecoveryPaused(WorkflowError):
    """Do not turn a stalled stage into an unlimited sequence of paid turns."""


def stage_inputs(project, stage) -> list[dict]:
    return [ref.to_dict() for dependency in project.workflow.spec(stage).dependencies
            for ref in project.current_artifact_refs(stage=dependency)
            if not ref.kind.startswith("codex_")]


def request_decision(project, stage, refs, findings) -> None:
    _request(project, stage, {
        "stage": stage.value, "inputs": stage_inputs(project, stage),
        "artifacts": [ref.to_dict() for ref in refs] if refs is not None else None,
        "findings": list(dict.fromkeys(findings)),
    })


def request_recovery(project, stage, error) -> None:
    """Use one durable decision protocol for execution and acceptance failures."""
    pending = pending_decision(project, stage)
    if pending is None:
        request_decision(project, stage, None, [f"{type(error).__name__}: {error}"])
    payload = json.loads(pending.path.read_text())
    payload["findings"] = list(dict.fromkeys([
        *payload["findings"], f"{type(error).__name__}: {error}"]))
    _request(project, stage, payload)


def _request(project, stage, payload) -> None:
    # A newer durable job result is the repair response, even if the controller
    # stopped before submitting it. Reuse it instead of paying for another turn.
    payload["after_job"] = max((r.ordinal for r in project.current_artifact_refs(stage=stage)
                                if r.kind == "codex_job_result"), default=-1)
    directory = project.control / "checker-decisions"
    directory.mkdir(exist_ok=True)
    previous = [json.loads(path.read_text()) for path in directory.glob(f"{stage.value}-*.json")]
    if sum(item["inputs"] == payload["inputs"] for item in previous) >= 3:
        clear_pending(project, stage)
        raise RecoveryPaused(
            f"{stage.value}: three recovery requests without completing the stage; "
            "paused with evidence preserved, not calling the worker again. "
            + "\n".join(payload["findings"]))
    path = directory / f"{stage.value}-{uuid4().hex}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _replace_text(directory / f"{stage.value}.pending", str(path))
    raise CheckerDecisionRequired(path, payload["findings"])


def pending_decision(project, stage):
    pointer = project.control / "checker-decisions" / f"{stage.value}.pending"
    if not pointer.is_file():
        return None
    path = Path(pointer.read_text())
    payload = json.loads(path.read_text())
    if payload["inputs"] != stage_inputs(project, stage):
        clear_pending(project, stage)
        return None
    return CheckerDecisionRequired(path, payload["findings"])


def clear_pending(project, stage):
    (project.control / "checker-decisions" / f"{stage.value}.pending").unlink(missing_ok=True)


def _replace_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def accept_decision(project, stage, path: Path, report: Path) -> None:
    """Publish the captured outputs; never rewrite failed receipts as successful tests."""
    from .events import RunEvent
    payload = json.loads(path.read_text())
    if payload["stage"] != stage.value or payload["inputs"] != stage_inputs(project, stage):
        raise WorkflowError("checker decision inputs changed; re-evaluate the current outputs")
    text = report.read_text()
    if not text.rstrip().endswith("\nDPF_CHECKER_DECISION: ACCEPT"):
        raise WorkflowError("worker acceptance report must end DPF_CHECKER_DECISION: ACCEPT")
    if payload["artifacts"] is None:
        raise WorkflowError("execution stopped before outputs were captured; submit repaired normal deliverables")
    refs = [ArtifactRef(ArtifactContent(
        item["digest"], item["kind"], item["size"], item["cas_path"]), item["source"])
        for item in payload["artifacts"]]
    if not all(project.artifacts.verify(ref) for ref in refs):
        raise WorkflowError("checker decision outputs are missing or damaged")
    decision = project.artifacts.put_bytes(text.encode(), kind="worker_checker_decision")
    message = f"WORKER_ACCEPTED: {decision.digest}; checker findings: {path}"
    project._persistence._commit_validated_stage(stage, refs, message=message)
    project.record_event(RunEvent.CHECKER_DECISION, {
        "stage": stage.value, "decision": "ACCEPT", "report_sha256": decision.digest,
        "findings": payload["findings"], "artifacts": payload["artifacts"],
    })
    clear_pending(project, stage)
