"""Behavior progress inside implementation; completion is never acceptance."""

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from ..core.events import RunEvent
from ..core.models import WorkflowError
from .contracts import MigrationStage as S
from .implementation import worktree_files

STAGES = {S.TARGET_FRAMEWORK_ENABLEMENT, S.DRIVER_IMPLEMENTATION}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _validate_rows(rows):
    """Validate the plan input independently of dependency compilation."""
    if not isinstance(rows, list) or not rows or len(rows) > 64:
        raise WorkflowError("Supply 1..64 meaningful behaviors, not a per-function inventory")
    by_id = {}
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
            "id",
            "outcome",
            "contracts",
            "depends_on",
            "constraints",
        }:
            raise WorkflowError(
                "Behavior requires id, outcome, contracts, depends_on and constraints"
            )
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ("id", "outcome")):
            raise WorkflowError("Behavior id and observable outcome must be nonempty")
        if row["id"] in by_id:
            raise WorkflowError("Duplicate behavior ID")
        for key in ("contracts", "depends_on", "constraints"):
            if (
                not isinstance(row[key], list)
                or any(not isinstance(v, str) or not v for v in row[key])
                or len(row[key]) != len(set(row[key]))
            ):
                raise WorkflowError(f"Behavior {key} must be a unique string list")
        if not row["contracts"]:
            raise WorkflowError(
                "Behavior must link frozen contract IDs; linkage is not coverage proof"
            )
        by_id[row["id"]] = row
    for row in rows:
        if not set(row["depends_on"]) <= by_id.keys():
            raise WorkflowError("Unknown behavior prerequisite")

    return by_id


def compile_plan(rows, basis):
    """Explicit behavioral dependencies; cycles form an inseparable unit."""
    by_id = _validate_rows(rows)

    def reachable(name, visited=None):
        visited = set() if visited is None else visited
        if name not in visited:
            visited.add(name)
            for dep in by_id[name]["depends_on"]:
                reachable(dep, visited)
        return visited

    reaches = {name: reachable(name) for name in by_id}
    groups, assigned = [], set()
    for name in by_id:
        if name not in assigned:
            members = [
                other for other in by_id if other in reaches[name] and name in reaches[other]
            ]
            assigned.update(members)
            groups.append(members)
    owners = {member: group[0] for group in groups for member in group}
    units = {
        group[0]: {
            "id": group[0],
            "behaviors": [by_id[n] for n in group],
            "depends_on": list(
                dict.fromkeys(
                    owners[d]
                    for n in group
                    for d in by_id[n]["depends_on"]
                    if owners[d] != group[0]
                )
            ),
        }
        for group in groups
    }

    def key(name):
        unit = units[name]
        if "key" not in unit:
            unit["key"] = digest(
                {
                    "basis": {n: basis[n] for n in (b["id"] for b in unit["behaviors"])},
                    "behaviors": unit["behaviors"],
                    "prerequisites": [key(d) for d in unit["depends_on"]],
                }
            )
        return unit["key"]

    for name in units:
        key(name)
    return list(units.values())


def select(units, completed, active=None):
    done = set()
    while True:
        available = {
            u["id"] for u in units if u["key"] in completed and set(u["depends_on"]) <= done
        }
        if available <= done:
            break
        done |= available
    ready = [u for u in units if u["id"] not in done and set(u["depends_on"]) <= done]
    return next((u for u in ready if u["key"] == active), next(iter(ready), None))


def enabled(project, stage):
    return project.config.behavior_scheduling and stage in STAGES


def _load(project):
    with sqlite3.connect(f"{project.database_path.as_uri()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT payload FROM events WHERE event_type=? ORDER BY sequence DESC LIMIT 1",
            (RunEvent.BEHAVIOR_PROGRESS.value,),
        ).fetchone()
    return (
        json.loads(row[0])
        if row
        else {"rows": [], "completed": [], "active": None, "jobs": {}, "turns": 0}
    )


def _save(project, state):
    # Controller commands only; model progress is recorded separately from stage verdicts.
    project.record_event(RunEvent.BEHAVIOR_PROGRESS, state)
    path = project.control / "behavior-progress.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2) + "\n")
    temporary.replace(path)


def _units(project, state):
    from . import route, route_model

    value, text, _ = route.current(project)
    # Analysis owns the single plan; revisions update references, not a second JSON plan.
    state["rows"] = route_model.rows(value["index"], text)
    bases = route_model.bases(value["index"], text)
    return compile_plan(state["rows"], bases)


def last_selected(project, stage):
    """Whether completing the selected unit exhausts the current plan."""
    if not enabled(project, stage):
        return False
    state = _load(project)
    units = _units(project, state)
    current = select(units, state["completed"], state["active"])
    return bool(current) and select(units, [*state["completed"], current["key"]], None) is None


def packet(project, stage):
    if not enabled(project, stage):
        return None
    state = _load(project)
    current = select(_units(project, state), state["completed"], state["active"])
    if current:
        objective = (
            f"Complete only the selected work package {current['id']}: linked obligations, "
            "necessary "
            "framework adaptation, driver integration, error/cleanup paths and local checks. "
            "Stop this round at behavior_done or behavior_continue."
        )
    else:
        objective = (
            "All planned behaviors are implemented. Assemble the existing delivery and run "
            "the required final checks, reusing identity-matched receipts. Fix concrete remaining "
            "delivery failures; do not start another platform survey or optional improvement pass."
        )
    from . import route, route_model

    value, text, _ = route.current(project)
    return {
        "analysis_document": route.document(project),
        "route_context": route_model.packet(
            value["index"], text, {b["id"] for b in current["behaviors"]} if current else set()
        ),
        "objective": objective,
        "current": current,
        "final_work_package": last_selected(project, stage),
        "local_adaptation": state.get("local_adaptations", [])[-1:],
        "plan_exists": bool(state["rows"]),
        "instruction": "One complete work package per round, with required lifecycle and cleanup. "
        "A package may be the entire small driver and contain multiple contracts and tests. "
        "Code, read on demand, check and repair within this round; internal route steps, "
        "functions and test scenarios do not require separate handoffs. "
        "The plan is already seeded from the accepted joint analysis; do not "
        "restart platform/source analysis. Their full original references remain authoritative. "
        "Use driver_checks.analysis action=revise with the updated report/index for a "
        "concrete route correction. Each item is an "
        "observable "
        "driver behavior, not a helper-function inventory or a standalone framework task. "
        "Keep required target API changes, integration and test stimuli within the behavior that "
        "needs them. Read/probe only a concrete prerequisite whose answer changes that behavior "
        "or its required oracle; stop investigating once it is resolved. Use the verified platform "
        "build/boot tools. Do not perform a blanket capability survey. "
        "Submit operation behavior_done to finish only the selected behavior, or behavior_continue "
        "to retain it. For the last package, run the required suite and include your short "
        "source self-check and remaining validation limits in the done note. The controller "
        "captures delivery through the existing checks directly; "
        "no separate report-writing turn. Implemented is not benchmark acceptance.",
    }


def begin(project, stage, job):
    if not enabled(project, stage):
        return
    state = _load(project)
    current = select(_units(project, state), state["completed"], state["active"])
    state["jobs"][job] = {"selected": current["key"] if current else None, "consumed": False}
    _save(project, state)


def checkpoint(project):
    from ..acquisition.repository import load_repository_acquisition

    target = load_repository_acquisition(project).target_worktree
    worktree = project.root / target.path
    paths = [row["path"] for row in worktree_files(worktree, target.base_commit)]
    if not paths:
        return None
    from ..checkpoints import git

    with tempfile.TemporaryDirectory(prefix="dpf-behavior-index-") as directory:
        env = {"GIT_INDEX_FILE": str(Path(directory) / "index")}
        git(worktree, "read-tree", target.base_commit, env=env)
        git(worktree, "--literal-pathspecs", "add", "-A", "--", *paths, env=env)
        tree = git(worktree, "write-tree", env=env).decode().strip()
        commit = (
            git(
                worktree,
                "commit-tree",
                tree,
                "-p",
                target.base_commit,
                data=b"DPF behavior progress; not acceptance\n",
                env=env,
            )
            .decode()
            .strip()
        )
        git(worktree, "update-ref", f"refs/dpf-behaviors/{commit}", commit)
        return commit


def finish(project, stage, submission):
    """Return a continuation reason; never complete a stage on a progress operation."""
    if not enabled(project, stage):
        return None
    state = _load(project)
    job = state["jobs"].get(submission["job_id"])
    if job is None:
        raise WorkflowError("Submission has no selected behavior")
    current = select(_units(project, state), state["completed"], state["active"])
    if submission["decision"] == "pass":
        return (
            "Complete the current behavior via behavior_done; pass cannot skip pending behaviors."
            if current
            else None
        )
    operation = submission.get("operation")
    if operation not in {"behavior_done", "behavior_continue"}:
        return None
    if not job["consumed"]:
        if state["turns"] >= 64:
            from .repair_routing import WorkerBlocked

            raise WorkerBlocked(
                "Behavior scheduling reached 64 progress handoffs; preserve the current checkpoint"
            )
        state["checkpoint"] = checkpoint(project)
        if current and operation == "behavior_done" and job["selected"] == current["key"]:
            state["completed"] = list(dict.fromkeys([*state["completed"], current["key"]]))
            state["active"] = None
        else:
            state["active"] = current["key"] if current else None
        job["consumed"] = True
        state["turns"] += 1
        _save(project, state)
    next_packet = packet(project, stage)
    if submission.get("final_package_handoff") and next_packet["current"] is None:
        return None  # Continue through the ordinary implementation/acceptance validators.
    return "Behavior progress recorded, not stage PASS. " + json.dumps(next_packet)
