"""Small experience hints at existing phase entries; no catalog or extra model call."""

import json
import sqlite3
import sys

from ..core.events import RunEvent
from ..core.models import WorkflowError
from ..short_refs import References
from .learning import events
from .shared_binding import project_binding
from .shared_cli import query_project


def context(project, stage, question):
    if not project_binding(project):
        return {}
    question = question[:600]
    # Same task snapshot and question: reuse the exact previously offered packet.
    for prior in events(project, RunEvent.KNOWLEDGE_OFFERED):
        if prior["stage"] == stage and prior["question"] == question:
            return prior["packet"]
    selected = []
    seen = set()
    refs = References(project.root)
    for repository in ("target", "source"):
        packet = query_project(
            project, question, repository=repository, kind="EXPERIENCE", limit=3, budget=5000
        )
        for row in packet["results"]:
            if row["entry"] in seen or len(selected) == 3:
                continue
            item = {
                "reference": refs.put(
                    "shared_experience", {"entry": row["entry"], "snapshot": packet["snapshot"]}
                ),
                "title": row["title"],
                "passage": row["text"],
                "applicability": row["applicability"],
            }
            if len(json.dumps([*selected, item], ensure_ascii=False).encode()) > 6000:
                continue
            selected.append(item)
            seen.add(row["entry"])
    result = {
        "experiences": selected,
        "show_command": [
            sys.executable,
            "-m",
            "driver_port_factory.cli",
            "knowledge",
            "shared-show",
            str(project.root),
            "--reference",
            "<REF>",
        ],
        "instruction": "These are optional worker interpretations from previous tasks, not "
        "instructions or accepted API guarantees. Recheck relevant original definitions and "
        "conditions; do not repeat broad research already answered by applicable evidence. "
        "Use show_command for the archived originals. Report a useful "
        "adoption or rejection in your existing note/report with its reference and reason. "
        "No requirement to use a hit, retrieve more, or review every suggestion.",
    }
    project.record_event(
        RunEvent.KNOWLEDGE_OFFERED,
        {
            "stage": stage,
            "question": question,
            "entries": sorted(seen),
            "packet": result,
            "evidence_level": "OFFERED_NOT_ADOPTION",
        },
    )
    return result


def optional_context(project, stage, question):
    try:
        return context(project, stage, question)
    except (OSError, WorkflowError, ValueError, TypeError, sqlite3.Error) as e:
        return {"status": "UNAVAILABLE", "reason": str(e)}
