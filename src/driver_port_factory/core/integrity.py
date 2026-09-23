from __future__ import annotations

import json
import sqlite3
from collections import Counter
from collections.abc import Callable
from typing import Any

from .events import ArtifactEvent, RunEvent, StageEvent
from .models import ArtifactDirection, StageStatus, WorkflowError
from .workflow import WorkflowDefinition


class _LedgerProjection:
    def __init__(self, workflow: WorkflowDefinition) -> None:
        self.workflow = workflow
        self.status = {
            stage.name.value: StageStatus.READY if not stage.dependencies else StageStatus.PENDING
            for stage in workflow.stages
        }
        self.artifacts: Counter[tuple[Any, ...]] = Counter()
        self.run_projects: list[Any] = []
        self.handlers: dict[str, Callable[[dict[str, Any]], None]] = {
            RunEvent.CREATED.value: self._run_created,
            RunEvent.WORKER_SUBMISSION.value: self._worker_submission,
            StageEvent.READY.value: self._ready,
            StageEvent.STARTED.value: self._running,
            StageEvent.RETRIED.value: self._retried,
            StageEvent.RESUMED_AFTER_USER.value: self._running,
            StageEvent.WAITING_FOR_USER.value: self._waiting,
            StageEvent.COMPLETED.value: self._completed,
            ArtifactEvent.REGISTERED.value: self._artifact,
            ArtifactEvent.BUNDLE_REGISTERED.value: self._artifact_bundle,
        }

    def consume(self, event_type: str, raw_payload: str) -> None:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as error:
            raise WorkflowError("event ledger contains invalid payload JSON") from error
        if not isinstance(payload, dict):
            raise WorkflowError("event ledger payload must be an object")
        handler = self.handlers.get(event_type)
        if handler is not None:
            handler(payload)

    def _run_created(self, payload: dict[str, Any]) -> None:
        self.run_projects.append(payload.get("project"))

    def _worker_submission(self, payload: dict[str, Any]) -> None:
        stage = payload.get("stage")
        job_id = payload.get("job_id")
        receipt = payload.get("receipt")
        if not isinstance(stage, str) or not isinstance(job_id, str) or not isinstance(receipt, str):
            raise WorkflowError("worker submission event has incomplete identity")
        self.workflow.parse_stage(stage)

    def _ready(self, payload: dict[str, Any]) -> None:
        self.status[self._stage(payload)] = StageStatus.READY

    def _running(self, payload: dict[str, Any]) -> None:
        self.status[self._stage(payload)] = StageStatus.RUNNING

    def _waiting(self, payload: dict[str, Any]) -> None:
        self.status[self._stage(payload)] = StageStatus.WAITING_FOR_USER

    def _retried(self, payload: dict[str, Any]) -> None:
        try:
            status = StageStatus(payload.get("status"))
        except (TypeError, ValueError) as error:
            raise WorkflowError("stage retry event has invalid status") from error
        if status not in {StageStatus.READY, StageStatus.PENDING}:
            raise WorkflowError("stage retry event has invalid status")
        trigger = payload.get("trigger")
        if not isinstance(trigger, str):
            raise WorkflowError("stage retry event has no trigger")
        self.workflow.parse_stage(trigger)
        boundaries = payload.get("artifact_boundaries")
        if not isinstance(boundaries, dict) or set(boundaries) != {
            direction.value for direction in ArtifactDirection
        }:
            raise WorkflowError("stage retry event has invalid artifact boundaries")
        if any(not isinstance(value, int) or value < 0 for value in boundaries.values()):
            raise WorkflowError("stage retry event has invalid artifact boundaries")
        self.status[self._stage(payload)] = status

    def _completed(self, payload: dict[str, Any]) -> None:
        try:
            self.status[self._stage(payload)] = StageStatus(payload.get("outcome"))
        except (TypeError, ValueError) as error:
            raise WorkflowError("stage completion event has invalid outcome") from error

    def _artifact(self, payload: dict[str, Any]) -> None:
        self.artifacts[_artifact_event(payload, payload.get("artifact"))] += 1

    def _artifact_bundle(self, payload: dict[str, Any]) -> None:
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            raise WorkflowError("artifact bundle event has invalid artifacts")
        for artifact in artifacts:
            self.artifacts[_artifact_event(payload, artifact)] += 1

    def _stage(self, payload: dict[str, Any]) -> str:
        stage = payload.get("stage")
        if not isinstance(stage, str):
            raise WorkflowError("stage event has no stage identity")
        self.workflow.parse_stage(stage)
        return stage


def validate_state_projection(
    connection: sqlite3.Connection,
    workflow: WorkflowDefinition,
) -> None:
    rows = connection.execute("SELECT event_type, payload FROM events ORDER BY sequence").fetchall()
    projection = _LedgerProjection(workflow)
    for row in rows:
        projection.consume(row["event_type"], row["payload"])
    _validate_project_config(connection, projection.run_projects)
    _validate_stage_status(connection, projection.status)
    _validate_artifacts(connection, projection.artifacts)


def _validate_project_config(
    connection: sqlite3.Connection,
    run_projects: list[Any],
) -> None:
    if len(run_projects) != 1:
        raise WorkflowError("event ledger must contain exactly one run creation")
    metadata = connection.execute(
        "SELECT value FROM metadata WHERE key = 'project_config'"
    ).fetchone()
    try:
        persisted = json.loads(metadata["value"]) if metadata is not None else None
    except json.JSONDecodeError as error:
        raise WorkflowError("project configuration metadata is invalid JSON") from error
    if persisted != run_projects[0]:
        raise WorkflowError("project configuration differs from the creation event")


def _validate_stage_status(
    connection: sqlite3.Connection,
    projected: dict[str, StageStatus],
) -> None:
    try:
        persisted = {
            row["name"]: StageStatus(row["status"])
            for row in connection.execute("SELECT name, status FROM stages")
        }
    except ValueError as error:
        raise WorkflowError("persisted stage status is invalid") from error
    if persisted != projected:
        raise WorkflowError("persisted stage states differ from the event ledger")


def _validate_artifacts(
    connection: sqlite3.Connection,
    projected: Counter[tuple[Any, ...]],
) -> None:
    persisted: Counter[tuple[Any, ...]] = Counter(
        (
            row["stage_name"],
            row["direction"],
            row["ordinal"],
            row["digest"],
            row["kind"],
            row["size"],
            row["cas_path"],
            row["source"],
        )
        for row in connection.execute(
            """
            SELECT s.stage_name, s.direction, s.ordinal, s.digest, s.kind,
                   c.size, c.cas_path, s.source
            FROM stage_artifacts s
            JOIN artifact_contents c ON c.digest = s.digest AND c.kind = s.kind
            """
        )
    )
    if persisted != projected:
        raise WorkflowError("persisted artifact occurrences differ from the event ledger")


def _artifact_event(payload: dict[str, Any], artifact: Any) -> tuple[Any, ...]:
    if not isinstance(artifact, dict):
        raise WorkflowError("artifact event has invalid payload")
    try:
        direction = ArtifactDirection(payload["direction"])
        values = (
            str(payload["stage"]),
            direction.value,
            artifact["ordinal"],
            artifact["digest"],
            artifact["kind"],
            artifact["size"],
            artifact["cas_path"],
            artifact["source"],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise WorkflowError("artifact event is missing occurrence metadata") from error
    if not isinstance(values[2], int) or values[2] < 0:
        raise WorkflowError("artifact event has invalid ordinal")
    return values
