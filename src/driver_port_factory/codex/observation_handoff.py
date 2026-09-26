"""Bounded ledger observations for recovery; no diagnosis from report prose."""

import json
import sqlite3
from itertools import pairwise


def observation_handoff(project, stage) -> dict:
    # Read only the active repair episode. A passed/reopened stage invalidates
    # temporal comparisons to its earlier attempts, even if inputs look similar.
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT sequence,event_type,payload FROM events "
            "WHERE json_extract(payload, '$.stage')=? "
            "AND event_type IN ('run.continuation','stage.completed','stage.retried') "
            "ORDER BY sequence DESC LIMIT 32", (stage.value,),
        ).fetchall()
    observations = []
    for sequence, kind, raw in rows:
        value = json.loads(raw)
        if ((kind == "stage.completed" and value.get("outcome") in {"PASS", "NOT_APPLICABLE"})
                or (kind == "stage.retried" and value.get("operator_reopen"))):
            break
        if kind == "run.continuation":
            observations.append({"event_sequence": sequence, "receipt": value.get("receipt"),
                                 "fingerprint": value["fingerprint"],
                                 "observation": value.get("observation")})
    recent = observations[:2]
    changes = observation_changes(*recent) if len(recent) == 2 else []
    return {
        "recent": recent, "changes": changes,
        "last_transition": last_transition(observations),
        "instruction": "These are historical observations at the cited ledger events, not "
                       "current execution guarantees. Recheck old diagnoses against these facts "
                       "and current inputs. Changed inputs may explain differences; missing "
                       "fields mean unknown, not false. An observed process or log does not "
                       "establish device behavior. Do not repeat a disproved diagnosis or "
                       "infer that the whole blocker is resolved from one changed field.",
    }


def observation_changes(current, previous):
    current, previous = current["observation"], previous["observation"]
    if not isinstance(current, dict) or not isinstance(previous, dict):
        return []
    return [{"field": field, "previous": previous[field], "current": current[field]}
            for field in sorted(current.keys() & previous.keys())
            if current[field] != previous[field]]


def last_transition(observations):
    """Retain a transition across repeated identical snapshots, not across unknowns."""
    for current, previous in pairwise(observations):
        a, b = current["observation"], previous["observation"]
        if not isinstance(a, dict) or not isinstance(b, dict) or not a or not b:
            break
        changes = observation_changes(current, previous)
        if changes:
            return {"previous": previous, "current": current, "changes": changes}
        if a != b:
            break  # Missing/new fields do not establish a stable intervening observation.
    return None
