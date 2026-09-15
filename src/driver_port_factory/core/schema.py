from __future__ import annotations

import sqlite3

from .ledger import canonical_json
from .models import ProjectConfig, StageStatus, WorkflowError
from .workflow import WorkflowDefinition

SCHEMA_VERSION = "1"


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE stages (
            name TEXT PRIMARY KEY,
            position INTEGER NOT NULL UNIQUE,
            description TEXT NOT NULL,
            owner TEXT NOT NULL,
            dependencies TEXT NOT NULL,
            required_outputs TEXT NOT NULL,
            auxiliary_outputs TEXT NOT NULL,
            allowed_roles TEXT NOT NULL,
            accept_failed_dependencies INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            message TEXT
        );
        CREATE TABLE artifact_contents (
            digest TEXT NOT NULL,
            kind TEXT NOT NULL,
            size INTEGER NOT NULL CHECK(size >= 0),
            cas_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (digest, kind)
        );
        CREATE TABLE stage_artifacts (
            occurrence_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage_name TEXT NOT NULL REFERENCES stages(name),
            direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
            ordinal INTEGER NOT NULL CHECK(ordinal >= 0),
            digest TEXT NOT NULL,
            kind TEXT NOT NULL,
            source TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(stage_name, direction, ordinal),
            FOREIGN KEY(digest, kind) REFERENCES artifact_contents(digest, kind)
        );
        CREATE INDEX stage_artifact_content
            ON stage_artifacts(digest, kind);
        CREATE TABLE events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            previous_hash TEXT NOT NULL,
            event_hash TEXT NOT NULL UNIQUE
        );
        """
    )


def initialize_run(
    connection: sqlite3.Connection,
    config: ProjectConfig,
    workflow: WorkflowDefinition,
) -> None:
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("storage_schema", SCHEMA_VERSION),
            ("project_config", canonical_json(config.to_dict())),
        ),
    )
    for position, stage in enumerate(workflow.stages):
        initial = StageStatus.READY if not stage.dependencies else StageStatus.PENDING
        connection.execute(
            """
            INSERT INTO stages(
                name, position, description, owner, dependencies,
                required_outputs, auxiliary_outputs, allowed_roles,
                accept_failed_dependencies, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                stage.name.value,
                position,
                stage.description,
                stage.owner.value,
                canonical_json([item.value for item in stage.dependencies]),
                canonical_json(
                    [
                        {"kind": output.value, "cardinality": output.cardinality.value}
                        for output in stage.required_outputs
                    ]
                ),
                canonical_json([output.value for output in stage.auxiliary_outputs]),
                canonical_json([role.value for role in stage.allowed_roles]),
                int(stage.accept_failed_dependencies),
                initial.value,
            ),
        )


def validate_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT value FROM metadata WHERE key = 'storage_schema'").fetchone()
    if row is None or row["value"] != SCHEMA_VERSION:
        raise WorkflowError("run database storage schema is missing or unsupported")
    tables = {
        row["name"]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    required = {"metadata", "stages", "artifact_contents", "stage_artifacts", "events"}
    if not required <= tables:
        raise WorkflowError("run database storage schema is incomplete")
