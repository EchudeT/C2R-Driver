from __future__ import annotations

import json
from pathlib import Path

from .artifacts import ArtifactStore
from .models import ActorRole, ProjectConfig, StageStatus, WorkflowError
from .policy import validate_project_config
from .store import RunStore, canonical_json
from .validation import validate_artifact
from .workflow import workflow_for


class Project:
    CONTROL_DIR = ".dpf"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.control = self.root / self.CONTROL_DIR
        self.database_path = self.control / "run.sqlite3"
        self.config_path = self.control / "project.json"
        if not self.database_path.exists() or not self.config_path.exists():
            raise WorkflowError(f"not a Driver Port Factory project: {self.root}")
        self.store = RunStore(self.database_path)
        self.artifacts = ArtifactStore(self.control / "cas")

    @classmethod
    def initialize(cls, root: Path, config: ProjectConfig) -> Project:
        validate_project_config(config)
        root = root.resolve()
        control = root / cls.CONTROL_DIR
        if control.exists():
            raise WorkflowError(f"project already initialized: {root}")
        root.mkdir(parents=True, exist_ok=True)
        control.mkdir(parents=True)
        config_path = control / "project.json"
        config_json = (
            json.dumps(config.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        )
        config_path.write_text(config_json, encoding="utf-8")
        store = RunStore.create(control / "run.sqlite3", config, workflow_for(config))
        artifact_store = ArtifactStore(control / "cas")
        manifest = artifact_store.put_bytes(
            canonical_json(config.to_dict()).encode("utf-8"),
            kind="project_manifest",
            source=str(config_path),
        )
        store.start_stage("project_init", config.actor_role)
        store.register_artifact(manifest, stage="project_init")
        store.complete_stage("project_init", StageStatus.PASS)
        return cls(root)

    @property
    def config(self) -> ProjectConfig:
        return self.store.config

    def add_artifact(self, stage: str, kind: str, path: Path, *, direction: str = "output") -> str:
        data = path.resolve().read_bytes()
        validate_artifact(kind, data)
        ref = self.artifacts.put_bytes(data, kind=kind, source=str(path.resolve()))
        self.store.register_artifact(ref, stage=stage, direction=direction)
        return ref.digest

    def add_bytes(
        self,
        stage: str,
        kind: str,
        data: bytes,
        *,
        source: str | None = None,
        direction: str = "output",
    ) -> str:
        validate_artifact(kind, data)
        ref = self.artifacts.put_bytes(data, kind=kind, source=source)
        self.store.register_artifact(ref, stage=stage, direction=direction)
        return ref.digest

    def start(self, stage: str) -> None:
        self.store.start_stage(stage, self.config.actor_role)

    def complete(self, stage: str, outcome: StageStatus, *, message: str | None = None) -> None:
        self.store.complete_stage(stage, outcome, message=message)

    def ensure_role(self, *roles: ActorRole) -> None:
        if self.config.actor_role not in roles:
            expected = ", ".join(role.value for role in roles)
            raise WorkflowError(
                f"operation requires role {expected}, current role is {self.config.actor_role.value}"
            )

    def artifact(self, stage: str, kind: str, *, direction: str = "output"):
        refs = [
            ref
            for ref in self.store.artifact_refs(stage=stage, direction=direction)
            if ref.kind == kind
        ]
        if len(refs) != 1:
            raise WorkflowError(f"expected one {kind} artifact in {stage}, found {len(refs)}")
        return refs[0]

    def load_json_artifact(self, stage: str, kind: str, *, direction: str = "output") -> dict:
        ref = self.artifact(stage, kind, direction=direction)
        value = json.loads(self.artifacts.read(ref))
        if not isinstance(value, dict):
            raise WorkflowError(f"{kind} in {stage} is not a JSON object")
        return value
