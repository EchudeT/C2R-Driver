"""Accept natural analysis plus replayed source-backed knowledge quality observations."""

import json

from ..core.models import FileArtifact, GeneratedArtifact, WorkflowError
from ..knowledge.index import KnowledgeIndex
from ..knowledge.probes import evaluate, read_specification
from .contracts import TargetStudyArtifact as A
from .contracts import TargetStudyStage as S


def prepare(project, report, *, specification=None, route_index=None):
    """Read-only preflight shared by worker submission and controller acceptance."""
    from ..migration import route

    errors = []
    quality = binding = None
    try:
        specification = read_specification(report) if specification is None else specification
        quality = evaluate(KnowledgeIndex.for_project(project), specification, report.read_bytes())
    except WorkflowError as error:
        errors.append(str(error))
    try:
        route_index = route.read_index(report) if route_index is None else route_index
        binding = route.freeze(project, route_index, report.read_text())
    except WorkflowError as error:
        errors.append(str(error))
    if errors:
        raise WorkflowError(
            "Submission not recorded. Correct these issues and resubmit in "
            "this same turn; do not end the turn as completed:\n" + "\n".join(errors)
        )
    return quality, binding


class TargetStudyService:
    def accept(self, project, report, *, specification=None, route_index=None):
        from ..migration.route import encoded

        quality, route = prepare(
            project, report, specification=specification, route_index=route_index
        )
        project.finalize_stage(
            S.STUDY,
            (
                FileArtifact(A.REPORT, report),
                GeneratedArtifact(A.ROUTE, encoded(route), "generated:analysis-route"),
                GeneratedArtifact(
                    A.KNOWLEDGE_QUALITY,
                    json.dumps(quality, sort_keys=True).encode(),
                    "generated:replayed-target-knowledge-probes",
                ),
            ),
        )

        from ..knowledge.route_learning import publish_optional

        publish_optional(project, route, report.read_text())
