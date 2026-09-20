"""Worker/controller contract: capabilities and report actions have one owner."""
from dataclasses import dataclass

from ..core.models import WorkflowError


@dataclass(frozen=True)
class TaskProtocol:
    operations: tuple[str, ...] = ()
    completion: str = "Complete the requested Markdown report."
    executable: bool = False


TASKS = {
    "revision_selection": TaskProtocol(completion="Return repository selection JSON."),
    "evidence_closure": TaskProtocol(completion="Return evidence selection JSON."),
    "environment_recovery": TaskProtocol(executable=True),
    "target_platform_study": TaskProtocol(),
    "source_closure": TaskProtocol(("SOURCE_ANALYSIS",),
        "Consume compiler facts, complete source coverage; end DPF_SELF_REVIEW: PASS."),
    "migration_contracts": TaskProtocol(),
    "driver_implementation": TaskProtocol(completion=
        "Finish implementation and affected checks; end DPF_SELF_REVIEW: PASS.", executable=True),
    "artifact_preparation": TaskProtocol(completion=
        "Prepare artifact and checker covering payloads and reachable entrypoints for every retained "
        "runtime scenario without artifact changes. If packaging changed source/configuration, "
        "self-check affected changes and end DPF_SELF_REVIEW: PASS; the controller refreshes "
        "the implementation snapshot without another implementation turn.", executable=True),
    "public_qemu_validation": TaskProtocol(("PUBLIC_QEMU",),
        "Inspect captured execution and self-check; end DPF_SELF_REVIEW: PASS.", True),
    "public_repair": TaskProtocol(completion=
        "End DPF_REVIEW: PASS or request the smallest evidenced prerequisite repair.", executable=True),
}

REPAIR_TARGETS = frozenset({"evidence_closure", "environment_recovery", "target_platform_study",
    "source_closure", "migration_contracts", "driver_implementation", "artifact_preparation",
    "public_qemu_validation"})


def describe(stage):
    task = TASKS.get(stage)
    if task is None:
        return None
    return {
        "input_authority": "Current frozen_inputs supersede older versions in conversation or reports. "
            "repair_state OPEN is actionable; RESOLVED is history, not a new repair request.",
        "operations": [f"DPF_RUN: {op}" for op in task.operations],
        "operation_delivery": "End the report with an operation request; resume this task after its receipt.",
        "completion": task.completion,
        "repair": "Within the current phase choose from context.repair_targets. End DPF_REPAIR_STAGE: <affected prerequisite> then DPF_REVIEW: REWORK; explain the causal defect. Completed earlier phases are sealed. If a frozen earlier premise must change, report the concrete blocker and impact; never silently reopen it.",
        "blocked": "Explain external prerequisite and alternatives; end DPF_STATUS: BLOCKED.",
    }


def operation(stage, text):
    markers = [line.strip()[8:].strip() for line in text.splitlines()
               if line.strip().startswith("DPF_RUN:")]
    if not markers:
        return None
    task = TASKS.get(stage)
    if (len(markers) != 1 or task is None or markers[0] not in task.operations
            or text.rstrip().splitlines()[-1].strip() != f"DPF_RUN: {markers[0]}"):
        raise WorkflowError(f"Invalid operation for {stage}; allowed: {task.operations if task else ()}")
    return markers[0]
