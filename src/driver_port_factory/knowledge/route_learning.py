"""Selective analysis findings, backed by original bytes or controller observations."""

import hashlib

from ..core.events import RunEvent
from ..core.models import ActorRole, ArtifactContent, EvaluationMode, WorkflowError
from ..migration import route, route_model, route_probe
from .shared import add_experience
from .shared_binding import project_binding
from .shared_store import Library


def publish(project, binding, text):
    index = binding["index"]
    if not index["learn"]:
        return {"status": "NO_LEARNING_SELECTED"}
    bound = project_binding(project)
    if not bound:
        return {"status": "NO_SHARED_LIBRARY_CONFIGURED"}
    if (
        project.config.actor_role is not ActorRole.DEVELOPER
        or project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE
    ):
        raise WorkflowError("Shared learning is limited to public developer findings")
    library = Library(bound.get("publish_root", bound["root"]))
    outcomes = []
    for premise in index["premises"]:
        if premise["id"] not in index["learn"]:
            continue
        if premise["status"] != "supported":
            raise WorkflowError("Unresolved hypotheses cannot be published as supported findings")
        entries, ranges = [], []
        with library.writing():
            for citation in premise["sources"]:
                data, checkout = route.source(project, citation)
                frozen = next(
                    (r for r in binding["sources"] if all(r[k] == v for k, v in citation.items())),
                    None,
                )
                if not frozen or frozen["sha256"] != hashlib.sha256(data).hexdigest():
                    raise WorkflowError("Analysis learning source changed after binding")
                # A target edit is a task source observation, never an upstream original.
                entries.append(
                    {
                        "schema_version": 1,
                        "kind": "OBSERVATION",
                        "domain": citation["repository"],
                        "platform": checkout.platform,
                        "revision": checkout.resolved_commit,
                        "title": citation["path"],
                        "blob": library.put(data),
                        "origin": {**citation, "task": project.config.project_id},
                        "authority": "ARCHIVED_TASK_SOURCE_NOT_UPSTREAM_GUARANTEE",
                    }
                )
                ranges.append((citation["line_start"], citation["line_end"]))
            for name in premise["probe_receipts"]:
                observed = route_probe.receipt(project, name)
                if observed["status"] != "OBSERVED":
                    raise WorkflowError("Failed probe is not supporting evidence for this finding")
                attachments = []
                for item in [observed["script"], *observed["evidence"]]:
                    raw = project.artifacts.read(ArtifactContent(**item))
                    attachments.append({"blob": library.put(raw)})
                raw = route.encoded(observed)
                entries.append(
                    {
                        "schema_version": 1,
                        "kind": "OBSERVATION",
                        "domain": "target",
                        "platform": project.config.target_platform,
                        "revision": route.load_repository_acquisition(
                            project
                        ).target_worktree.base_commit,
                        "title": f"Critical premise {premise['id']} observation {name}",
                        "blob": library.put(raw),
                        "attachments": attachments,
                        "status": "OBSERVED_SCRIPT_NOT_GENERAL_SEMANTIC_PROOF",
                    }
                )
                ranges.append((1, len(raw.decode().splitlines())))
            published = library.commit(entries)
        citations = [
            {"entry": key, "line_start": span[0], "line_end": span[1]}
            for key, span in zip(published["entries"], ranges, strict=True)
        ]
        result = add_experience(
            library,
            {
                "title": premise["section"],
                "problem": "A design-changing prerequisite in a source-to-target driver route.",
                "lesson": route_model.section(text, premise["section"]),
                "conditions": "Applies only under the cited revisions and the following route "
                "conditions; recheck them for another task:\n"
                + "\n".join(
                    route_model.section(text, r["section"])
                    for r in index["main_route"]
                    if r["id"] in premise["route"]
                ),
                "limitations": "Model interpretation of the cited evidence, not semantic proof or "
                "new-task acceptance. Target snapshots may include task-specific edits. "
                "The lesson must state any additional applicability limits.",
                "tags": [
                    project.config.source_platform,
                    project.config.target_platform,
                    project.config.driver_name,
                    "route-premise",
                ],
                "evidence": citations,
            },
        )
        outcomes.append({"premise": premise["id"], **result})
    project.record_event(
        RunEvent.ROUTE_LEARN, {"analysis": binding["report_sha256"], "published": outcomes}
    )
    return {"status": "PUBLISHED_INTERPRETATIONS", "results": outcomes}


def publish_optional(project, binding, text):
    # Shared learning is not a delivery gate. A library outage must not reopen accepted work.
    try:
        return publish(project, binding, text)
    except (OSError, WorkflowError) as error:
        value = {
            "status": "NOT_PUBLISHED",
            "analysis": binding["report_sha256"],
            "error": str(error),
        }
        project.record_event(RunEvent.ROUTE_LEARN, value)
        return value
