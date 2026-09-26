"""Explicit context rotation with a factual handoff, never automatic history deletion."""
import hashlib
import json
import sqlite3
import uuid

from ..core.events import RunEvent
from ..core.models import ArtifactDirection, EvaluationMode, WorkflowError, utc_now
from .context_focus import reading_plan
from .contracts import CodexArtifact
from .observation_handoff import observation_handoff
from .sessions import read_session


def attach_handoff(project, session, context, thread_id):
    if not session.get("handoff") or session.get("thread_id"):
        return context, thread_id
    handoff = session["handoff"]
    path = project.artifacts.path_for_digest(handoff["digest"])
    if hashlib.sha256(path.read_bytes()).hexdigest() != handoff["digest"]:
        raise WorkflowError("context handoff failed integrity verification")
    return {**(context or {}), "context_handoff": {
        **handoff, "path": str(path),
        "instruction": "Read this factual handoff before continuing. Preserve unresolved "
        "obligations; recheck prior claims against current evidence.",
    }}, None  # An explicit rotation overrides even a stale caller thread ID.


def reset_session(project, stage, key: str, *, reason: str,
                  automatic_boundary: str | None = None) -> dict:
    """Caller must hold the controller lock. Rotate one provider/model/role session."""
    if not reason.strip():
        raise WorkflowError("context reset requires a reason")
    if project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE:
        raise WorkflowError("context reset is currently limited to developer-evidence runs")
    session = read_session(project, key)
    if not session.get("thread_id"):
        raise WorkflowError("no existing conversation for this stage/model/backend to reset")
    # References are controller-generated facts, not model-written recollections.
    # Retain current reports as evidence, including unresolved obligations in them.
    inputs = []
    excluded = {k.value for k in CodexArtifact} - {CodexArtifact.WORK_REPORT.value}
    for view in project.stages():
        refs = project.current_artifact_refs(stage=view.name, direction=ArtifactDirection.OUTPUT)
        latest = {ref.kind: ref for ref in refs}
        for ref in latest.values():
            if ref.kind not in excluded:
                inputs.append({"stage": view.name.value, "kind": ref.kind, "digest": ref.digest,
                               "path": str(project.artifacts.path_for_digest(ref.digest))})
    epoch = uuid.uuid4().hex
    packet = {
        "schema_version": 1, "epoch": epoch, "created_at": utc_now(), "reason": reason,
        "automatic_boundary": automatic_boundary,
        "stage": stage.value,
        "scope": {"source": project.config.source_platform,
                  "target": project.config.target_platform, "driver": project.config.driver_name},
        "stages": [{"stage": s.name.value, "status": s.status.value, "message": s.message}
                   for s in project.stages()],
        "current_evidence": inputs,
        "reading_plan": reading_plan(stage, {"current_evidence": inputs}),
        "history_lookup": {
            "previous_thread": session["thread_id"],
            "archived_session": str(project.control / "codex" / "sessions" / "archive" /
                                    f"{key}-{epoch}.json"),
            "job_logs": str(project.control / "codex"),
            "instruction": "Consult only for a concrete missing fact or rejected alternative; "
                           "do not reload the full conversation by default.",
        },
        "execution_observations": observation_handoff(project, stage),
        "instruction": "Continue the existing worktree and frozen scope. Read current contracts, "
        "unresolved obligations and the latest failure evidence before editing. Stage PASS does "
        "not prove device behavior. Prior reports are claims; prefer raw evidence on conflict. "
        "Reuse passing work with unchanged inputs. Read the relevant analysis reports and source "
        "references on demand, not every linked artifact. Keep verified conclusions, hypotheses, "
        "open questions and rejected alternatives distinct. Verify consequential claims against "
        "their cited originals when using them; no extra blanket review is required. "
        "History remains available for targeted lookup.",
    }
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT payload FROM events WHERE event_type='run.continuation' "
            "AND json_extract(payload, '$.stage')=? ORDER BY sequence DESC LIMIT 1",
            (stage.value,),
        ).fetchone()
    if row:
        packet["last_continuation"] = json.loads(row[0])
    root = project.control / "codex" / "sessions"
    archive = root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / f"{key}-{epoch}.json").write_text(json.dumps(session, sort_keys=True) + "\n")
    content = project.artifacts.put_bytes(
        (json.dumps(packet, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode(),
        kind="context_handoff",
    )
    handoff = {"digest": content.digest,
               "path": str(project.artifacts.path_for_digest(content.digest)), "epoch": epoch,
               "reason": reason}
    # Event is durable before the pointer switches; a failed switch leaves the
    # original resumable thread intact. The CAS packet is immutable.
    project.record_event(RunEvent.SESSION_RESET, {
        "stage": stage.value, "session_key": key, "previous_thread": session["thread_id"],
        "handoff": handoff, "reason": reason,
        "automatic_boundary": automatic_boundary,
    })
    path = root / f"{key}.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "documents": {}, "inputs": {}, "handoff": handoff,
        "automatic_boundary": automatic_boundary or session.get("automatic_boundary"),
    }))
    temporary.replace(path)
    return handoff
