"""Persistent worker and reviewer conversations with isolated project identities."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from pathlib import Path

from ..core.contracts import StageKey
from ..core.models import EvaluationMode, StageStatus
from ..core.project import Project
from ..migration.contracts import MigrationArtifact, MigrationStage
from .policy import CodexExecutionGrant


def compact_token_limit(project: Project, stage: StageKey, thread_id: str | None) -> int:
    """Use a lower native threshold only for the first implementation invocation.

    Codex measures live context; billing counters are cumulative and cannot decide
    whether compaction is needed. Existing metrics prevent reapplying on retries.
    """
    default = 224000
    if (stage is not MigrationStage.DRIVER_IMPLEMENTATION or not thread_id
            or project.config.evaluation_mode is not EvaluationMode.DEVELOPER_EVIDENCE):
        return default
    if project.stage(MigrationStage.CONTRACTS).status is not StageStatus.PASS:
        return default
    if any((project.control / "codex").glob("driver_implementation-*.metrics.json")):
        return default
    refs = project.artifact_refs(stage=MigrationStage.CONTRACTS)
    required = {MigrationArtifact.CONTRACTS.value, MigrationArtifact.TEST_PORT_MATRIX.value}
    present = {ref.kind for ref in refs
               if ref.kind in required and project.artifacts.path_for_digest(ref.digest).is_file()}
    return 160000 if required <= present else default


def session_key(
    project: Project, stage: StageKey, grant: CodexExecutionGrant, model: str | None, backend: str,
) -> str:
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    config = home / "config.toml"
    settings = tomllib.loads(config.read_text()) if config.is_file() else {}
    provider = settings.get("model_provider", "openai")
    connection = {
        "provider": provider,
        "settings": settings.get("model_providers", {}).get(provider, {}),
        "model": model or settings.get("model"),
        "base_url": settings.get("openai_base_url"),
    }
    if stage in {MigrationStage.FINAL_EVIDENCE_REVIEW, MigrationStage.ANALYSIS_REVIEW}:
        conversation = "reviewer"
    elif project.config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE:
        conversation = "worker"
    else:
        conversation = stage.value
    identity = [
        "persistent-conversations-v2", str(project.root), project.config.actor_role.value,
        project.config.evaluation_mode.value, conversation, model, backend, str(home),
        hashlib.sha256(json.dumps(connection, sort_keys=True).encode()).hexdigest(),
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
    project: Project, key: str, thread_id: str | None, documents: dict[str, str],
    inputs: dict[str, str] | None = None,
) -> None:
    if not thread_id:
        return
    path = project.control / "codex" / "sessions" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"thread_id": thread_id, "documents": documents,
                                     "inputs": inputs or {}}))
    temporary.replace(path)


def input_changes(context: dict, known: dict[str, str]) -> tuple[dict, dict[str, str]]:
    """Annotate immutable input identities; never claim that the model read their contents."""
    supplied = dict(known)
    changes = {"new": [], "changed": [], "unchanged": []}

    def visit(value, location):
        if isinstance(value, dict):
            if all(isinstance(value.get(key), str) for key in ("kind", "digest", "path")):
                key = "/".join(location)
                digest = value["digest"]
                status = ("new" if key not in known else
                          "unchanged" if known[key] == digest else "changed")
                changes[status].append(key)
                supplied[key] = digest
            else:
                for key, child in value.items():
                    visit(child, (*location, key))

    visit(context, ())
    return {**context, "input_changes": changes}, supplied
