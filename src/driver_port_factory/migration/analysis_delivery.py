"""One analysis task, separate ledger artifacts, and targeted contract repairs."""

import json
import sqlite3
from pathlib import Path

from ..acquisition.contracts import AcquisitionArtifact as A, AcquisitionStage as S
from ..core.events import RunEvent
from ..environment.contracts import EnvironmentArtifact as E, EnvironmentStage as ES
from ..knowledge.contracts import KnowledgeArtifact as K, KnowledgeStage as KS
from ..target_study.contracts import TargetStudyArtifact as T, TargetStudyStage as TS
from .contracts import MigrationStage
from .review_policy import review_policy_digest


def identity(project):
    inputs = ((S.REPOSITORY_ACQUISITION, A.REPOSITORY_MANIFEST),
              (S.EVIDENCE_CLOSURE, A.MATERIALS_MANIFEST),
              (S.EVIDENCE_CLOSURE, A.EVIDENCE_GAP_REGISTER),
              (ES.RECOVERY, E.MODE_RECORD), (ES.RECOVERY, E.EXPERIMENT_ROUTE),
              (KS.KNOWLEDGE_BASE, K.QUERY_CONTRACT), (TS.STUDY, T.REPORT))
    return {"inputs": {kind.value: project.artifact(stage, kind).digest for stage, kind in inputs},
            "rules": {stage.value: review_policy_digest(project, None, stage)
                      for stage in (TS.STUDY, MigrationStage.CONTRACTS)}}


def record(project, *, job_policy=None):
    value = identity(project)
    if job_policy is not None and value["rules"][TS.STUDY.value] != job_policy:
        return False
    project.record_event(RunEvent.ANALYSIS_PREPARED, value)
    return True


def prepared(project) -> Path | None:
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute("SELECT sequence,payload FROM events WHERE event_type=? "
                         "ORDER BY sequence DESC LIMIT 1", (RunEvent.ANALYSIS_PREPARED.value,)).fetchone()
        if row is None:
            return None
        # A contract-only repair must not consume the old combined analysis again.
        if db.execute("SELECT 1 FROM events WHERE sequence>? AND event_type='stage.retried' "
                      "AND json_extract(payload,'$.stage') IN "
                      "('target_platform_study','migration_contracts') LIMIT 1", (row[0],)).fetchone():
            return None
    receipt = json.loads(row[1])
    if receipt != identity(project):
        return None
    ref = project.artifact(TS.STUDY, T.REPORT)
    project.artifacts.read(ref)  # Verify CAS content before propagating its evidence roles.
    return project.artifacts.path_for_digest(ref.digest)
