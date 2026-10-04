"""Optional worker findings: archive now, publish after public acceptance, never gate delivery."""

import json
import sqlite3
from dataclasses import asdict

from ..core.events import RunEvent
from ..core.models import ActorRole, ArtifactContent, EvaluationMode, StageStatus, WorkflowError
from ..migration.contracts import MigrationArtifact as A
from ..migration.contracts import MigrationStage as S
from .shared import add_experience
from .shared_binding import project_binding
from .shared_store import Library


def events(project, event):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM events WHERE event_type=? ORDER BY sequence", (event.value,)
            )
        ]


def developer(project):
    return (
        project.config.actor_role is ActorRole.DEVELOPER
        and project.config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE
    )


def _text(row, key):
    value = row.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise WorkflowError(f"Knowledge {key} needs 1..2000 characters")
    return value.strip()


def _lesson(project, row):
    from ..migration.route import source
    from ..migration.route_model import _citations

    lesson, conditions = _text(row, "lesson"), _text(row, "conditions")
    citations = row.get("sources")
    if not isinstance(citations, list) or not 1 <= len(citations) <= 4:
        raise WorkflowError("Knowledge needs 1..4 source locations")
    _citations(citations)
    sources = []
    for citation in citations:
        raw, checkout = source(project, citation)
        ref = project.artifacts.put_bytes(raw, kind="knowledge_source")
        sources.append(
            {
                **citation,
                "platform": checkout.platform,
                "revision": checkout.resolved_commit,
                "archive": asdict(ref),
            }
        )
    return {"lesson": lesson, "conditions": conditions, "sources": sources}


def remember(project, job_id, value):
    if value is None or not developer(project) or not project_binding(project):
        return {"status": "NOT_REQUESTED_OR_CONFIGURED"}
    result = {"job_id": job_id, "lessons": [], "skipped": []}
    if not isinstance(value, dict):
        result["skipped"].append("knowledge must be an object")
    else:
        for key, parse in (("lessons", _lesson),):
            rows = value.get(key, [])
            if not isinstance(rows, list):
                result["skipped"].append(f"{key} must be a list")
                continue
            if len(rows) > 3:
                result["skipped"].append(f"Only the first three {key} retained")
            for row in rows[:3]:
                try:
                    result[key].append(parse(project, row))
                except (
                    OSError,
                    WorkflowError,
                    ValueError,
                    TypeError,
                    KeyError,
                    AttributeError,
                ) as e:
                    result["skipped"].append(str(e))
    project.record_event(RunEvent.KNOWLEDGE_FEEDBACK, result)
    return {"status": "RECORDED", "lessons": len(result["lessons"]), "skipped": result["skipped"]}


def remember_optional(project, job_id, value):
    try:
        return remember(project, job_id, value)
    except (OSError, WorkflowError, ValueError, TypeError, sqlite3.Error) as e:
        return {"status": "NOT_RECORDED", "reason": str(e)}


def _publish_lesson(project, library, row):
    entries = []
    with library.writing():
        for source in row["sources"]:
            raw = project.artifacts.read(ArtifactContent(**source["archive"]))
            entries.append(
                {
                    "schema_version": 1,
                    "kind": "OBSERVATION",
                    "domain": source["repository"],
                    "platform": source["platform"],
                    "revision": source["revision"],
                    "title": source["path"],
                    "blob": library.put(raw),
                    "origin": {
                        k: source[k] for k in ("repository", "path", "line_start", "line_end")
                    },
                    "authority": "ARCHIVED_TASK_SOURCE_NOT_UPSTREAM_GUARANTEE",
                }
            )
        archived = library.commit(entries)
    return add_experience(
        library,
        {
            "title": row["lesson"].splitlines()[0][:160],
            "problem": "Driver adaptation or implementation finding",
            "lesson": row["lesson"],
            "conditions": row["conditions"],
            "limitations": "Worker interpretation of archived source. Public task acceptance does "
            "not independently verify this lesson or establish correctness on another driver.",
            "tags": [
                project.config.driver_name,
                project.config.source_platform,
                project.config.target_platform,
                "implementation-finding",
            ],
            "evidence": [
                {"entry": entry, "line_start": s["line_start"], "line_end": s["line_end"]}
                for entry, s in zip(archived["entries"], row["sources"], strict=True)
            ],
        },
    )


def publish(project):
    bound = project_binding(project)
    if (
        not bound
        or not developer(project)
        or project.stage(S.PUBLIC_QEMU_VALIDATION).status is not StageStatus.PASS
    ):
        return {"status": "NOT_ELIGIBLE"}
    receipt = project.artifact(S.PUBLIC_QEMU_VALIDATION, A.PUBLIC_QEMU_REPORT)
    # Only process new feedback. Resuming after acceptance never republishes old findings.
    feedback = events(project, RunEvent.KNOWLEDGE_FEEDBACK)
    from .shared_store import digest, encoded

    batch = digest(encoded(feedback))
    for previous in events(project, RunEvent.KNOWLEDGE_PUBLISHED):
        if previous.get("batch") == batch and previous.get("status") == "PUBLISHED":
            return previous
    library = Library(bound.get("publish_root", bound["root"]))
    result = {
        "status": "PUBLISHED",
        "batch": batch,
        "acceptance": receipt.digest,
        "destination": str(library.root),
        "published": [],
        "skipped": [],
    }
    for record in feedback:
        for row in record["lessons"]:
            try:
                result["published"].append(_publish_lesson(project, library, row))
            except (OSError, WorkflowError, ValueError, TypeError, KeyError) as e:
                result["skipped"].append(str(e))
    if result["skipped"]:
        result["status"] = "PARTIAL"
    project.record_event(RunEvent.KNOWLEDGE_PUBLISHED, result)
    return result


def publish_optional(project):
    try:
        return publish(project)
    except (OSError, WorkflowError, ValueError, TypeError, sqlite3.Error) as e:
        # This path runs after acceptance; learning cannot undo the final verdict.
        result = {"status": "NOT_PUBLISHED", "error": str(e)}
        try:
            project.record_event(RunEvent.KNOWLEDGE_PUBLISHED, result)
        except (OSError, WorkflowError, sqlite3.Error):
            pass
        return result
