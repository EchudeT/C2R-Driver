from __future__ import annotations

import json
import sqlite3
from typing import Any

from .artifact_persistence import load_current_occurrences
from .contracts import StageKey
from .events import StageEvent
from .ledger import append_event
from .models import (
    ActorRole,
    ArtifactDirection,
    StageOwner,
    StageStatus,
    StageView,
    WorkflowError,
)
from .workflow import WorkflowDefinition


class _StagePersistence:
    """Validate and project persisted stage records for one workflow definition."""

    def __init__(self, workflow: WorkflowDefinition) -> None:
        self.workflow = workflow

    def stage(self, connection: sqlite3.Connection, name: StageKey) -> StageView:
        row = self._row(connection, name)
        view = self._validated_view(row, name)
        self._validate_pass_outputs(connection, view)
        return view

    def stages(self, connection: sqlite3.Connection) -> list[StageView]:
        rows = connection.execute("SELECT * FROM stages ORDER BY position").fetchall()
        if len(rows) != len(self.workflow.stages):
            raise WorkflowError("persisted stage catalog differs from workflow")
        views = [self._validated_view(row, self.workflow.parse_stage(row["name"])) for row in rows]
        for view in views:
            self._validate_pass_outputs(connection, view)
        return views

    def transition_row(
        self,
        connection: sqlite3.Connection,
        name: StageKey,
        expected: StageStatus,
    ) -> sqlite3.Row:
        row = self._row(connection, name)
        try:
            status = StageStatus(row["status"])
        except ValueError as error:
            raise WorkflowError(f"stage {name.value} has invalid persisted status") from error
        if status is not expected:
            raise WorkflowError(f"stage {name.value} is {status.value}, not {expected.value}")
        return row

    def allowed_roles(self, row: sqlite3.Row, name: StageKey) -> set[ActorRole]:
        values = self._json_field(row, "allowed_roles", name)
        if not isinstance(values, list):
            raise WorkflowError(f"persisted allowed_roles is invalid for {name.value}")
        try:
            return {ActorRole(value) for value in values}
        except (TypeError, ValueError) as error:
            raise WorkflowError(f"persisted allowed_roles is invalid for {name.value}") from error

    def refresh_ready(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT name FROM stages WHERE status = ? ORDER BY position",
            (StageStatus.PENDING.value,),
        ).fetchall()
        for row in rows:
            stage_name = self.workflow.parse_stage(row["name"])
            if self._dependencies_satisfied(connection, stage_name):
                connection.execute(
                    "UPDATE stages SET status = ? WHERE name = ?",
                    (StageStatus.READY.value, stage_name.value),
                )
                append_event(connection, StageEvent.READY, {"stage": stage_name.value})

    def _dependencies_satisfied(self, connection: sqlite3.Connection, name: StageKey) -> bool:
        spec = self.workflow.spec(name)
        if not spec.dependencies:
            return True
        statuses: list[StageStatus] = []
        for dependency in spec.dependencies:
            row = connection.execute(
                "SELECT status FROM stages WHERE name = ?", (dependency.value,)
            ).fetchone()
            if row is None:
                raise WorkflowError(f"missing dependency stage: {dependency.value}")
            try:
                statuses.append(StageStatus(row["status"]))
            except ValueError as error:
                raise WorkflowError(
                    f"dependency {dependency.value} has invalid persisted status"
                ) from error
        if spec.accept_failed_dependencies:
            return all(status.terminal for status in statuses)
        return all(status.satisfies_dependency for status in statuses)

    def _validated_view(self, row: sqlite3.Row, name: StageKey) -> StageView:
        spec = self.workflow.spec(name)
        expected = {
            "position": self.workflow.stages.index(spec),
            "description": spec.description,
            "owner": spec.owner.value,
            "dependencies": [item.value for item in spec.dependencies],
            "required_outputs": [
                {"kind": item.value, "cardinality": item.cardinality.value}
                for item in spec.required_outputs
            ],
            "auxiliary_outputs": [item.value for item in spec.auxiliary_outputs],
            "allowed_roles": [role.value for role in spec.allowed_roles],
            "accept_failed_dependencies": int(spec.accept_failed_dependencies),
        }
        json_fields = {
            "dependencies",
            "required_outputs",
            "auxiliary_outputs",
            "allowed_roles",
        }
        actual = {
            field: self._json_field(row, field, name) if field in json_fields else row[field]
            for field in expected
        }
        mismatched = sorted(field for field in expected if actual[field] != expected[field])
        if mismatched:
            raise WorkflowError(
                f"persisted stage specification differs for {name.value}: " + ", ".join(mismatched)
            )
        try:
            owner = StageOwner(row["owner"])
            status = StageStatus(row["status"])
        except ValueError as error:
            raise WorkflowError(f"persisted stage state is invalid for {name.value}") from error
        return StageView(
            name=spec.name,
            position=row["position"],
            owner=owner,
            status=status,
            dependencies=spec.dependencies,
            required_outputs=spec.required_outputs,
            auxiliary_outputs=spec.auxiliary_outputs,
            description=row["description"],
        )

    def _validate_pass_outputs(self, connection: sqlite3.Connection, view: StageView) -> None:
        if view.status is not StageStatus.PASS:
            return
        refs = load_current_occurrences(
            connection,
            stage_name=view.name.value,
            direction=ArtifactDirection.OUTPUT,
        )
        self.workflow.output_contract(view.name).validate_persisted(ref.kind for ref in refs)

    @staticmethod
    def _row(connection: sqlite3.Connection, name: StageKey) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM stages WHERE name = ?", (name.value,)).fetchone()
        if row is None:
            raise WorkflowError(f"unknown stage: {name.value}")
        return row

    @staticmethod
    def _json_field(row: sqlite3.Row, field: str, name: StageKey) -> Any:
        try:
            return json.loads(row[field])
        except (TypeError, json.JSONDecodeError) as error:
            raise WorkflowError(
                f"persisted stage field {field} is invalid JSON for {name.value}"
            ) from error
