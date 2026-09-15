from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .artifact_persistence import load_occurrences, persist_occurrences
from .contracts import EventKey, StageKey
from .events import ArtifactEvent, RunEvent, StageEvent
from .integrity import validate_state_projection
from .ledger import append_event, verify_event_chain
from .models import (
    ActorRole,
    ArtifactDirection,
    ArtifactRef,
    ProjectConfig,
    StageStatus,
    StageView,
    WorkflowError,
    utc_now,
)
from .schema import create_schema, initialize_run, validate_schema
from .stage_persistence import _StagePersistence
from .workflow import WorkflowDefinition


class _RunPersistence:
    """Internal transactional persistence; successful stage commits enter via Project only."""

    def __init__(self, path: Path, workflow: WorkflowDefinition) -> None:
        self.path = path.resolve()
        if not self.path.exists():
            raise WorkflowError(f"run database does not exist: {self.path}")
        self.workflow = workflow
        self._stages = _StagePersistence(workflow)
        with self._connect() as connection:
            validate_schema(connection)

    @classmethod
    def create(
        cls,
        path: Path,
        config: ProjectConfig,
        workflow: WorkflowDefinition,
    ) -> _RunPersistence:
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise WorkflowError(f"refusing to overwrite existing run database: {path}")
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        try:
            create_schema(connection)
            initialize_run(connection, config, workflow)
            append_event(connection, RunEvent.CREATED, {"project": config.to_dict()})
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        return cls(path, workflow)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @property
    def config(self) -> ProjectConfig:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'project_config'"
            ).fetchone()
        if row is None:
            raise WorkflowError("run database has no project configuration")
        try:
            value = json.loads(row["value"])
            if not isinstance(value, dict):
                raise TypeError("project configuration metadata is not an object")
            return ProjectConfig.from_dict(value)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise WorkflowError("run database has invalid project configuration") from error

    def record_event(self, event_type: EventKey, payload: dict[str, Any]) -> str:
        with self._connect() as connection:
            return append_event(connection, event_type, payload)

    def stage(self, name: StageKey) -> StageView:
        with self._connect() as connection:
            return self._stages.stage(connection, name)

    def stages(self) -> list[StageView]:
        with self._connect() as connection:
            return self._stages.stages(connection)

    def validate_integrity(self) -> None:
        with self._connect() as connection:
            validate_schema(connection)
            if not verify_event_chain(connection):
                raise WorkflowError("run event ledger failed integrity verification")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise WorkflowError("run database has broken artifact references")
            validate_state_projection(connection, self.workflow)
        self.stages()

    def refresh_ready(self) -> None:
        with self._connect() as connection:
            self._stages.refresh_ready(connection)

    def start_stage(self, name: StageKey, actor_role: ActorRole) -> None:
        configured_role = self.config.actor_role
        with self._connect() as connection:
            row = self._stages.transition_row(connection, name, StageStatus.READY)
            allowed = self._stages.allowed_roles(row, name)
            if actor_role not in allowed or actor_role is not configured_role:
                raise WorkflowError(
                    f"role {actor_role.value} may not execute {name.value} in a "
                    f"{configured_role.value} workspace"
                )
            connection.execute(
                "UPDATE stages SET status = ?, started_at = ? WHERE name = ?",
                (StageStatus.RUNNING.value, utc_now(), name.value),
            )
            append_event(
                connection,
                StageEvent.STARTED,
                {"stage": name.value, "actor_role": actor_role.value},
            )

    def wait_for_user(self, name: StageKey, *, question: str) -> None:
        with self._connect() as connection:
            self._stages.transition_row(connection, name, StageStatus.RUNNING)
            connection.execute(
                "UPDATE stages SET status = ?, message = ? WHERE name = ?",
                (StageStatus.WAITING_FOR_USER.value, question, name.value),
            )
            append_event(
                connection,
                StageEvent.WAITING_FOR_USER,
                {"stage": name.value, "question": question},
            )

    def resume_after_user(self, name: StageKey, actor_role: ActorRole, *, answer: str) -> None:
        configured_role = self.config.actor_role
        with self._connect() as connection:
            row = self._stages.transition_row(connection, name, StageStatus.WAITING_FOR_USER)
            allowed = self._stages.allowed_roles(row, name)
            if actor_role not in allowed or actor_role is not configured_role:
                raise WorkflowError(f"role {actor_role.value} may not resume {name.value}")
            connection.execute(
                "UPDATE stages SET status = ?, message = NULL WHERE name = ?",
                (StageStatus.RUNNING.value, name.value),
            )
            append_event(
                connection,
                StageEvent.RESUMED_AFTER_USER,
                {"stage": name.value, "actor_role": actor_role.value, "answer": answer},
            )

    def register_artifact(
        self,
        ref: ArtifactRef,
        *,
        stage: StageKey,
        direction: ArtifactDirection = ArtifactDirection.OUTPUT,
    ) -> ArtifactRef:
        with self._connect() as connection:
            self._stages.transition_row(connection, stage, StageStatus.RUNNING)
            if direction is ArtifactDirection.OUTPUT:
                self.workflow.output_contract(stage).require_auxiliary(ref.kind)
            persisted = persist_occurrences(connection, stage.value, direction, (ref,))[0]
            append_event(
                connection,
                ArtifactEvent.REGISTERED,
                {
                    "stage": stage.value,
                    "direction": direction.value,
                    "artifact": persisted.to_dict(),
                },
            )
            return persisted

    def _commit_validated_stage(
        self,
        name: StageKey,
        refs: Iterable[ArtifactRef],
        *,
        message: str | None = None,
    ) -> tuple[ArtifactRef, ...]:
        artifacts = tuple(refs)
        if not artifacts:
            raise WorkflowError("cannot finalize a stage with an empty artifact set")
        contract = self.workflow.output_contract(name)
        contract.validate_final_bundle(ref.kind for ref in artifacts)
        with self._connect() as connection:
            self._stages.transition_row(connection, name, StageStatus.RUNNING)
            existing = load_occurrences(
                connection,
                stage_name=name.value,
                direction=ArtifactDirection.OUTPUT,
            )
            contract.validate_existing_auxiliary(ref.kind for ref in existing)
            persisted = persist_occurrences(
                connection,
                name.value,
                ArtifactDirection.OUTPUT,
                artifacts,
            )
            contract.validate_persisted(ref.kind for ref in (*existing, *persisted))
            connection.execute(
                "UPDATE stages SET status = ?, completed_at = ?, message = ? WHERE name = ?",
                (StageStatus.PASS.value, utc_now(), message, name.value),
            )
            append_event(
                connection,
                ArtifactEvent.BUNDLE_REGISTERED,
                {
                    "stage": name.value,
                    "direction": ArtifactDirection.OUTPUT.value,
                    "artifacts": [ref.to_dict() for ref in persisted],
                },
            )
            append_event(
                connection,
                StageEvent.COMPLETED,
                {"stage": name.value, "outcome": StageStatus.PASS.value, "message": message},
            )
            self._stages.refresh_ready(connection)
            return persisted

    def artifact_refs(
        self,
        *,
        stage: StageKey | None = None,
        direction: ArtifactDirection | None = None,
    ) -> list[ArtifactRef]:
        with self._connect() as connection:
            return load_occurrences(
                connection,
                stage_name=stage.value if stage is not None else None,
                direction=direction,
            )

    def complete_stage(
        self, name: StageKey, outcome: StageStatus, *, message: str | None = None
    ) -> None:
        if not outcome.terminal or outcome is StageStatus.PASS:
            raise WorkflowError(f"invalid direct completion outcome: {outcome.value}")
        with self._connect() as connection:
            self._stages.transition_row(connection, name, StageStatus.RUNNING)
            connection.execute(
                "UPDATE stages SET status = ?, completed_at = ?, message = ? WHERE name = ?",
                (outcome.value, utc_now(), message, name.value),
            )
            append_event(
                connection,
                StageEvent.COMPLETED,
                {"stage": name.value, "outcome": outcome.value, "message": message},
            )
            self._stages.refresh_ready(connection)

    def verify_event_chain(self) -> bool:
        with self._connect() as connection:
            return verify_event_chain(connection)
