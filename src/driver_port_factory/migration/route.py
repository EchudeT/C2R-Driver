"""Immutable analysis bindings and explicit local route revisions.

These checks establish provenance and dependency identity, not semantic truth.
"""

import hashlib
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from ..acquisition.repository import load_repository_acquisition
from ..acquisition.repository_role import RepositoryRole
from ..checkpoints import git
from ..core.events import RunEvent
from ..core.models import ArtifactContent, WorkflowError
from ..target_study.contracts import TargetStudyArtifact as A
from ..target_study.contracts import TargetStudyStage as S
from . import route_model as model


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n").encode()


def read_index(report):
    path = report.with_suffix(".route.json")
    try:
        return model.normalize(json.loads(path.read_text()))
    except (OSError, ValueError) as error:
        raise WorkflowError(f"Analysis requires its route index: {path}") from error


def source(project, citation):
    acquisition = load_repository_acquisition(project)
    checkout = acquisition.checkout(RepositoryRole(citation["repository"]))
    relative = Path(citation["path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise WorkflowError("Route source paths must be repository-relative")
    # Source obligations always refer to the pinned original. Target adaptation may cite
    # the current implementation; store those exact bytes, never relabel them as original.
    if citation["repository"] == "target":
        root = (project.root / acquisition.target_worktree.path).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise WorkflowError("Target citation escapes or is missing from the target worktree")
        data = path.read_bytes()
    else:
        data = git(
            project.root / checkout.checkout_path,
            "show",
            f"{checkout.resolved_commit}:{relative.as_posix()}",
        )
    try:
        count = len(data.decode("utf-8").splitlines())
    except UnicodeDecodeError as error:
        raise WorkflowError("Route citations require textual originals") from error
    if citation["line_end"] > count:
        raise WorkflowError("Route citation extends beyond its source")
    return data, checkout


def evidence(project, index):
    result = []
    seen = set()
    checks = model.Checks()
    for row in [*index["contracts"], *index["premises"]]:
        for n, citation in enumerate(row.get("sources", [])):
            key = model.digest(citation)
            if key in seen:
                continue
            seen.add(key)
            location = f"{row.get('id', '?')}.sources[{n}]"
            before = len(checks.errors)
            checks.check(location, model._citations, [citation])
            if len(checks.errors) != before:
                continue
            found = checks.check(location, source, project, citation)
            if found is not None:
                data, checkout = found
                result.append(
                    {
                        **citation,
                        "revision": checkout.resolved_commit,
                        "sha256": hashlib.sha256(data).hexdigest(),
                    }
                )
    checks.finish()
    return result


def _inspectable(index):
    """Select well-shaped rows for independent diagnostics after a schema error."""
    result = {}
    for name in ("contracts", "premises"):
        rows = index.get(name, []) if isinstance(index, dict) else []
        result[name] = (
            [r for r in rows if isinstance(r, dict) and isinstance(r.get("sources", []), list)]
            if isinstance(rows, list)
            else []
        )
    return result


def freeze(project, index, text, *, ready=True, previous=None):
    from . import behavior
    from .route_probe import verify_references

    index = model.normalize(index)
    checks = model.Checks()
    valid = checks.check("route", model.validate, index, text, ready=ready)
    inspectable = _inspectable(index)
    sources = checks.check("sources", evidence, project, inspectable)
    checks.check("probe_receipts", verify_references, project, inspectable, text, previous=previous)
    if valid is not None:
        checks.check(
            "behaviors", behavior.compile_plan, model.rows(index, text), model.bases(index, text)
        )
    checks.finish()
    return {
        "index": index,
        "report_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "sources": sources,
    }


def initial(project):
    ref = project.artifact(S.STUDY, A.ROUTE)
    value = json.loads(project.artifacts.read(ref))
    text = project.artifacts.read(project.artifact(S.STUDY, A.REPORT)).decode()
    if value["report_sha256"] != hashlib.sha256(text.encode()).hexdigest():
        raise WorkflowError("Route index is detached from the accepted analysis")
    return value, text, ref.digest


def events(project, kind):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM events WHERE event_type=? ORDER BY sequence DESC",
                (kind.value,),
            )
        ]


def current(project):
    value, text, base = initial(project)
    for event in events(project, RunEvent.ROUTE_REVISION):
        if event["base"] == base:
            data = project.artifacts.read(ArtifactContent(**event["content"]))
            revision = json.loads(data)
            return revision["binding"], revision["text"], event["content"]["digest"]
    return value, text, base


def revise(project, job_id, report):
    from . import behavior
    from .contracts import MigrationStage
    from .experiment_ack import _job

    job = _job(project, job_id)
    if job["stage"] != MigrationStage.DRIVER_IMPLEMENTATION.value:
        raise WorkflowError("Local route revisions belong to the active implementation behavior")
    state = behavior._load(project)
    if job_id not in state["jobs"] or state["jobs"][job_id]["consumed"]:
        raise WorkflowError("Route revision requires an unconsumed behavior round")
    report = (project.root / report).resolve()
    if not report.is_relative_to(project.root):
        raise WorkflowError("Route report must be inside this workspace")
    selected = behavior.select(behavior._units(project, state), state["completed"], state["active"])
    text, index = report.read_text(), read_index(report)
    old, old_text, _ = current(project)
    value = freeze(project, index, text, previous=(old, old_text))
    original, original_text, base = initial(project)
    if model.obligations(index, text) != model.obligations(original["index"], original_text):
        raise WorkflowError(
            "Local route revision cannot change frozen source obligations or oracles"
        )
    for contract in original["index"]["contracts"]:
        revised = next(c for c in index["contracts"] if c["id"] == contract["id"])
        if any(c not in revised["sources"] for c in contract["sources"]):
            raise WorkflowError(
                "A local revision cannot remove frozen source obligation references"
            )
    if value == old and text == old_text:
        return {"status": "UNCHANGED"}
    ref = project.artifacts.put_bytes(
        encoded({"binding": value, "text": text}), kind="route_revision"
    )
    project.record_event(
        RunEvent.ROUTE_REVISION, {"base": base, "job": job_id, "content": asdict(ref)}
    )
    from ..knowledge.route_learning import publish_optional

    publish_optional(project, value, text)
    updated = behavior.select(behavior._units(project, state), state["completed"], state["active"])
    if (
        selected
        and updated
        and selected["behaviors"] == updated["behaviors"]
        and selected["depends_on"] == updated["depends_on"]
        and state["jobs"][job_id]["selected"] == selected["key"]
    ):
        # A corrected route inside the same objective is ordinary implementation work.
        # It does not require another round just to acknowledge the updated basis.
        state["jobs"][job_id]["selected"] = updated["key"]
        state["active"] = updated["key"]
        behavior._save(project, state)
    return {
        "status": "REVISED_NOT_ACCEPTED",
        "revision": ref.digest,
        "packet": behavior.packet(project, MigrationStage.DRIVER_IMPLEMENTATION),
    }


def document(project):
    """Read-only locations for a local correction; no duplicated editable plan."""
    _, _, base = initial(project)
    for event in events(project, RunEvent.ROUTE_REVISION):
        if event["base"] == base:
            return {
                "revision_json": str(project.artifacts.path_for_digest(event["content"]["digest"])),
                "instruction": "Copy text and binding.index into your report.md and "
                "report.route.json only when a route correction is needed.",
            }
    return {
        "report": str(
            project.artifacts.path_for_digest(project.artifact(S.STUDY, A.REPORT).digest)
        ),
        "route_json": str(project.artifacts.path_for_digest(base)),
        "instruction": "The route JSON contains index. Copy the report and index into "
        "report.md/report.route.json only for a concrete route correction.",
    }
