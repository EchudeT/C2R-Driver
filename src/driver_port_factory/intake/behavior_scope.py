"""Operator-owned functional boundary, distinct from selecting a device identity."""

import json
from pathlib import Path

from ..core.models import WorkflowError


def validate(value):
    fields = {"mode", "integration", "required", "excluded"}
    if not isinstance(value, dict) or set(value) != fields:
        raise WorkflowError("Behavior scope needs exactly mode/integration/required/excluded")
    if value["mode"] not in ("source-driver", "explicit-subset"):
        raise WorkflowError("Behavior scope mode must be source-driver or explicit-subset")
    if value["integration"] not in ("target-kernel", "callback-harness"):
        raise WorkflowError("Behavior scope integration must be target-kernel or callback-harness")
    for name in ("required", "excluded"):
        rows = value[name]
        if not isinstance(rows, list) or any(not isinstance(x, str) or not x.strip() for x in rows):
            raise WorkflowError(f"Behavior scope {name} must be a list of nonempty descriptions")
        if len(set(rows)) != len(rows):
            raise WorkflowError(f"Behavior scope {name} has duplicate descriptions")
    if set(value["required"]) & set(value["excluded"]):
        raise WorkflowError("Behavior scope requires and excludes the same description")
    if value["mode"] == "explicit-subset" and not value["required"]:
        raise WorkflowError("An explicit behavior subset must name its required behaviors")
    return value


def load(path):
    return validate(json.loads(Path(path).read_text()))


def effective(config):
    supplied = config.behavior_scope
    return (
        json.loads(json.dumps(validate(supplied)))
        if supplied is not None
        else {
            "mode": "source-driver",
            "integration": "target-kernel",
            "required": [],
            "excluded": [],
        }
    )


RULE = (
    "This is a functional boundary, not just a device ID. source-driver includes the selected "
    "source driver's externally observable behavior, its called shared driver core, controls, "
    "lifecycle and error paths, except explicit exclusions. required adds explicit obligations; "
    "an empty required list in source-driver mode never means no obligations. explicit-subset "
    "limits functional delivery to required behaviors and their necessary dependencies. "
    "target-kernel requires actual target caller/insertion paths, not callback-only simulation; "
    "callback-harness permits only the declared modeled caller boundary, still requiring the "
    "configured device execution. Missing target mechanisms do not authorize silently dropping "
    "source obligations or implementing unrelated subsystems. Identify the affected obligation "
    "and report a bounded prerequisite/scope decision. A cost limit is not a scope exception. "
    "Keep behavior IDs, adaptation/compatibility differences and stimulus/assertion boundaries "
    "once in the existing analysis. Do not copy Linux-internal mechanisms without a required "
    "observable effect. Semantic compliance is determined by evidence and tests, not this schema."
)
