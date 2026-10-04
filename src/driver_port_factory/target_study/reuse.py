"""Reuse an accepted target study only when its relevant immutable inputs match."""

import hashlib
import json

from ..acquisition.contracts import AcquisitionArtifact, AcquisitionStage
from ..core.events import RunEvent
from ..core.models import GeneratedArtifact, StageStatus
from ..environment.contracts import EnvironmentArtifact, EnvironmentStage
from ..knowledge.corpus import CorpusManifest
from .contracts import TargetStudyArtifact as A
from .contracts import TargetStudyStage as S


def identity(project):
    corpus = CorpusManifest.current(project)

    # The study now also owns source semantics and the first contract/test matrix.
    # Source-only additions can invalidate this combined analysis too.
    def semantic(value):
        if isinstance(value, dict):
            return {
                key: semantic(item)
                for key, item in value.items()
                if key not in {"acquired_at", "retrieved_at", "ordinal"}
            }
        if isinstance(value, list):
            return [semantic(item) for item in value]
        return value

    # Acquisition bookkeeping, order and duplicate records do not change evidence.
    # Keep content, paths, origins and authority: cross-domain facts can affect the study.
    materials = sorted({json.dumps(semantic(r.to_dict()), sort_keys=True) for r in corpus.records})
    from ..knowledge.contracts import KnowledgeArtifact, KnowledgeStage
    from ..migration.contracts import MigrationStage
    from ..migration.review_policy import review_policy_digest

    value = {
        "route": project.artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.EXPERIMENT_ROUTE
        ).digest,
        "gaps": project.artifact(
            AcquisitionStage.EVIDENCE_CLOSURE, AcquisitionArtifact.EVIDENCE_GAP_REGISTER
        ).digest,
        "query": project.artifact(
            KnowledgeStage.KNOWLEDGE_BASE, KnowledgeArtifact.QUERY_CONTRACT
        ).digest,
        "rules": {
            stage.value: review_policy_digest(project, None, stage)
            for stage in (S.STUDY, MigrationStage.CONTRACTS)
        },
        "materials": materials,
        "repositories": project.artifact(
            AcquisitionStage.REPOSITORY_ACQUISITION, AcquisitionArtifact.REPOSITORY_MANIFEST
        ).digest,
        "environment": project.artifact(
            EnvironmentStage.RECOVERY, EnvironmentArtifact.MODE_RECORD
        ).digest,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def remember(project, *, combined=False):
    report = project.artifact(S.STUDY, A.REPORT)
    project.record_event(
        RunEvent.TASK_REUSE,
        {
            "stage": S.STUDY.value,
            "identity": identity(project),
            "report": report.digest,
            "analysis_route": project.artifact(S.STUDY, A.ROUTE).digest,
            "combined_analysis": combined,
            "knowledge_quality": project.artifact(S.STUDY, A.KNOWLEDGE_QUALITY).digest,
        },
    )


def restore(project):
    # Use ledgered receipts, not mutable cache hints or old PASS state.
    import sqlite3

    from ..core.events import RunEvent

    with sqlite3.connect(project.database_path) as db:
        rows = db.execute(
            "SELECT payload FROM events WHERE event_type=? ORDER BY sequence DESC",
            (RunEvent.TASK_REUSE.value,),
        ).fetchall()
    wanted = identity(project)
    for (raw,) in rows:
        receipt = json.loads(raw)
        if receipt.get("stage") != S.STUDY.value:
            continue
        # A newer correction supersedes older conclusions even if inputs return
        # to an old value. Never resurrect an older matching report.
        if not receipt.get("combined_analysis") or receipt.get("identity") != wanted:
            return False
        matching = [
            r
            for r in project.artifact_refs(stage=S.STUDY)
            if r.kind == A.REPORT.value and r.digest == receipt["report"]
        ]
        if not matching:
            return False
        qualities = [
            r
            for r in project.artifact_refs(stage=S.STUDY)
            if r.kind == A.KNOWLEDGE_QUALITY.value and r.digest == receipt.get("knowledge_quality")
        ]
        if not qualities:
            return False
        routes = [r for r in project.artifact_refs(stage=S.STUDY)
                  if r.kind == A.ROUTE.value and r.digest == receipt["analysis_route"]]
        if not routes:
            return False
        route = project.artifacts.read(routes[-1])
        quality = project.artifacts.read(qualities[-1])
        data = project.artifacts.read(matching[-1])
        if project.stage(S.STUDY).status is StageStatus.READY:
            project.start(S.STUDY)
        project.finalize_stage(
            S.STUDY,
            (
                GeneratedArtifact(A.ROUTE, route, "reused:analysis-route"),
                GeneratedArtifact(A.REPORT, data, f"reused:target-study:{receipt['report']}"),
                GeneratedArtifact(A.KNOWLEDGE_QUALITY, quality, "reused:target-knowledge-quality"),
            ),
            message="Reused target study: relevant inputs unchanged",
        )
        from ..migration.analysis_delivery import record

        record(project)
        return True
    return False
