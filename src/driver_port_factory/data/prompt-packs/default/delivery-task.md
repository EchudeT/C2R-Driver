# Continuous behavior implementation

Development checks retain earlier passing observations; do not rerun them merely to renew records,
session IDs, temporary paths or reports. Historical results are not final acceptance.
After delivery, the controller runs the complete required public suite once on one final source
snapshot and built artifact, without reusing individual development case results. For pvpanic this
means all three prepared cases in one batch. If all pass, finish without another model review or
test run. Resuming the already-passing final batch reuses it; temporary PATH/log/report differences
are not candidate versions and never request another run. Fix actual failed cases, not metadata.

The controller-selected behavior is the sole objective of this round. Its initial plan comes
directly from joint analysis; start the selected behavior without creating a second plan.
Each behavior includes necessary production wiring, framework adaptation, failure paths and cleanup.
Do not create separate survey, framework-enablement or reporting items. A small driver may have one
behavior covering all its scoped paths and multiple contracts/tests; do not split it by internal
steps or hand back after each API/function. Final delivery requirements apply only where relevant until all behaviors are implemented.

Start implementing once the current entrypoint and required API preconditions are understood.
Batch independent reads for that question; do not front-load a platform or workflow survey.
Start from behavior_progress, the accepted analysis/contract references, and the verified platform
commands. Detailed Skill documents and the knowledge base are optional references for a concrete
question, not a reading checklist. Do not reread the whole workflow or explore controller internals
for ordinary tool usage. Batch independent symbol/location searches, then read bounded definitions
and callers. Stop investigating when the evidence answers the current implementation question.

Implement and check the current behavior in the same session. Compare the source obligation, target
API guarantee, and necessary preconditions; preserve early-return, ownership, concurrency and cleanup
semantics where the source requires them. A discovered platform/API gap belongs to this behavior:
check the actual environment/firmware route before adding framework machinery, then make the smallest
necessary adaptation. Retain consequential corrections and evidence in a short progress note or the existing delivery
report. An old platform design conclusion is not immutable correctness evidence. Local adaptation
must preserve frozen obligations, test oracles, versions and environment route. If the correction
changes another behavior's implementation assumptions, update the existing analysis and its
reference index with driver_checks.analysis action=revise. Ordinary source edits require no
route revision. If frozen obligations cannot be
met, report the concrete blocker. Actual changes to those prerequisites still use explicit rework;
do not route an implementation-choice correction back through all analysis stages.

Use tool_runtime.platform_execution for verified build/format/run commands and the complete case
interface. Format affected packages before the build/runtime check, so a final formatting-only edit
does not force a second build/test cycle. Do not recreate QMP/Docker
transport or try an unrelated host toolchain. After an edit run the smallest check that can falsify
it, then affected required regressions. Establish the real device path early; enumeration is not
BAR access, and compilation is not driver initialization. Check QEMU stderr, serial and actual
assertions before attributing failure. Do not change an oracle merely to accept a failure. Reuse
checks only through current input-bound receipts. Never edit/build concurrently with a managed check.
Do not freeze a smoke-only implementation and defer required device stimuli or test entrypoints
to packaging. Implement those needed by the selected behavior before handing it back as done.

For a target framework change, retain the concrete need, relevant definition/caller, minimal change,
affected safety/cleanup obligations and validation in a progress note or the existing report. Keep this proportional to
the change; do not duplicate the full analysis, contract table or historical check logs. Read target
coding/safety rules when needed by the changed mechanism; do not add unrelated DMA/IRQ/network work.

For the last work package (behavior_progress.final_work_package=true), finish source self-check
before the final build/check. Run driver_checks.check with no cases to collect the complete required
suite, reusing current passing receipts. Then call progress(done, note=<short source reasoning and
validation limits>). This same handoff supplies delivery; no separate report-writing or final
self-check model round is scheduled. Existing controller checks capture the delivery; no extra
receipt gate is added to done. Do not rerun an unchanged passing suite with fresh=true just to hand off. Progress remains separate
from controller acceptance in stages 14/15.

End the round with driver_checks.progress(status="done" or "continue", optional note). A short note
is enough for a consequential decision or unresolved fact; no report or self-review checklist is
required for each progress handoff. Done marks only the selected behavior implemented, never accepted.
Continue retains it and the session. Existing CLI behavior_done/behavior_continue submissions remain
supported. End the round after submission; the controller selects the next item. Preserve working
code. For lengthy unfinished work use continue at a useful checkpoint rather than redoing analysis.

Prepare the required delivery files inside the last work package; the controller assembles the
short progress notes as its delivery report. For unscheduled delivery or a concrete subsequent
repair, update the existing report instead of creating a second plan. Required files:
.dpf-output/runtime-artifact, check-presence.sh, implementation-smoke.sh, public-qemu.sh and helpers.
On the managed platform these entrypoints are preinstalled. When prepared_public_tests is present,
its public cases and assertions are also preinstalled: adapt the documented interface, run them,
and retain their IDs in the final note. Do not reauthor/re-register them or add required kernel
unit tests, mock frameworks or fault injection merely because analysis prose suggested them.
Source obligations outside their observations need bounded source reasoning and stated limits;
concrete defects or unresolved necessary premises still require repair/blocking. Otherwise register each required device case with
platform action=register_case (id, case, contracts); the controller writes wrappers and the shared
case manifest. Reuse these inputs and receipts; do not rewrite scripts for final packaging. Implementation
checks may reuse prior results. Final public acceptance executes the complete suite once on the
final candidate; it cannot assemble a pass from different versions. A missing case
still needs its real stimulus and assertion; shell readiness alone is not functional coverage.
Record source-obligation coverage, required test outcomes/limits, framework changes and the final
self-check against applicable target rules. Reconcile any local design corrections; do not present
superseded premises or framework failures as driver guarantees. Complete the required runtime checks through managed tools. The last scheduled package uses
progress(done); an unscheduled delivery or concrete repair uses submit pass. Only controller gates accept delivery. No unrun requirement
may become PASS. The controller-generated handoff preserves the worker notes as the report;
no additional report format or checklist is required for scheduled completion. Optional final review follows
the frozen configuration; this task adds no reviewer or full-suite requirement per behavior.
