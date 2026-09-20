"""Developer phase boundaries shared by scheduling, persistence and prompts."""
from .models import WorkflowError


GROUPS = {
    "scope_and_baselines": (
        "project_init", "request_intake", "driver_candidate_resolution", "scope_confirmation",
        "migration_envelope_freeze", "revision_selection", "repository_acquisition"),
    "evidence_and_design": (
        "evidence_closure", "environment_recovery", "knowledge_base", "target_platform_study",
        "migration_handoff", "source_closure", "migration_contracts"),
    "delivery": (
        "driver_implementation", "artifact_preparation", "public_qemu_validation", "public_repair"),
    "completion": ("completion_audit",),
}
PHASES = {stage: phase for phase, stages in GROUPS.items() for stage in stages}


def phase_rows(connection):
    # Historical entry seals earlier phases even if a later checkpoint is reset.
    return connection.execute("""SELECT s.name,s.status,EXISTS(
        SELECT 1 FROM events e WHERE e.event_type='stage.started'
        AND json_extract(e.payload,'$.stage')=s.name) AS entered
        FROM stages s""").fetchall()


def phase(stage):
    return PHASES.get(stage.value, stage.value)


class PhaseBoundaryError(WorkflowError):
    """A sealed prerequisite cannot be reopened by an automatic repair request."""


def require_local_repair(target, trigger, rows):
    target_phase = phase(target)
    if target_phase != phase(trigger):
        raise PhaseBoundaryError(
            f"Cross-phase repair requires an explicit phase-reopen decision: "
            f"{phase(trigger)} -> {target_phase} ({trigger.value} -> {target.value}). "
            "No earlier stage or evidence has been invalidated.")
    ordered = list(GROUPS)
    if target_phase not in ordered:
        return
    rank = ordered.index(target_phase)
    for row in rows:
        later = PHASES.get(row["name"])
        if later in ordered and ordered.index(later) > rank and (
                row["entered"] or row["status"] not in {"PENDING", "READY"}):
            raise PhaseBoundaryError(
                f"Phase {target_phase} is sealed: later phase {later} has started. "
                "An explicit phase-reopen decision is required; evidence is unchanged.")
