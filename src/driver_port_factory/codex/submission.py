"""File-backed worker submissions.

Codex is deliberately not trusted to communicate structured data in its final
chat response.  A worker writes a deliverable in its granted workspace and
invokes ``dpf codex submit``.  The command validates the request and writes a
small, immutable-by-convention receipt for the controller to consume.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..core.contracts import StageKey
from ..core.events import RunEvent
from ..core.models import StageStatus, WorkflowError
from ..core.project import Project
from .policy import CodexExecutionPolicy


PROPOSAL_STAGES = frozenset({
    "repository_acquisition",
    "evidence_closure",
})
SUBMISSION_KINDS = frozenset({"proposal", "report"})
DECISIONS = frozenset({"submit", "pass", "rework", "blocked", "operation"})


def _checker_recovery_pending(project: Project, stage: StageKey) -> bool:
    """Allow a report only for an active checker-recovery decision.

    Evidence closure normally has a JSON proposal interface.  A checker
    recovery is different: the worker must first adjudicate the finding in a
    report, after which the controller recaptures the normal proposal.  Keep
    this exception tied to the durable pending pointer so ordinary proposal
    submissions cannot silently switch interfaces.
    """
    return (project.control / "checker-decisions" / f"{stage.value}.pending").is_file()


def receipt_path(project: Project, job_id: str) -> Path:
    try:
        uuid.UUID(job_id)
    except (ValueError, AttributeError) as error:
        raise WorkflowError("Codex submission job_id must be a UUID") from error
    return project.control / "codex" / "submissions" / f"{job_id}.json"


def _workspace_file(project: Project, stage: StageKey, value: str, *, kind: str) -> Path:
    grant = CodexExecutionPolicy().grant(project, stage)
    path = Path(value).expanduser().resolve()
    root = grant.execution_root.resolve()
    if path == project.control or project.control.resolve() in path.parents:
        raise WorkflowError("submission files must not be under .dpf control state")
    if path != root and root not in path.parents:
        raise WorkflowError(f"submission file must be inside the Codex workspace: {root}")
    if (kind == "report" and stage in CodexExecutionPolicy.WRITABLE_STAGES
            and root / ".dpf-output" not in path.parents):
        raise WorkflowError(
            "reports for target-framework, implementation, artifact and public-runtime stages "
            "must be under .dpf-output"
        )
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise WorkflowError(f"submission file is not readable: {path}") from error
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise WorkflowError("submission file must be a regular file, not a symlink")
    return path


def _read_deliverable(path: Path, kind: str) -> bytes:
    data = path.read_bytes()
    if not data.strip():
        raise WorkflowError("submission file is empty")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise WorkflowError("submission file must be UTF-8") from error
    if kind == "proposal":
        try:
            value = json.loads(data)
        except json.JSONDecodeError as error:
            raise WorkflowError(
                "proposal JSON must be valid in the submitted file; do not put it in the chat response"
            ) from error
        if not isinstance(value, dict):
            raise WorkflowError("proposal file must contain a JSON object")
    return data


def write_submission(
    project: Project,
    stage: StageKey,
    *,
    job_id: str,
    file_path: str,
    kind: str,
    decision: str,
    operation: str | None = None,
    repair_stage: str | None = None,
) -> Path:
    """Validate and atomically write one worker submission receipt."""
    if stage.value in PROPOSAL_STAGES:
        recovery_report = (
            kind == "report"
            and decision in {"pass", "rework", "blocked"}
            and _checker_recovery_pending(project, stage)
        )
        if not ((kind == "proposal" and decision == "submit") or recovery_report):
            raise WorkflowError(
                f"{stage.value} requires --kind proposal --decision submit"
            )
    elif kind != "report":
        raise WorkflowError(f"{stage.value} requires --kind report")
    if kind not in SUBMISSION_KINDS:
        raise WorkflowError(f"submission kind must be one of {sorted(SUBMISSION_KINDS)}")
    if decision not in DECISIONS:
        raise WorkflowError(f"submission decision must be one of {sorted(DECISIONS)}")
    if kind == "report" and decision == "submit":
        raise WorkflowError("report submissions must choose pass, rework, blocked, or operation")
    if decision == "operation":
        from ..orchestration.protocol import TASKS
        allowed = TASKS.get(stage.value).operations if TASKS.get(stage.value) else ()
        if operation not in allowed:
            raise WorkflowError(
                f"operation must be one of {allowed} for {stage.value}"
            )
    elif operation is not None:
        raise WorkflowError("--operation is only valid with --decision operation")
    if decision == "rework" and repair_stage is None:
        raise WorkflowError("--repair-stage is required with --decision rework")
    if repair_stage is not None:
        from ..orchestration.protocol import REPAIR_TARGETS
        if repair_stage not in REPAIR_TARGETS:
            raise WorkflowError(f"repair stage must be one of {sorted(REPAIR_TARGETS)}")
    if decision != "rework" and repair_stage is not None:
        raise WorkflowError("--repair-stage is only valid with --decision rework")
    if project.stage(stage).status is not StageStatus.RUNNING:
        raise WorkflowError(
            f"cannot submit for {stage.value} while stage is {project.stage(stage).status.value}"
        )
    path = _workspace_file(project, stage, file_path, kind=kind)
    data = _read_deliverable(path, kind)
    target = receipt_path(project, job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    record: dict[str, Any] = {
        "schema_version": 1,
        "job_id": job_id,
        "stage": stage.value,
        "kind": kind,
        "decision": decision,
        "operation": operation,
        "repair_stage": repair_stage,
        "file": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "submitted_at": datetime.now(UTC).isoformat(),
    }
    encoded = (json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    if target.exists():
        try:
            current = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"existing submission receipt is damaged: {target}") from error
        comparable = {key: value for key, value in record.items() if key != "submitted_at"}
        existing_comparable = {
            key: value for key, value in current.items() if key != "submitted_at"
        }
        if existing_comparable != comparable:
            raise WorkflowError("this Codex job already has a different submission")
        return target
    temporary = target.with_suffix(f".tmp-{os.getpid()}")
    temporary.write_bytes(encoded)
    os.replace(temporary, target)
    project.record_event(RunEvent.WORKER_SUBMISSION, {
        "stage": stage.value,
        "job_id": job_id,
        "receipt": str(target),
        "decision": decision,
    })
    return target


def load_submission(project: Project, stage: StageKey, job_id: str) -> dict[str, Any] | None:
    path = receipt_path(project, job_id)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WorkflowError(f"Codex submission receipt is invalid: {path}") from error
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise WorkflowError(f"Codex submission receipt has an invalid schema: {path}")
    if value.get("job_id") != job_id or value.get("stage") != stage.value:
        raise WorkflowError("Codex submission receipt is bound to a different job or stage")
    kind = value.get("kind")
    decision = value.get("decision")
    if kind not in SUBMISSION_KINDS or decision not in DECISIONS:
        raise WorkflowError("Codex submission receipt has an invalid kind or decision")
    if stage.value in PROPOSAL_STAGES:
        recovery_report = (
            kind == "report"
            and decision in {"pass", "rework", "blocked"}
            and _checker_recovery_pending(project, stage)
        )
        if not ((kind == "proposal" and decision == "submit") or recovery_report):
            raise WorkflowError("proposal-stage submission receipt has an invalid decision")
    elif kind != "report" or decision == "submit":
        raise WorkflowError("report-stage submission receipt has an invalid decision")
    if decision == "operation" and not value.get("operation"):
        raise WorkflowError("operation submission receipt has no operation")
    if decision == "operation":
        from ..orchestration.protocol import TASKS
        allowed = TASKS.get(stage.value).operations if TASKS.get(stage.value) else ()
        if value.get("operation") not in allowed:
            raise WorkflowError("operation submission receipt names an invalid operation")
    if decision == "rework" and not value.get("repair_stage"):
        raise WorkflowError("rework submission receipt has no repair_stage")
    if decision == "rework":
        from ..orchestration.protocol import REPAIR_TARGETS
        if value.get("repair_stage") not in REPAIR_TARGETS:
            raise WorkflowError("rework submission receipt names an invalid repair stage")
    deliverable = _workspace_file(
        project, stage, str(value.get("file", "")), kind=str(kind)
    )
    data = _read_deliverable(deliverable, kind)
    if value.get("sha256") != hashlib.sha256(data).hexdigest() or value.get("size") != len(data):
        raise WorkflowError(
            "submission file changed after dpf codex submit; submit the current file again"
        )
    return value


def submission_artifact(value: dict[str, Any], *, job_digest: str, job_ordinal: int) -> bytes:
    """Bind a receipt to the immutable Codex result occurrence for auditability."""
    return (json.dumps(
        {**value, "job_result_digest": job_digest, "job_result_ordinal": job_ordinal},
        ensure_ascii=False, sort_keys=True, indent=2,
    ) + "\n").encode()
