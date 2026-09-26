"""Bounded ledger observations for recovery; no diagnosis from report prose."""

import json
import sqlite3


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
        if len(observations) == 2:
            break
    changes = []
    if len(observations) == 2:
        current, previous = (item["observation"] for item in observations)
        if isinstance(current, dict) and isinstance(previous, dict):
            for field in sorted(current.keys() & previous.keys()):
                if current[field] != previous[field]:
                    changes.append({"field": field, "previous": previous[field],
                                    "current": current[field]})
    return {
        "recent": observations, "changes": changes,
        "instruction": "These are historical observations at the cited ledger events, not "
                       "current execution guarantees. Recheck old diagnoses against these facts "
                       "and current inputs. Changed inputs may explain differences; missing "
                       "fields mean unknown, not false. An observed process or log does not "
                       "establish device behavior. Do not repeat a disproved diagnosis or "
                       "infer that the whole blocker is resolved from one changed field.",
    }
