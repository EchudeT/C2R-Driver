"""Durable, replay-safe repair observations; no semantic guesses from report prose."""
import json
import sqlite3

from .events import RunEvent


def record_continuation(project, stage, occurrence, fingerprint: str, *, detail: str,
                        receipt: str | None = None, observation: dict | None = None) -> dict:
    # Keep history across retries/restarts, but an accepted stage starts a new
    # repair episode. Replaying one submission never spends another retry.
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT event_type,payload FROM events "
            "WHERE json_extract(payload, '$.stage') = ? "
            "AND event_type IN ('run.continuation','stage.completed','stage.retried') "
            "ORDER BY sequence DESC", (stage.value,),
        ).fetchall()
    previous = None
    for kind, raw in rows:
        value = json.loads(raw)
        if kind == "stage.retried":
            if value.get("operator_reopen"):
                break  # An explicit resolution reason authorizes a new episode.
            continue
        if kind == "stage.completed":
            if value.get("outcome") in {"PASS", "NOT_APPLICABLE"}:
                break
            continue
        previous = value
        break
    submission = {"digest": occurrence.digest, "ordinal": occurrence.ordinal}
    if (previous and previous["submission"] == submission
            and previous["fingerprint"] == fingerprint):
        return previous
    count = (previous["consecutive"] + 1
             if previous and previous["fingerprint"] == fingerprint else 1)
    value = {"stage": stage.value, "submission": submission,
             "fingerprint": fingerprint, "consecutive": count,
             "detail": detail, "receipt": receipt, "observation": observation}
    project.record_event(RunEvent.CONTINUATION, value)
    return value
