"""Reuse an accepted target study only when its relevant immutable inputs match."""
import hashlib
import json

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..core.models import GeneratedArtifact, StageStatus
from ..core.events import RunEvent
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..knowledge.corpus import CorpusManifest
from .contracts import TargetStudyArtifact as A, TargetStudyStage as S


def identity(project):
    corpus = CorpusManifest.current(project)
    # Source-only additions cannot change the pinned target framework. Other lanes
    # may change platform/packaging judgments and therefore require worker review.
    def semantic(value):
        if isinstance(value, dict):
            return {key: semantic(item) for key, item in value.items()
                    if key not in {"acquired_at", "retrieved_at", "ordinal"}}
        if isinstance(value, list):
            return [semantic(item) for item in value]
        return value

    # Acquisition bookkeeping, order and duplicate records do not change evidence.
    # Keep content, paths, origins and authority: cross-domain facts can affect the study.
    materials = sorted({json.dumps(semantic(r.to_dict()), sort_keys=True)
                        for r in corpus.records if r.facet.lane.value != "source"})
    value = {
        "materials": materials,
        "repositories": project.artifact(AcquisitionStage.REPOSITORY_ACQUISITION,
                                         AcquisitionArtifact.REPOSITORY_MANIFEST).digest,
        "environment": project.artifact(EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD).digest,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def remember(project):
    report = project.artifact(S.STUDY, A.REPORT)
    project.record_event(RunEvent.TASK_REUSE,
                         {"stage": S.STUDY.value, "identity": identity(project), "report": report.digest})


def restore(project):
    # Use ledgered receipts, not mutable cache hints or old PASS state.
    import sqlite3
    from ..core.events import RunEvent
    with sqlite3.connect(project.database_path) as db:
        rows = db.execute("SELECT payload FROM events WHERE event_type=? ORDER BY sequence DESC",
                          (RunEvent.TASK_REUSE.value,)).fetchall()
    wanted = identity(project)
    for (raw,) in rows:
        receipt = json.loads(raw)
        if receipt.get("stage") != S.STUDY.value or receipt.get("identity") != wanted:
            continue
        matching = [r for r in project.artifact_refs(stage=S.STUDY)
                    if r.kind == A.REPORT.value and r.digest == receipt["report"]]
        if not matching:
            return False
        data = project.artifacts.read(matching[-1])
        if project.stage(S.STUDY).status is StageStatus.READY:
            project.start(S.STUDY)
        project.finalize_stage(S.STUDY, (GeneratedArtifact(A.REPORT, data,
            f"reused:target-study:{receipt['report']}"),),
            message="Reused target study: relevant inputs unchanged")
        return True
    return False
