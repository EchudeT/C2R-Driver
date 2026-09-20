from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .contracts import ArtifactKey, EventKey, StageKey
from .models import (
    ActorRole,
    ArtifactDirection,
    ArtifactRef,
    FileArtifact,
    GeneratedArtifact,
    ProjectConfig,
    RepairExhausted,
    StageStatus,
    StageView,
    WorkflowError,
)
from .policy import validate_project_config
from .store import _RunPersistence
from .validation import ArtifactInputs, BundleValidationContext, ValidationRegistry
from .workflow import WorkflowDefinition


class Project:
    CONTROL_DIR = ".dpf"
    RUN_DATABASE = "run.sqlite3"

    def __init__(
        self,
        root: Path,
        workflow: WorkflowDefinition,
        validators: ValidationRegistry,
        *,
        read_only: bool = False,
    ) -> None:
        self.root = root.resolve()
        self.control = self.root / self.CONTROL_DIR
        self.database_path = self.control / self.RUN_DATABASE
        self.config_path = self.control / "project.json"
        if not self.database_path.exists() or not self.config_path.exists():
            raise WorkflowError(f"not a Driver Port Factory project: {self.root}")
        self.workflow = workflow
        self.validators = validators
        self._persistence = _RunPersistence(
            self.database_path,
            workflow,
            read_only=read_only,
        )
        self.artifacts = ArtifactStore(self.control / "cas")

    @classmethod
    def initialize(
        cls,
        root: Path,
        config: ProjectConfig,
        workflow: WorkflowDefinition,
        validators: ValidationRegistry,
        *,
        initial_stage: StageKey,
        manifest_kind: ArtifactKey,
    ) -> Project:
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
        _RunPersistence.create(control / cls.RUN_DATABASE, config, workflow)
        project = cls(root, workflow, validators)
        project.start(initial_stage)
        project.finalize_stage(
            initial_stage,
            (FileArtifact(manifest_kind, config_path),),
        )
        return project

    @property
    def config(self) -> ProjectConfig:
        return self._persistence.config

    def record_artifact(
        self,
        stage: StageKey,
        artifact: FileArtifact | GeneratedArtifact,
        *,
        direction: ArtifactDirection = ArtifactDirection.OUTPUT,
    ) -> ArtifactRef:
        if direction is ArtifactDirection.OUTPUT:
            self.workflow.output_contract(stage).require_auxiliary(artifact.kind.value)
        data, source = self._materialize(artifact)
        self.validators.validate(artifact.kind, data)
        content = self.artifacts.put_bytes(data, kind=artifact.kind.value)
        ref = ArtifactRef(content, source)
        return self._persistence.register_artifact(ref, stage=stage, direction=direction)

    def finalize_stage(
        self,
        stage: StageKey,
        artifacts: Iterable[FileArtifact | GeneratedArtifact],
        *,
        message: str | None = None,
    ) -> tuple[str, ...]:
        """Validate and persist a successful stage's complete output set."""

        submitted = tuple(artifacts)
        self.workflow.output_contract(stage).validate_final_bundle(
            artifact.kind.value for artifact in submitted
        )
        artifacts_with_data = []
        for artifact in submitted:
            data, source = self._materialize(artifact)
            self.validators.validate(artifact.kind, data)
            content = self.artifacts.put_bytes(data, kind=artifact.kind.value)
            ref = ArtifactRef(content, source)
            artifacts_with_data.append((ref, data))
        if self.validators.has_bundle_validator(stage):
            self._validate_stage_bundle(stage, tuple(artifacts_with_data))
        refs = [ref for ref, _ in artifacts_with_data]
        self._persistence._commit_validated_stage(stage, refs, message=message)
        return tuple(ref.digest for ref in refs)

    def _validate_stage_bundle(
        self, stage: StageKey, artifacts_with_data: tuple[tuple[ArtifactRef, bytes], ...]
    ) -> None:
        # Markdown-only stages have no cross-artifact validator. Loading gigabytes
        # of upstream ASTs there provides no extra check and can exhaust memory.
        if not self.validators.has_bundle_validator(stage):
            return
        dependency_artifacts = ArtifactInputs((
            ref
            for dependency in self.workflow.spec(stage).dependencies
            for ref in self._persistence.current_artifact_refs(
                stage=dependency,
                direction=ArtifactDirection.OUTPUT,
            )
        ), self.artifacts.read)
        current_stage_artifacts = ArtifactInputs((
            ref
            for ref in self._persistence.current_artifact_refs(
                stage=stage,
                direction=ArtifactDirection.OUTPUT,
            )
        ), self.artifacts.read)
        self.validators.validate_bundle(
            stage,
            BundleValidationContext(
                self.root,
                artifacts_with_data,
                dependency_artifacts,
                current_stage_artifacts,
            ),
        )

    def _materialize(self, artifact: FileArtifact | GeneratedArtifact) -> tuple[bytes, str]:
        if isinstance(artifact, FileArtifact):
            resolved = artifact.path.resolve()
            if resolved != self.root and self.root not in resolved.parents:
                raise WorkflowError(f"artifact file escapes project root: {resolved}")
            if not resolved.is_file():
                raise WorkflowError(f"artifact is not a regular project file: {resolved}")
            return resolved.read_bytes(), str(resolved)
        return artifact.data, artifact.source

    def start(self, stage: StageKey) -> None:
        self._persistence.start_stage(stage, self.config.actor_role)

    def retry_feedback(self, stage: StageKey) -> dict[str, str] | None:
        return self._persistence.retry_feedback(stage)

    def retry_from(self, stage: StageKey, *, trigger: StageKey, reason: str,
                   progress: object | None = None) -> None:
        try:
            self._persistence.retry_from(
                stage, trigger=trigger, actor_role=self.config.actor_role, reason=reason,
                progress=progress,
            )
        except RepairExhausted as error:
            self.complete(trigger, StageStatus.BLOCKED, message=str(error))

    def reopen_blocked(self, stage: StageKey, *, reason: str) -> None:
        self._persistence.reopen_blocked(stage, self.config.actor_role, reason=reason)

    def complete(
        self, stage: StageKey, outcome: StageStatus, *, message: str | None = None
    ) -> None:
        if outcome is StageStatus.PASS:
            raise WorkflowError("PASS is available only through finalize_stage")
        self._persistence.complete_stage(stage, outcome, message=message)

    def stage(self, stage: StageKey) -> StageView:
        return self._persistence.stage(stage)

    def stages(self) -> list[StageView]:
        return self._persistence.stages()

    def artifact_refs(
        self,
        *,
        stage: StageKey | None = None,
        direction: ArtifactDirection | None = None,
    ) -> list[ArtifactRef]:
        return self._persistence.artifact_refs(stage=stage, direction=direction)

    def current_artifact_refs(
        self,
        *,
        stage: StageKey,
        direction: ArtifactDirection | None = None,
    ) -> list[ArtifactRef]:
        return self._persistence.current_artifact_refs(stage=stage, direction=direction)

    def wait_for_user(self, stage: StageKey, *, question: str) -> None:
        self._persistence.wait_for_user(stage, question=question)

    def resume_after_user(self, stage: StageKey, *, answer: str) -> None:
        self._persistence.resume_after_user(stage, self.config.actor_role, answer=answer)

    def record_event(self, event_type: EventKey, payload: dict[str, Any]) -> str:
        return self._persistence.record_event(event_type, payload)

    def verify_integrity(self, *, artifacts: bool = True) -> None:
        self._persistence.validate_integrity()
        self._verify_project_config()
        if not artifacts:
            return
        verified: set[tuple[str, int, str]] = set()
        for ref in self.artifact_refs():
            identity = (ref.digest, ref.size, ref.cas_path)
            if identity in verified:
                continue
            if not self.artifacts.verify(ref):
                raise WorkflowError(f"artifact failed integrity verification: {ref.digest}")
            verified.add(identity)

    def _verify_project_config(self) -> None:
        try:
            raw = self.config_path.read_bytes()
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError("project configuration is not an object")
            file_config = ProjectConfig.from_dict(value)
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
        ) as error:
            raise WorkflowError("project configuration is unreadable or invalid") from error
        if file_config != self.config:
            raise WorkflowError("project configuration differs from run database")
        manifests = [ref for ref in self.artifact_refs() if ref.source == str(self.config_path)]
        if len(manifests) != 1 or self.artifacts.read(manifests[0]) != raw:
            raise WorkflowError("project configuration differs from frozen project manifest")

    def verify_event_chain(self) -> bool:
        return self._persistence.verify_event_chain()

    def ensure_role(self, *roles: ActorRole) -> None:
        if self.config.actor_role not in roles:
            expected = ", ".join(role.value for role in roles)
            raise WorkflowError(
                f"operation requires role {expected}, current role is "
                f"{self.config.actor_role.value}"
            )

    def artifact(
        self,
        stage: StageKey,
        kind: ArtifactKey,
        *,
        direction: ArtifactDirection = ArtifactDirection.OUTPUT,
    ) -> ArtifactRef:
        refs = [
            ref
            for ref in self._persistence.current_artifact_refs(
                stage=stage,
                direction=direction,
            )
            if ref.kind == kind.value
        ]
        if len(refs) != 1:
            raise WorkflowError(
                f"expected one {kind.value} artifact in {stage.value}, found {len(refs)}"
            )
        return refs[0]

    def load_json_artifact(
        self,
        stage: StageKey,
        kind: ArtifactKey,
        *,
        direction: ArtifactDirection = ArtifactDirection.OUTPUT,
    ) -> dict[str, Any]:
        ref = self.artifact(stage, kind, direction=direction)
        try:
            value = json.loads(self.artifacts.read(ref))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkflowError(f"{kind.value} in {stage.value} is not valid UTF-8 JSON") from error
        if not isinstance(value, dict):
            raise WorkflowError(f"{kind.value} in {stage.value} is not a JSON object")
        return value
