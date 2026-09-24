"""Worker decisions on checker findings, separate from raw execution evidence."""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from .models import ArtifactContent, ArtifactRef, WorkflowError
from .recovery_state import fingerprint
from .artifact_identity import substantive_artifact_identity_digest


class CheckerDecisionRequired(WorkflowError):
    def __init__(self, path: Path, findings: list[str]):
        self.path = path
        super().__init__("Checker findings require worker judgment:\n" + "\n".join(findings))


class RecoveryPaused(WorkflowError):
    """Do not turn a stalled stage into an unlimited sequence of paid turns."""


_DERIVED_RECEIPT_INPUTS = frozenset({
    "artifact_preparation_attempt",
    "public_qemu_attempt",
    "target_framework_enablement_report",
    "compliance_report",
    "public_qemu_work_report",
})


def _substantive_input(project, ref) -> dict | None:
    """Return the input identity that can invalidate executable work.

    Human-readable reports and execution attempts are audit metadata.  The
    artifact identity is retained, but its attempt/report pointers are removed
    so refreshing a receipt cannot reset the unchanged-input recovery guard.
    """
    if ref.kind in _DERIVED_RECEIPT_INPUTS:
        return None
    value = ref.to_dict()
    if ref.kind == "artifact_identity":
        identity = json.loads(project.artifacts.read(ref))
        value = {
            "kind": ref.kind,
            "semantic_sha256": substantive_artifact_identity_digest(identity),
        }
    return value


def stage_inputs(project, stage) -> list[dict]:
    result = []
    for dependency in project.workflow.spec(stage).dependencies:
        for ref in project.current_artifact_refs(stage=dependency):
            if ref.kind.startswith("codex_"):
                continue
            identity = _substantive_input(project, ref)
            if identity is not None:
                result.append(identity)
    return result


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


def guard_operation(project, stage, job_ordinal: int) -> None:
    """Bound unchanged execution requests, including successful receipt replays."""
    payload = {"stage": stage.value, "inputs": stage_inputs(project, stage),
               "artifacts": None, "findings": ["Repeated execution requests with unchanged inputs"]}
    identity = fingerprint(project, stage, payload)
    directory = project.control / "operation-requests"
    directory.mkdir(exist_ok=True)
    path = directory / f"{stage.value}-{identity}.json"
    jobs = json.loads(path.read_text()) if path.exists() else []
    if job_ordinal not in jobs:
        if len(jobs) >= 3:
            _request(project, stage, payload, force_pause=True)
        jobs.append(job_ordinal)
        _replace_text(path, json.dumps(jobs))


def _request(project, stage, payload, *, force_pause=False) -> None:
    # A newer durable job result is the repair response, even if the controller
    # stopped before submitting it. Reuse it instead of paying for another turn.
    payload["after_job"] = max((r.ordinal for r in project.current_artifact_refs(stage=stage)
                                if r.kind == "codex_job_result"), default=-1)
    directory = project.control / "checker-decisions"
    directory.mkdir(exist_ok=True)
    previous = [json.loads(path.read_text()) for path in directory.glob(f"{stage.value}-*.json")]
    payload["repair_fingerprint"] = fingerprint(project, stage, payload)
    payload["paused"] = force_pause or sum(item["repair_fingerprint"] == payload["repair_fingerprint"]
                            and not item["paused"] for item in previous) >= 3
    path = directory / f"{stage.value}-{uuid4().hex}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _replace_text(directory / f"{stage.value}.pending", str(path))
    if payload["paused"]:
        _raise_paused(stage, path)
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
    if payload["paused"]:
        if fingerprint(project, stage, payload) != payload["repair_fingerprint"]:
            clear_pending(project, stage)
            return None
        _raise_paused(stage, path)
    return CheckerDecisionRequired(path, payload["findings"])


def _raise_paused(stage, path):
    raise RecoveryPaused(
        f"{stage.value}: repeated recovery with unchanged repair inputs. Evidence: {path}. "
        "Repair executable inputs then resume; for external changes not represented in files, "
        "use stage recovery-resume with a reason. No new worker call was made.")


def clear_pending(project, stage):
    (project.control / "checker-decisions" / f"{stage.value}.pending").unlink(missing_ok=True)


def resume_recovery(project, stage, reason):
    """Explicit operator authority for causal changes no file snapshot can observe."""
    from .events import RunEvent
    if not reason.strip():
        raise WorkflowError("recovery resume requires the changed prerequisite or resolution reason")
    directory = project.control / "checker-decisions"
    pointer = directory / f"{stage.value}.pending"
    if not pointer.is_file() and stage.value == "repository_acquisition":
        from ..acquisition.contracts import AcquisitionArtifact
        from .models import StageStatus
        if project.stage(stage).status is not StageStatus.RUNNING:
            raise WorkflowError("acquisition must be RUNNING to diagnose a paused fetch")
        attempts = [ref for ref in project.current_artifact_refs(stage=stage)
                    if ref.kind == AcquisitionArtifact.REPOSITORY_ACQUISITION_ATTEMPT.value]
        failure = json.loads(project.artifacts.read(max(attempts, key=lambda r: r.ordinal))) if attempts else {}
        if failure.get("failure_type") != "RepositoryFetchError":
            raise WorkflowError("no paused repository fetch to diagnose")
        project.record_event(RunEvent.RECOVERY_RESUMED, {
            "stage": stage.value, "reason": reason, "action": "diagnose_paused_fetch"})
        try:
            request_decision(project, stage, None, [
                f"Operator requested diagnosis: {reason}", failure["message"]])
        except CheckerDecisionRequired:
            return
    if not pointer.is_file() or not json.loads(Path(pointer.read_text()).read_text())["paused"]:
        raise WorkflowError("stage has no paused recovery to resume")
    resume_id = uuid4().hex
    project.record_event(RunEvent.RECOVERY_RESUMED, {
        "stage": stage.value, "reason": reason, "resume_id": resume_id,
        "previous_request": pointer.read_text(),
    })
    _replace_text(directory / f"{stage.value}.resume", resume_id)
    clear_pending(project, stage)


def _replace_text(path: Path, text: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text)
    temporary.replace(path)


def capture_is_current(project, stage, payload) -> bool:
    """Only unchanged captured candidates can be published without recapture."""
    return (payload["artifacts"] is not None
            and payload["inputs"] == stage_inputs(project, stage)
            and payload["repair_fingerprint"] == fingerprint(project, stage, payload))


def accept_decision(project, stage, path: Path, report: Path) -> None:
    """Publish the captured outputs; never rewrite failed receipts as successful tests."""
    from .events import RunEvent
    payload = json.loads(path.read_text())
    if payload["stage"] != stage.value or payload["inputs"] != stage_inputs(project, stage):
        raise WorkflowError("checker decision inputs changed; re-evaluate the current outputs")
    text = report.read_text()
    if not text.strip():
        raise WorkflowError("worker acceptance report is empty")
    if payload["artifacts"] is None:
        raise WorkflowError("execution stopped before outputs were captured; submit repaired normal deliverables")
    if not capture_is_current(project, stage, payload):
        raise WorkflowError("captured outputs are stale; recapture current deliverables")
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
