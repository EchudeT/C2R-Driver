Resolve this stage's execution or checker findings using existing evidence.
Repeated failures with unchanged repair inputs pause recovery. Continue meaningful repairs;
if no causal repair is available, report BLOCKED instead of repeating unchanged failing work.
Adjudicate mechanical findings with evidence. This decision never replaces the independent
reviewer's final functional verdict; reviewer-stage findings stay in the reviewer conversation.

The decision record distinguishes captured outputs from an interrupted operation (artifacts: null).
For an interrupted operation, diagnose the tool or input failure and repair this same stage.
Reuse completed work; do not restart prior stages. Missing outputs cannot be published
by ACCEPT. Preserve original evidence; never rewrite failed results as successful checks.

If the work meets the Skill requirements, explain why the findings do not invalidate it and cite
evidence in one Markdown report. Submit it with the tool's `pass` decision. Unchanged captured outputs
are published without rerunning checks or rewriting results. If capture was interrupted or outputs
changed, the controller instead processes the current deliverables through normal capture/checks;
retain the stage's required self-check in that report. ACCEPT never creates missing artifacts.
A command failure remains a command failure, even if it does not invalidate the work.

For a real defect, repair the smallest affected inputs and submit the normal stage deliverable
through `tool_runtime.submission_command`. Use `--decision rework --repair-stage` for a
causal prerequisite repair, `--decision blocked` for a genuine external blocker, and
`--decision pass` for an accepted checker explanation. No separate retry decision or duplicate
chat submission is needed. For a static stage, write the brief repair explanation to a file and
submit it through the same command; the controller reruns that operation. Do not repair correct
code to fit a collector limitation.
For an external blocker explain it in the report and submit it with the tool's `blocked` decision.
Acceptance and blocker explanations are files submitted through the tool; no extra checklist or reviewer conversation.
