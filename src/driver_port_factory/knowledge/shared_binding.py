"""Pin shared discovery before acquisition; readers never follow a moving HEAD."""

import json
import os
import sqlite3

from ..core.events import RunEvent
from .contracts import KnowledgeArtifact, KnowledgeStage
from .shared_store import Library


def _recorded(project):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT payload FROM events WHERE event_type=? ORDER BY sequence LIMIT 1",
            (RunEvent.SHARED_KNOWLEDGE_BOUND.value,),
        ).fetchone()
    if row:
        return True, json.loads(row[0])["shared_library"]
    # Preserve the last accepted binding of older projects, including on KB repair.
    refs = [
        r
        for r in project.artifact_refs(stage=KnowledgeStage.KNOWLEDGE_BASE)
        if r.kind == KnowledgeArtifact.QUERY_CONTRACT.value
    ]
    if refs:
        ref = max(refs, key=lambda r: r.ordinal or 0)
        return True, json.loads(project.artifacts.read(ref)).get("shared_library")
    return False, None


def project_binding(project):
    return _recorded(project)[1]


def freeze(project):
    found, bound = _recorded(project)
    if found:
        return bound
    root = os.environ.get("DPF_SHARED_KB")
    if root:
        library = Library(root)
        if not (library.root / "HEAD").exists():
            with library.writing():
                library.commit()
        key, _ = library.snapshot()
        bound = {
            "root": str(library.root),
            "snapshot": key,
            "use": "discovery only; revalidate task versions and import controlled originals",
        }
        destination = os.environ.get("DPF_SHARED_KB_PUBLISH")
        if destination:
            bound["publish_root"] = str(Library(destination).root)
    # Record absence too: resuming with a different environment cannot change evidence.
    project.record_event(RunEvent.SHARED_KNOWLEDGE_BOUND, {"shared_library": bound})
    return bound
