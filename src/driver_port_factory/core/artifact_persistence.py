from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from .events import StageEvent
from .models import ArtifactContent, ArtifactDirection, ArtifactRef, WorkflowError, utc_now


def persist_content(connection: sqlite3.Connection, content: ArtifactContent) -> None:
    existing = connection.execute(
        "SELECT size, cas_path FROM artifact_contents WHERE digest = ? AND kind = ?",
        (content.digest, content.kind),
    ).fetchone()
    if existing is None:
        connection.execute(
            """
            INSERT INTO artifact_contents(digest, kind, size, cas_path, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (content.digest, content.kind, content.size, content.cas_path, utc_now()),
        )
        return
    if existing["size"] != content.size or existing["cas_path"] != content.cas_path:
        raise WorkflowError(
            f"artifact content metadata conflicts for {content.kind}:{content.digest}"
        )


def next_ordinal(
    connection: sqlite3.Connection,
    stage_name: str,
    direction: ArtifactDirection,
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(ordinal), -1) AS last
        FROM stage_artifacts WHERE stage_name = ? AND direction = ?
        """,
        (stage_name, direction.value),
    ).fetchone()
    return int(row["last"]) + 1


def persist_occurrences(
    connection: sqlite3.Connection,
    stage_name: str,
    direction: ArtifactDirection,
    refs: Iterable[ArtifactRef],
) -> tuple[ArtifactRef, ...]:
    start = next_ordinal(connection, stage_name, direction)
    persisted: list[ArtifactRef] = []
    for offset, ref in enumerate(refs):
        if not ref.source:
            raise WorkflowError("artifact occurrence requires non-empty provenance")
        if ref.ordinal is not None:
            raise WorkflowError("new artifact occurrence must not predeclare an ordinal")
        persist_content(connection, ref.content)
        ordinal = start + offset
        try:
            cursor = connection.execute(
                """
                INSERT INTO stage_artifacts(
                    stage_name, direction, ordinal, digest, kind, source, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stage_name,
                    direction.value,
                    ordinal,
                    ref.digest,
                    ref.kind,
                    ref.source,
                    utc_now(),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise WorkflowError(
                f"artifact occurrence conflicts at {stage_name}:{direction.value}:{ordinal}"
            ) from error
        if cursor.rowcount != 1:
            raise WorkflowError("artifact occurrence was not persisted exactly once")
        persisted.append(ArtifactRef(ref.content, ref.source, ordinal))
    return tuple(persisted)


def load_occurrences(
    connection: sqlite3.Connection,
    *,
    stage_name: str | None = None,
    direction: ArtifactDirection | None = None,
) -> list[ArtifactRef]:
    query = """
        SELECT c.digest, c.kind, c.size, c.cas_path, s.source, s.ordinal
        FROM stage_artifacts s
        JOIN artifact_contents c ON c.digest = s.digest AND c.kind = s.kind
    """
    parameters: list[str] = []
    conditions: list[str] = []
    if stage_name is not None:
        conditions.append("s.stage_name = ?")
        parameters.append(stage_name)
    if direction is not None:
        conditions.append("s.direction = ?")
        parameters.append(direction.value)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY s.stage_name, s.direction, s.ordinal"
    rows = connection.execute(query, parameters).fetchall()
    return [
        ArtifactRef(
            ArtifactContent(row["digest"], row["kind"], row["size"], row["cas_path"]),
            row["source"],
            row["ordinal"],
        )
        for row in rows
    ]


def load_current_occurrences(
    connection: sqlite3.Connection,
    *,
    stage_name: str,
    direction: ArtifactDirection | None = None,
) -> list[ArtifactRef]:
    """Load only occurrences produced by the stage's current audited attempt."""

    boundaries = {value: 0 for value in ArtifactDirection}
    rows = connection.execute(
        "SELECT payload FROM events WHERE event_type = ? ORDER BY sequence",
        (StageEvent.RETRIED.value,),
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError) as error:
            raise WorkflowError("stage retry event has invalid payload JSON") from error
        if not isinstance(payload, dict) or payload.get("stage") != stage_name:
            continue
        values = payload.get("artifact_boundaries")
        if not isinstance(values, dict):
            raise WorkflowError("stage retry event has invalid artifact boundaries")
        try:
            boundaries = {
                value: int(values[value.value])
                for value in ArtifactDirection
            }
        except (KeyError, TypeError, ValueError) as error:
            raise WorkflowError("stage retry event has invalid artifact boundaries") from error
        if any(value < 0 for value in boundaries.values()):
            raise WorkflowError("stage retry event has invalid artifact boundaries")

    directions = (direction,) if direction is not None else tuple(ArtifactDirection)
    return [
        ref
        for selected in directions
        for ref in load_occurrences(
            connection,
            stage_name=stage_name,
            direction=selected,
        )
        if ref.ordinal is not None and ref.ordinal >= boundaries[selected]
    ]
