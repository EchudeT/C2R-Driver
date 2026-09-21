"""Worker/controller contract: capabilities and report actions have one owner."""
from dataclasses import dataclass

from ..core.models import WorkflowError


@dataclass(frozen=True)
class TaskProtocol:
    operations: tuple[str, ...] = ()
    completion: str = "Complete the requested Markdown report."
    executable: bool = False


TASKS = {
    "repository_acquisition": TaskProtocol(completion="Return repository selection JSON."),
    "evidence_closure": TaskProtocol(completion="Return evidence selection JSON."),
    "environment_recovery": TaskProtocol(completion=
        "Write environment-smoke.sh and the environment report, then return REPORT_PATH only. "
        "Submission automatically runs the controller acceptance smoke; no DPF_RUN marker is needed.", executable=True),
    "target_platform_study": TaskProtocol(),
    "migration_contracts": TaskProtocol(completion="Complete source analysis, contracts and test plan in one Markdown report; end DPF_SELF_REVIEW: PASS."),
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
    "migration_contracts", "driver_implementation", "artifact_preparation",
    "public_qemu_validation"})


def describe(stage):
    task = TASKS.get(stage)
    if task is None:
        return None
    return {
        "input_authority": "Current frozen_inputs supersede older versions in conversation or reports. "
            "repair_state OPEN is actionable; RESOLVED is history, not a new repair request.",
        "operations": [f"DPF_RUN: {op}" for op in task.operations],
        "operation_delivery": ("End the report with exactly one listed DPF_RUN line, without a prefix or code fence. An operation request is not completion, blockage or prerequisite repair. Resume this same task after its receipt; missing receipts require the local operation, not upstream repair."
                               if task.operations else "No worker-requested controller operations in this task; submit the deliverable directly."),
        "completion": task.completion,
        "repair": "Within the current phase choose from instructions.repair_targets. End DPF_REPAIR_STAGE: <affected prerequisite> then DPF_REVIEW: REWORK; explain the causal defect. Completed earlier phases are sealed. If a frozen earlier premise must change, report the concrete blocker and impact; never silently reopen it.",
        "blocked": "Explain external prerequisite and alternatives; end DPF_STATUS: BLOCKED.",
    }


def operation(stage, text):
    lines = [line.strip() for line in text.splitlines()]
    if any(line.startswith("DPF_OPERATION_REQUEST:")
           or (line.lstrip("`*- ").startswith("DPF_RUN:")
               and not line.startswith("DPF_RUN:")) for line in lines):
        raise WorkflowError("Malformed operation request: end the report with exactly DPF_RUN: "
                            "<available operation>, without a prefix or code fence; "
                            "do not request prerequisite repair for a missing operation receipt.")
    markers = [line.strip()[8:].strip() for line in text.splitlines()
               if line.strip().startswith("DPF_RUN:")]
    if not markers:
        return None
    task = TASKS.get(stage)
    if (len(markers) != 1 or task is None or markers[0] not in task.operations
            or text.rstrip().splitlines()[-1].strip() != f"DPF_RUN: {markers[0]}"
            or any(line.startswith(("DPF_REPAIR_STAGE:", "DPF_REVIEW:",
                                    "DPF_SELF_REVIEW:", "DPF_STATUS:")) for line in lines)):
        raise WorkflowError(f"Invalid operation for {stage}; allowed: {task.operations if task else ()}")
    return markers[0]
