"""A short handoff, recorded by the controller without a per-behavior report."""

import json

from ..core.models import WorkflowError
from . import behavior
from .contracts import MigrationStage
from .experiment_ack import _job


def tool():
    return {
        "name": "progress",
        "description": "Finish this round: done marks only the selected behavior implemented; "
        "continue retains it. Neither accepts delivery. Optional short note for a consequential "
        "decision or unresolved question; no report required. On the final work package, "
        "include relevant source self-check findings and validation limits in note. Existing "
        "controller checks handle delivery; no extra submission gate or review round. "
        "Stop after successful submission.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["status"],
            "properties": {
                "status": {"type": "string", "enum": ["done", "continue"]},
                "note": {"type": "string", "maxLength": 2000},
            },
        },
    }


def submit(project, job_id, arguments):
    from ..codex.submission import receipt_path, write_submission

    if not isinstance(arguments, dict) or set(arguments) - {"status", "note"}:
        raise WorkflowError("Progress accepts status and optional note")
    status, note = arguments.get("status"), arguments.get("note", "")
    if status not in {"done", "continue"} or not isinstance(note, str) or len(note) > 2000:
        raise WorkflowError("Use done/continue and an optional note of at most 2000 characters")
    job = _job(project, job_id)
    stage = MigrationStage(job["stage"])
    if not behavior.enabled(project, stage):
        raise WorkflowError("Progress requires a scheduled implementation behavior")
    if behavior.packet(project, stage)["current"] is None:
        raise WorkflowError("No selected behavior; submit final delivery")
    receipt = receipt_path(project, job_id)
    if receipt.exists():
        raise WorkflowError("This job already submitted; end the round")
    from ..acquisition.repository import load_repository_acquisition

    target = load_repository_acquisition(project).target_worktree
    path = project.root / target.path / ".dpf-output" / "progress" / f"{job_id}.md"
    if (
        not path.resolve().is_relative_to((project.root / target.path).resolve())
        or path.is_symlink()
    ):
        raise WorkflowError("Progress file escapes the worktree")
    path.parent.mkdir(parents=True, exist_ok=True)
    if status == "done" and behavior.last_selected(project, stage):
        # Preserve the worker's own account, without synthesizing semantic claims.
        previous = sorted(path.parent.glob("*.md"))
        history = "\n".join(p.read_text() for p in previous if p != path)
        path.write_text(
            "# Delivery handoff\nWorker notes; controller acceptance is separate.\n\n"
            + history
            + "\n"
            + note
            + "\n"
        )
    else:
        path.write_text(f"Behavior {status}; not acceptance.\n{note}\n")
    write_submission(
        project,
        stage,
        job_id=job_id,
        file_path=str(path),
        kind="report",
        decision="operation",
        operation=f"behavior_{status}",
    )
    return json.dumps(
        {"submitted": status, "instruction": "End this round; controller records progress."}
    )
