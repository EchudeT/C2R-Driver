from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from .contracts import EventKey
from .models import utc_now


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def append_event(
    connection: sqlite3.Connection,
    event_type: EventKey,
    payload: dict[str, Any],
) -> str:
    created_at = utc_now()
    payload_json = canonical_json(payload)
    previous = connection.execute(
        "SELECT event_hash FROM events ORDER BY sequence DESC LIMIT 1"
    ).fetchone()
    previous_hash = previous["event_hash"] if previous else "0" * 64
    material = canonical_json(
        {
            "created_at": created_at,
            "event_type": event_type.value,
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
        (created_at, event_type.value, payload_json, previous_hash, event_hash),
    )
    return event_hash


def verify_event_chain(connection: sqlite3.Connection) -> bool:
    previous_hash = "0" * 64
    rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
    for row in rows:
        if row["previous_hash"] != previous_hash:
            return False
        try:
            payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            return False
        material = canonical_json(
            {
                "created_at": row["created_at"],
                "event_type": row["event_type"],
                "payload": payload,
                "previous_hash": row["previous_hash"],
            }
        ).encode("utf-8")
        if hashlib.sha256(material).hexdigest() != row["event_hash"]:
            return False
        previous_hash = row["event_hash"]
    return True
