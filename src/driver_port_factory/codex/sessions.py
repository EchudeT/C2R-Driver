"""Persistent worker and reviewer conversations with isolated project identities."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..core.contracts import StageKey
from ..core.models import EvaluationMode
from ..acquisition.contracts import AcquisitionStage
from ..environment.contracts import EnvironmentStage
from ..source_analysis.contracts import SourceAnalysisStage
from ..target_study.contracts import TargetStudyStage
from ..core.project import Project
from ..migration.contracts import MigrationStage
from .policy import CodexExecutionGrant


WORKER_STAGES = frozenset({
    AcquisitionStage.REVISION_SELECTION, AcquisitionStage.EVIDENCE_CLOSURE,
    EnvironmentStage.RECOVERY, TargetStudyStage.STUDY, SourceAnalysisStage.SOURCE_CLOSURE,
    MigrationStage.CONTRACTS, MigrationStage.TEST_ADAPTATION,
    MigrationStage.DRIVER_IMPLEMENTATION, MigrationStage.ARTIFACT_PREPARATION,
    MigrationStage.PUBLIC_QEMU_VALIDATION,
})


def session_key(
    project: Project, stage: StageKey, grant: CodexExecutionGrant, model: str | None, backend: str
) -> str:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    config = home / "config.toml"
    if project.config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE and stage in WORKER_STAGES:
        conversation = "worker"
    elif stage in {MigrationStage.TARGET_COMPLIANCE, MigrationStage.PUBLIC_REPAIR}:
        conversation = "reviewer"
    else:
        conversation = stage.value
    identity = [
        "persistent-conversations-v2", str(project.root), project.config.actor_role.value,
        project.config.evaluation_mode.value, conversation, model, backend, str(home),
        hashlib.sha256(config.read_bytes()).hexdigest() if config.is_file() else "",
    ]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def read_session(project: Project, key: str) -> dict:
    path = project.control / "codex" / "sessions" / f"{key}.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text())
    return value if isinstance(value, dict) else {}


def stage_session(project: Project, stage: StageKey, grant: CodexExecutionGrant,
                  model: str | None, backend: str) -> tuple[str, dict]:
    key = session_key(project, stage, grant, model, backend)
    return key, read_session(project, key)


def save_session(
    project: Project, key: str, thread_id: str | None, documents: dict[str, str]
) -> None:
    if not thread_id:
        return
    path = project.control / "codex" / "sessions" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"thread_id": thread_id, "documents": documents}))
    temporary.replace(path)
