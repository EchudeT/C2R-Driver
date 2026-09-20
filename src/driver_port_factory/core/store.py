from __future__ import annotations

import json
import hashlib
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .artifact_persistence import (
    load_current_occurrences,
    load_occurrences,
    next_ordinal,
    persist_occurrences,
)
from .contracts import EventKey, StageKey
from .events import ArtifactEvent, RunEvent, StageEvent
from .integrity import validate_state_projection
from .ledger import append_event, verify_event_chain
from .models import (
    ActorRole,
    ArtifactDirection,
    ArtifactRef,
    ProjectConfig,
    RepairExhausted,
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

    def __init__(
        self,
        path: Path,
        workflow: WorkflowDefinition,
        *,
        read_only: bool = False,
    ) -> None:
        self.path = path.resolve()
        if not self.path.exists():
            raise WorkflowError(f"run database does not exist: {self.path}")
        self.workflow = workflow
        self.read_only = read_only
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
        # Codex queries run after the controller closes its transaction. Immutable mode
        # keeps those readers from creating WAL/SHM sidecars outside their writable sandbox.
        connection = sqlite3.connect(
            f"{self.path.as_uri()}?mode=ro&immutable=1" if self.read_only else self.path,
            uri=self.read_only,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            if not self.read_only:
                connection.commit()
        except Exception:
            if not self.read_only:
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

    def retry_feedback(self, name: StageKey) -> dict[str, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT sequence, event_type, payload FROM events WHERE json_extract(payload, '$.stage') = ? "
                "AND event_type IN (?, ?) ORDER BY sequence DESC LIMIT 1",
                (name.value, StageEvent.RETRIED.value, StageEvent.COMPLETED.value),
            ).fetchone()
            if row is None or row["event_type"] != StageEvent.RETRIED.value:
                return None
            payload = json.loads(row["payload"])
            root = payload.get("repair_root", payload["stage"])
            completion = connection.execute(
                "SELECT sequence FROM events WHERE event_type=? AND sequence>? "
                "AND json_extract(payload, '$.stage')=? "
                "AND json_extract(payload, '$.outcome')='PASS' ORDER BY sequence DESC LIMIT 1",
                (StageEvent.COMPLETED.value, row["sequence"], root),
            ).fetchone()
            state = connection.execute("SELECT status FROM stages WHERE name=?", (root,)).fetchone()
        resolved = completion is not None and state is not None and state["status"] == "PASS"
        return {**{key: payload[key] for key in ("stage", "trigger", "reason", "repair_root")
                   if key in payload}, "status": "RESOLVED" if resolved else "OPEN"}

    def retry_from(
        self,
        name: StageKey,
        *,
        trigger: StageKey,
        actor_role: ActorRole,
        reason: str,
        progress: object | None = None,
    ) -> None:
        configured_role = self.config.actor_role
        if not reason.strip():
            raise WorkflowError("stage retry requires a reason")
        with self._connect() as connection:
            target = self._stages.transition_row(connection, name, StageStatus.PASS)
            source = self._stages.transition_row(connection, trigger, StageStatus.RUNNING)
            if target["position"] >= source["position"]:
                raise WorkflowError("stage retry target must precede its trigger")
            allowed = self._stages.allowed_roles(target, name)
            if actor_role not in allowed or actor_role is not configured_role:
                raise WorkflowError(f"role {actor_role.value} may not retry {name.value}")

            # Evidence paths and prose are feedback, not proof of progress.
            inputs = load_current_occurrences(connection, stage_name=name.value,
                                              direction=ArtifactDirection.OUTPUT)
            identity = sorted((ref.kind, ref.digest) for ref in inputs
                              if not ref.kind.startswith("codex_"))
            fingerprint = hashlib.sha256(json.dumps(
                [name.value, trigger.value, progress if progress is not None else identity], sort_keys=True
            ).encode()).hexdigest()
            repeated = connection.execute(
                "SELECT count(*) FROM events WHERE event_type = ? "
                "AND json_extract(payload, '$.stage') = ? "
                "AND json_extract(payload, '$.repair_fingerprint') = ?",
                (StageEvent.RETRIED.value, name.value, fingerprint),
            ).fetchone()[0]
            if repeated >= 3:
                raise RepairExhausted(
                    "Repeated prerequisite repair without changed substantive inputs; "
                    "preserved in ledger. Resolve the concrete blocker before resuming."
                )

            descendants = self.workflow.descendants(name)
            if trigger.value not in descendants:
                raise WorkflowError("repair target is not a data prerequisite of the current task")
            affected = [row for row in connection.execute(
                "SELECT name, position FROM stages ORDER BY position").fetchall()
                if row["name"] in descendants]
            for row in affected:
                stage = self.workflow.parse_stage(row["name"])
                status = StageStatus.READY if stage is name else StageStatus.PENDING
                boundaries = {
                    direction.value: next_ordinal(connection, stage.value, direction)
                    for direction in ArtifactDirection
                }
                connection.execute(
                    """
                    UPDATE stages
                    SET status = ?, started_at = NULL, completed_at = NULL, message = NULL
                    WHERE name = ?
                    """,
                    (status.value, stage.value),
                )
                append_event(
                    connection,
                    StageEvent.RETRIED,
                    {
                        "stage": stage.value,
                        "repair_root": name.value,
                        "status": status.value,
                        "trigger": trigger.value,
                        "actor_role": actor_role.value,
                        "reason": reason,
                        "repair_fingerprint": fingerprint,
                        "artifact_boundaries": boundaries,
                    },
                )

    def reopen_blocked(self, name: StageKey, actor_role: ActorRole, *, reason: str) -> None:
        if not reason.strip():
            raise WorkflowError("reopening a blocker requires the resolution reason")
        with self._connect() as connection:
            row = self._stages.transition_row(connection, name, StageStatus.BLOCKED)
            if actor_role is not self.config.actor_role or actor_role not in self._stages.allowed_roles(row, name):
                raise WorkflowError(f"role {actor_role.value} may not reopen {name.value}")
            boundaries = {direction.value: next_ordinal(connection, name.value, direction)
                          for direction in ArtifactDirection}
            connection.execute(
                "UPDATE stages SET status = ?, started_at = NULL, completed_at = NULL, "
                "message = NULL WHERE name = ?", (StageStatus.READY.value, name.value))
            append_event(connection, StageEvent.RETRIED, {
                "stage": name.value, "status": StageStatus.READY.value,
                "trigger": name.value, "actor_role": actor_role.value,
                "reason": "operator resolved blocker: " + reason,
                "artifact_boundaries": boundaries,
            })

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
            existing = load_current_occurrences(
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

    def current_artifact_refs(
        self,
        *,
        stage: StageKey,
        direction: ArtifactDirection | None = None,
    ) -> list[ArtifactRef]:
        with self._connect() as connection:
            return load_current_occurrences(
                connection,
                stage_name=stage.value,
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
