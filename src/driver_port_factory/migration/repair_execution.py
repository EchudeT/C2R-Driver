"""One worker repair, followed by validated mechanical evidence refreshes.

The reuse receipt is bound to substantive source, runtime and harness bytes.
Worker prose and human-readable receipt text remain audit metadata; changing
them must not manufacture a new implementation or QEMU repair.  Reuse still
authorizes no PASS: normal snapshot, presence, execution and final self-check
still apply.
"""
import json
import sqlite3

from ..acquisition.repository import load_repository_acquisition
from ..core.events import RunEvent
from ..core.models import WorkflowError
from ..knowledge.index import file_sha256
from .contracts import MigrationStage as S
from .implementation import worktree_files
from .public_qemu import PublicQemuService


def active(project, stage):
    feedback = project.retry_feedback(stage)
    return bool(stage in (S.DRIVER_IMPLEMENTATION, S.ARTIFACT_PREPARATION)
                and ((feedback and feedback["status"] == "OPEN"
                      and feedback.get("repair_root") == stage.value)
                     or (stage is S.ARTIFACT_PREPARATION and prepared(project) is not None)))


def identity(project):
    acquisition = load_repository_acquisition(project)
    target = acquisition.target_worktree
    worktree = project.root / target.path
    paths = [worktree / ".dpf-output" / name for name in
             ("runtime-artifact", "check-presence.sh", "public-qemu.sh")]
    if any(not p.is_file() or p.is_symlink() for p in paths):
        raise WorkflowError("Repair preparation requires runtime-artifact, check-presence.sh and public-qemu.sh")
    return {"files": worktree_files(worktree, target.base_commit),
            "prepared": {p.name: file_sha256(p) for p in paths},
            "helpers": PublicQemuService._helper_inputs(worktree)}


def record(project, report):
    from .review_policy import require_self_review
    require_self_review(report.read_text())
    value = identity(project)
    # report is already in CAS, recorded by the ordinary worker result handler.
    project.record_event(RunEvent.REPAIR_PREPARED, {
        "identity": value, "report": str(report.relative_to(project.root)),
        "report_sha256": file_sha256(report)})


def prepared(project):
    # Only the latest receipt may authorize reuse. An old matching receipt must
    # not hide a later invalidation or intervening worker changes.
    with sqlite3.connect(project.database_path) as db:
        row = db.execute("SELECT sequence,payload FROM events WHERE event_type=? ORDER BY sequence DESC LIMIT 1",
                         (RunEvent.REPAIR_PREPARED.value,)).fetchone()
        if row is None:
            return None
        later = db.execute("SELECT 1 FROM events WHERE sequence>? AND event_type='stage.retried' "
            "AND json_extract(payload,'$.stage') IN ('driver_implementation','artifact_preparation') LIMIT 1",
            (row[0],)).fetchone()
    if later:
        return None
    value = json.loads(row[1])
    report = (project.root / value["report"]).resolve()
    try:
        # The report is retained for audit context, but its prose/hash is not a
        # substantive repair input.  A refreshed receipt must not invalidate a
        # prepared repair when the source/runtime/harness identity is unchanged.
        if (project.root not in report.parents or not report.is_file()
                or identity(project) != value["identity"]):
            return None
    except (OSError, WorkflowError):
        return None
    return report
