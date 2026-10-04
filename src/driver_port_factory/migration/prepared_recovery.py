"""Recover a prepared receipt after acceptance, using the accepted job binding."""
import json
import sqlite3

from .target_framework import framework_stage
from pathlib import Path

from ..core.models import StageStatus
from ..codex.contracts import CodexArtifact as C


def accepted_policy(project, stage, report):
    refs = project.current_artifact_refs(stage=stage)
    for work in reversed(refs):
        if work.kind != C.WORK_REPORT.value or work.digest != report.digest:
            continue
        prefix = 'generated:codex-work-report:'
        if not work.source.startswith(prefix):
            continue
        ordinal, digest = work.source[len(prefix):].split(':', 1)
        jobs = [r for r in refs if r.kind == C.JOB_RESULT.value
                and r.ordinal == int(ordinal) and r.digest == digest]
        if len(jobs) != 1:
            return None
        path = Path(jobs[0].source).with_suffix('.metrics.json')
        if not path.is_file():
            return None
        return json.loads(path.read_text()).get('policy_sha256')
    return None


def recoverable(project, owner, consumers):
    if project.stage(owner).status is not StageStatus.PASS:
        return False
    with sqlite3.connect(f'{project.database_path.as_uri()}?mode=ro', uri=True) as db:
        row = db.execute("SELECT MAX(sequence) FROM events WHERE event_type='stage.completed' "
                         "AND json_extract(payload,'$.stage')=? AND json_extract(payload,'$.outcome')='PASS'",
                         (owner.value,)).fetchone()
        if row[0] is None:
            return False
        retries = db.execute("SELECT payload FROM events WHERE sequence>? AND event_type='stage.retried'",
                             (row[0],)).fetchall()
    return not any(json.loads(raw)['stage'] in {s.value for s in consumers} for (raw,) in retries)


def analysis(project):
    from ..target_study.contracts import TargetStudyStage as TS, TargetStudyArtifact as T
    from ..target_study.reuse import remember
    from .contracts import MigrationStage as S
    from .analysis_delivery import record
    if not recoverable(project, TS.STUDY, (TS.STUDY, S.CONTRACTS)):
        return False
    report = project.artifact(TS.STUDY, T.REPORT)
    policy = accepted_policy(project, TS.STUDY, report)
    if policy and record(project, job_policy=policy):
        remember(project, combined=True)
        return True
    return False


def delivery(project):
    from .contracts import MigrationStage as S, MigrationArtifact as A
    from .target_framework import _validate_files
    from .repair_execution import prepare_delivery
    from ..core.models import WorkflowError
    if project.config.unified_implementation:
        return False
    if not recoverable(project, S.TARGET_FRAMEWORK_ENABLEMENT,
                       (S.TARGET_FRAMEWORK_ENABLEMENT, S.DRIVER_IMPLEMENTATION, S.ARTIFACT_PREPARATION)):
        return False
    report = project.artifact(framework_stage(project), A.TARGET_FRAMEWORK_REPORT)
    policy = accepted_policy(project, S.TARGET_FRAMEWORK_ENABLEMENT, report)
    if not policy:
        return False
    bundle = project.load_json_artifact(framework_stage(project), A.TARGET_FRAMEWORK_BUNDLE)
    try:
        _validate_files(project.root, bundle['target_worktree'], bundle['files'])
    except WorkflowError:
        return False
    return prepare_delivery(project, project.artifacts.path_for_digest(report.digest),
                            job_policy=policy, owner=S.TARGET_FRAMEWORK_ENABLEMENT)
