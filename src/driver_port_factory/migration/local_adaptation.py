"""Keep implementation-choice corrections inside the selected behavior.

Frozen obligations are not editable here. A correction is worker evidence, never
an imported platform verdict or a replacement for final execution.
"""

from ..core.models import EvaluationMode
from . import behavior


def eligible(config, stage, target):
    return (
        config.behavior_scheduling
        and config.unified_implementation
        and config.evaluation_mode is EvaluationMode.DEVELOPER_EVIDENCE
        and stage.value == "driver_implementation"
        and target.value == "target_platform_study"
    )


def retain(project, stage, target, submission, freeze_report):
    if not eligible(project.config, stage, target):
        return None
    state = behavior._load(project)
    current = behavior.select(behavior._units(project, state), state["completed"], state["active"])
    if current is None:
        return None
    job = state["jobs"][submission["job_id"]]
    history = state.get("local_adaptations", [])
    recorded = any(row["job_id"] == submission["job_id"] for row in history)
    if not job["consumed"] and not recorded:
        from ..core.recovery_state import repair_inputs
        from .repair_routing import WorkerBlocked

        identity = behavior.digest(
            {"behavior": current["key"], "inputs": repair_inputs(project, stage)}
        )
        if sum(row["identity"] == identity for row in history) >= 2:
            raise WorkerBlocked(
                "Repeated local platform-study request without changed implementation inputs; "
                "preserved current behavior and source. Diagnose the concrete blocker."
            )
        report = freeze_report(project, stage, submission)
        state["local_adaptations"] = [
            *history,
            {
                "identity": identity,
                "behavior": current["id"],
                "report": report,
                "job_id": submission["job_id"],
            },
        ]
        behavior._save(project, state)
    # Same operation as a normal unfinished function: no stage invalidation, no advancement.
    behavior.finish(
        project, stage, {**submission, "decision": "operation", "operation": "behavior_continue"}
    )
    return (
        "The target-platform assumption correction stays in the current behavior and session. "
        "Read behavior_progress.local_adaptation for the retained finding. Check the actual "
        "route/firmware and relevant definition before introducing framework changes; implement "
        "the smallest necessary adaptation, then check its affected behavior. Preserve the "
        "contract IDs, source obligations, oracles and environment route. Update the existing "
        "delivery report with the corrected premise and evidence. If those frozen obligations "
        "cannot be met, report blocked or explicitly request the actual affected prerequisite; "
        "do not claim that the old platform conclusion has been revalidated."
    )
