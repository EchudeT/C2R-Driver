Resolve this stage's execution or checker findings using existing evidence.
Repeated failures with unchanged repair inputs pause recovery. Continue meaningful repairs;
if no causal repair is available, report BLOCKED instead of repeating unchanged failing work.
You have final functional acceptance authority, including over risk/review recommendations.

The decision record distinguishes captured outputs from an interrupted operation (artifacts: null).
For an interrupted operation, diagnose the tool or input failure and repair this same stage.
Reuse completed work; do not restart prior stages. Missing outputs cannot be published
by ACCEPT. Preserve original evidence; never rewrite failed results as successful checks.

If the work meets the Skill requirements, explain why the findings do not invalidate it and cite
evidence in one Markdown report ending DPF_CHECKER_DECISION: ACCEPT. Unchanged captured outputs
are published without rerunning checks or rewriting results. If capture was interrupted or outputs
changed, the controller instead processes the current deliverables through normal capture/checks;
retain the stage's required self-check in that report. ACCEPT never creates missing artifacts.
A command failure remains a command failure, even if it does not invalidate the work.

For a real defect, repair the smallest affected inputs and submit the normal stage deliverable
directly, using its usual JSON or REPORT_PATH protocol and available operations. No separate
retry decision or duplicate submission is needed. For a static stage, return REPORT_PATH to a
brief repair explanation; the controller reruns that operation. Do not repair correct code to fit a collector limitation.
For an external blocker explain it and end DPF_STATUS: BLOCKED.
Acceptance and blocker explanations use REPORT_PATH; no extra checklist or reviewer conversation.
