"""Call-boundary cost guard. Estimates are not provider billing or a hard spend cap."""

import json
import math
import sqlite3

from ..core.events import RunEvent
from ..core.models import ControllerError


class ModelBudgetStopped(ControllerError):
    """Stop outside model-repair routing; budget exhaustion is not a driver defect."""


def read(project):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT payload FROM events WHERE event_type=? ORDER BY sequence DESC LIMIT 1",
            (RunEvent.MODEL_BUDGET.value,),
        ).fetchone()
    return json.loads(row[0])["max_usd"] if row else None


def configure(project, maximum):
    if maximum is None:
        return
    if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum <= 0:
        raise ControllerError("Model cost budget must be a finite positive USD amount")
    prior = read(project)
    if prior is not None:
        if maximum != prior:
            raise ControllerError("Model cost budget differs from the persisted run")
        return
    project.record_event(RunEvent.MODEL_BUDGET, {"max_usd": maximum, "basis": "recorded estimates"})


def guard(project):
    maximum = read(project)
    if maximum is None:
        return
    from .statistics import project_statistics

    totals = project_statistics(project)["totals"]
    if totals["unknown_usage_calls"] or totals["unpriced_calls"]:
        raise ModelBudgetStopped(
            "Model budget cannot price all previous calls; unknown usage is not zero. "
            "No new model call started. Preserve state and reconcile the missing accounting."
        )
    spent = totals["usd"]
    if spent >= maximum:
        raise ModelBudgetStopped(
            f"Model budget reached: recorded estimate ${spent:.4f}, budget ${maximum:.2f}. "
            "No new model call started; current work and failed-call costs are preserved. "
            "This is a call-boundary guard, not an exact provider billing cap."
        )
