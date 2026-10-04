from __future__ import annotations

from types import MappingProxyType

from ..core.models import WorkflowError
from ..core.validation import ArtifactValidator, json_object, json_object_document, utf8_document
from .contracts import TargetStudyArtifact, TargetStudyOutcome, TargetStudyStage


def _failed_attempt(data: bytes) -> None:
    value = json_object(data, TargetStudyArtifact.VALIDATION_ATTEMPT.value)
    if TargetStudyOutcome(value.get("status")) is not TargetStudyOutcome.FAIL:
        raise WorkflowError("target_study_validation_attempt must record FAIL")
    if not value.get("errors"):
        raise WorkflowError("target_study_validation_attempt must preserve errors")


def _quality(data):
    value = json_object(data, TargetStudyArtifact.KNOWLEDGE_QUALITY.value)
    from ..knowledge.probes import _rows

    rows = _rows(value.get("specification"))
    expected = "RETRIEVAL_VALIDATED" if rows else "NO_RETRIEVAL_REQUESTED"
    if value.get("status") != expected:
        raise WorkflowError("Target knowledge quality misstates its retrieval evidence")


def _study_bundle(context):
    # Do not let direct finalize_stage bypass actual original/query checks.
    import json

    from ..composition import open_project
    from ..knowledge.corpus import CorpusManifest
    from ..knowledge.index import KnowledgeIndex
    from ..knowledge.probes import evaluate

    _, report = context.one_current(TargetStudyArtifact.REPORT)
    _, data = context.one_current(TargetStudyArtifact.KNOWLEDGE_QUALITY)
    supplied = json.loads(data)
    project = open_project(context.project_root, read_only=True, verify_artifacts=False)
    current = evaluate(
        KnowledgeIndex(context.project_root, CorpusManifest.current(project)),
        supplied["specification"],
        report,
    )
    from ..migration.route import freeze

    _, route_data = context.one_current(TargetStudyArtifact.ROUTE)
    route = json.loads(route_data)
    if freeze(project, route["index"], report.decode()) != route:
        raise WorkflowError("Analysis route or source evidence changed before acceptance")
    if current != supplied:
        raise WorkflowError("Target knowledge quality is stale or does not bind current report")


BUNDLE_VALIDATORS = {TargetStudyStage.STUDY: _study_bundle}


VALIDATORS = MappingProxyType[TargetStudyArtifact, ArtifactValidator](
    {
        TargetStudyArtifact.ROUTE: json_object_document,
        TargetStudyArtifact.REPORT: utf8_document,
        TargetStudyArtifact.KNOWLEDGE_QUALITY: _quality,
        TargetStudyArtifact.VALIDATION_ATTEMPT: _failed_attempt,
    }
)
