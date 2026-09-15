from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

from .models import (
    ActorRole,
    ArtifactRef,
    ProjectConfig,
    StageOwner,
    StageSpec,
    StageStatus,
    StageView,
    WorkflowError,
    utc_now,
)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RunStore:
    """SQLite-backed workflow state with an append-only hash-chained event ledger."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        if not self.path.exists():
            raise WorkflowError(f"run database does not exist: {self.path}")

    @classmethod
    def create(
        cls, path: Path, config: ProjectConfig, stages: Iterable[StageSpec]
    ) -> "RunStore":
        path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise WorkflowError(f"refusing to overwrite existing run database: {path}")
        connection = sqlite3.connect(path)
        try:
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
                    allowed_roles TEXT NOT NULL,
                    accept_failed_dependencies INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    message TEXT
                );
                CREATE TABLE artifacts (
                    digest TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    cas_path TEXT NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (digest, kind)
                );
                CREATE TABLE stage_artifacts (
                    stage_name TEXT NOT NULL REFERENCES stages(name),
                    digest TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    direction TEXT NOT NULL CHECK(direction IN ('input', 'output')),
                    PRIMARY KEY(stage_name, digest, kind, direction),
                    FOREIGN KEY(digest, kind) REFERENCES artifacts(digest, kind)
                );
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
            connection.execute(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                ("project_config", canonical_json(config.to_dict())),
            )
            for position, stage in enumerate(stages):
                initial = StageStatus.READY if not stage.dependencies else StageStatus.PENDING
                connection.execute(
                    """
                    INSERT INTO stages(
                        name, position, description, owner, dependencies,
                        required_outputs, allowed_roles, accept_failed_dependencies, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        stage.name,
                        position,
                        stage.description,
                        stage.owner.value,
                        canonical_json(stage.dependencies),
                        canonical_json(stage.required_outputs),
                        canonical_json([role.value for role in stage.allowed_roles]),
                        int(stage.accept_failed_dependencies),
                        initial.value,
                    ),
                )
            connection.commit()
        finally:
            connection.close()
        store = cls(path)
        store.append_event("run.created", {"project": config.to_dict()})
        return store

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
        return ProjectConfig.from_dict(json.loads(row["value"]))

    def append_event(self, event_type: str, payload: dict[str, Any]) -> str:
        created_at = utc_now()
        payload_json = canonical_json(payload)
        with self._connect() as connection:
            previous = connection.execute(
                "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()
            previous_hash = previous["event_hash"] if previous else "0" * 64
            material = canonical_json(
                {
                    "created_at": created_at,
                    "event_type": event_type,
                    "payload": json.loads(payload_json),
                    "previous_hash": previous_hash,
                }
            ).encode("utf-8")
            event_hash = hashlib.sha256(material).hexdigest()
            connection.execute(
                """
                INSERT INTO events(created_at, event_type, payload, previous_hash, event_hash)
                VALUES (?, ?, ?, ?, ?)
                """,
                (created_at, event_type, payload_json, previous_hash, event_hash),
            )
        return event_hash

    def stage(self, name: str) -> StageView:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stages WHERE name = ?", (name,)).fetchone()
        if row is None:
            raise WorkflowError(f"unknown stage: {name}")
        return StageView(
            name=row["name"],
            position=row["position"],
            owner=StageOwner(row["owner"]),
            status=StageStatus(row["status"]),
            dependencies=tuple(json.loads(row["dependencies"])),
            required_outputs=tuple(json.loads(row["required_outputs"])),
            description=row["description"],
        )

    def stages(self) -> list[StageView]:
        with self._connect() as connection:
            names = [
                row["name"]
                for row in connection.execute("SELECT name FROM stages ORDER BY position")
            ]
        return [self.stage(name) for name in names]

    def _dependencies_satisfied(self, connection: sqlite3.Connection, name: str) -> bool:
        row = connection.execute(
            "SELECT dependencies, accept_failed_dependencies FROM stages WHERE name = ?",
            (name,),
        ).fetchone()
        dependencies = json.loads(row["dependencies"])
        if not dependencies:
            return True
        statuses = [
            StageStatus(
                connection.execute(
                    "SELECT status FROM stages WHERE name = ?", (dependency,)
                ).fetchone()["status"]
            )
            for dependency in dependencies
        ]
        if row["accept_failed_dependencies"]:
            return all(status.terminal for status in statuses)
        return all(status.satisfies_dependency for status in statuses)

    def refresh_ready(self) -> None:
        changed: list[str] = []
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name FROM stages WHERE status = ? ORDER BY position",
                (StageStatus.PENDING.value,),
            ).fetchall()
            for row in rows:
                if self._dependencies_satisfied(connection, row["name"]):
                    connection.execute(
                        "UPDATE stages SET status = ? WHERE name = ?",
                        (StageStatus.READY.value, row["name"]),
                    )
                    changed.append(row["name"])
        for name in changed:
            self.append_event("stage.ready", {"stage": name})

    def start_stage(self, name: str, actor_role: ActorRole) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stages WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise WorkflowError(f"unknown stage: {name}")
            if StageStatus(row["status"]) is not StageStatus.READY:
                raise WorkflowError(f"stage {name} is {row['status']}, not READY")
            allowed = {ActorRole(value) for value in json.loads(row["allowed_roles"])}
            if actor_role not in allowed or actor_role is not self.config.actor_role:
                raise WorkflowError(
                    f"role {actor_role.value} may not execute {name} in a "
                    f"{self.config.actor_role.value} workspace"
                )
            connection.execute(
                "UPDATE stages SET status = ?, started_at = ? WHERE name = ?",
                (StageStatus.RUNNING.value, utc_now(), name),
            )
        self.append_event("stage.started", {"stage": name, "actor_role": actor_role.value})

    def wait_for_user(self, name: str, *, question: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT status FROM stages WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise WorkflowError(f"unknown stage: {name}")
            if StageStatus(row["status"]) is not StageStatus.RUNNING:
                raise WorkflowError(f"stage {name} is {row['status']}, not RUNNING")
            connection.execute(
                "UPDATE stages SET status = ?, message = ? WHERE name = ?",
                (StageStatus.WAITING_FOR_USER.value, question, name),
            )
        self.append_event("stage.waiting_for_user", {"stage": name, "question": question})

    def resume_after_user(self, name: str, actor_role: ActorRole, *, answer: str) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, allowed_roles FROM stages WHERE name = ?", (name,)
            ).fetchone()
            if row is None:
                raise WorkflowError(f"unknown stage: {name}")
            if StageStatus(row["status"]) is not StageStatus.WAITING_FOR_USER:
                raise WorkflowError(
                    f"stage {name} is {row['status']}, not WAITING_FOR_USER"
                )
            allowed = {ActorRole(value) for value in json.loads(row["allowed_roles"])}
            if actor_role not in allowed or actor_role is not self.config.actor_role:
                raise WorkflowError(f"role {actor_role.value} may not resume {name}")
            connection.execute(
                "UPDATE stages SET status = ?, message = NULL WHERE name = ?",
                (StageStatus.RUNNING.value, name),
            )
        self.append_event(
            "stage.resumed_after_user",
            {"stage": name, "actor_role": actor_role.value, "answer": answer},
        )

    def register_artifact(
        self, ref: ArtifactRef, *, stage: str, direction: str = "output"
    ) -> None:
        if direction not in {"input", "output"}:
            raise WorkflowError(f"invalid artifact direction: {direction}")
        stage_view = self.stage(stage)
        if direction == "output" and stage_view.status is not StageStatus.RUNNING:
            raise WorkflowError(
                f"cannot attach output to stage {stage} while it is {stage_view.status.value}"
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO artifacts(digest, kind, size, cas_path, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ref.digest, ref.kind, ref.size, ref.cas_path, ref.source, utc_now()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO stage_artifacts(stage_name, digest, kind, direction)
                VALUES (?, ?, ?, ?)
                """,
                (stage, ref.digest, ref.kind, direction),
            )
        self.append_event(
            "artifact.registered",
            {"stage": stage, "direction": direction, "artifact": ref.to_dict()},
        )

    def artifact_refs(self, *, stage: str | None = None, direction: str | None = None) -> list[ArtifactRef]:
        query = "SELECT a.* FROM artifacts a"
        parameters: list[Any] = []
        conditions: list[str] = []
        if stage is not None or direction is not None:
            query += " JOIN stage_artifacts s ON a.digest = s.digest AND a.kind = s.kind"
            if stage is not None:
                conditions.append("s.stage_name = ?")
                parameters.append(stage)
            if direction is not None:
                conditions.append("s.direction = ?")
                parameters.append(direction)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY a.kind, a.digest"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            ArtifactRef(
                digest=row["digest"],
                kind=row["kind"],
                size=row["size"],
                cas_path=row["cas_path"],
                source=row["source"],
            )
            for row in rows
        ]

    def complete_stage(
        self, name: str, outcome: StageStatus, *, message: str | None = None
    ) -> None:
        if not outcome.terminal:
            raise WorkflowError(f"completion outcome must be terminal, got {outcome.value}")
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM stages WHERE name = ?", (name,)).fetchone()
            if row is None:
                raise WorkflowError(f"unknown stage: {name}")
            if StageStatus(row["status"]) is not StageStatus.RUNNING:
                raise WorkflowError(f"stage {name} is {row['status']}, not RUNNING")
            if outcome is StageStatus.PASS:
                required = set(json.loads(row["required_outputs"]))
                actual = {
                    artifact["kind"]
                    for artifact in connection.execute(
                        "SELECT kind FROM stage_artifacts WHERE stage_name = ? AND direction = 'output'",
                        (name,),
                    )
                }
                missing = sorted(required - actual)
                if missing:
                    raise WorkflowError(
                        f"stage {name} cannot PASS; missing outputs: {', '.join(missing)}"
                    )
            connection.execute(
                """
                UPDATE stages SET status = ?, completed_at = ?, message = ? WHERE name = ?
                """,
                (outcome.value, utc_now(), message, name),
            )
        self.append_event(
            "stage.completed", {"stage": name, "outcome": outcome.value, "message": message}
        )
        self.refresh_ready()

    def verify_event_chain(self) -> bool:
        previous_hash = "0" * 64
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False
            material = canonical_json(
                {
                    "created_at": row["created_at"],
                    "event_type": row["event_type"],
                    "payload": json.loads(row["payload"]),
                    "previous_hash": row["previous_hash"],
                }
            ).encode("utf-8")
            if hashlib.sha256(material).hexdigest() != row["event_hash"]:
                return False
            previous_hash = row["event_hash"]
        return True
