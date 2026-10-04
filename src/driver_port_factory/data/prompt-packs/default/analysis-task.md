# Evidence sufficient for a complete first delivery

Development checks retain earlier passing observations; do not rerun them merely to renew records,
session IDs, temporary paths or reports. Historical results are not final acceptance.
After delivery, the controller runs the complete required public suite once on one final source
snapshot and built artifact, without reusing individual development case results. For pvpanic this
means all three prepared cases in one batch. If all pass, finish without another model review or
test run. Resuming the already-passing final batch reuses it; temporary PATH/log/report differences
are not candidate versions and never request another run. Fix actual failed cases, not metadata.

Produce one source-to-target analysis and contract/test plan for the frozen scope. The task is
ready when required behavior, target mappings, executable acceptance and design-changing unknowns
are clear enough to implement. The controller records this one report under several evidence roles.
Do not write separate versions for platform study, contracts and test matrix.

When an accepted study/report already exists, use it as the working baseline. Preserve settled
source facts, mappings and stable contract IDs; fill only missing behavioral obligations, executable
oracles and unresolved decisions needed for delivery. Reopen a conclusion only for conflicting
evidence or changed inputs. Update the existing report with those changes rather than retelling the
investigation. If the prepared combined report already covers the frozen scope, submit it unchanged.
An absent Open decisions heading is not evidence of either completeness or a defect.

Read the source entry and shared driver core to identify the obligations within functional_scope.
These obligations do not each require a separate implementation work package. In the existing report state the functional boundary once: behavior IDs,
required result, retained or adapted caller/control surface, explicit exclusions and their authority,
and actual-kernel versus callback-harness acceptance. Device-variant exclusions are not behavior
exclusions. Do not infer exclusions from cost, missing target APIs or absence of tests. If a source
obligation lacks a target mechanism, identify its concrete impact and resolve the required prerequisite
or scope decision; do not silently label it NOT_APPLICABLE. No separate scope report or model call.

Study target definitions only where they decide whether an obligation can be met or change the
integration strategy. Examples include ownership transfer, callback quiescence, nonblocking failure,
and whether the target has an appropriate publication/control path. Follow the closest relevant
call site to answer that question; do not trace an analogous driver's unrelated lifecycle or hardware.
Local method spelling, file layout, helper types and detailed build edits can be resolved during the
selected implementation behavior. Mark them as deferred choices without claiming verification.
No full platform profile, every-API evidence table or preimplementation VERIFIED quota is required.

The analysis stops when each in-scope behavior has a source basis, an intended target owner/path,
a distinguishing stimulus and assertion (shared scenarios are allowed), and no unresolved prerequisite
that would change its required result or the integration strategy. Future coding details and unrun
planned tests do not themselves require more research. A real unresolved prerequisite states the fact,
why either answer matters and the smallest source lookup/observation that resolves it, or is blocked.
This is a semantic work boundary, not a heading/count gate or a claim of exhaustive correctness.

Use a short delivery entry point in the same report linking behavior IDs to those decisions and tests.
Keep each source obligation, target responsibility and test interpretation in one place; do not restate
it separately as a profile row, API row, contract row and self-review paragraph. Framework implementation
and exact API checks happen in the controller-selected behavior while preserving these obligations.
Use supplied paths, accepted decisions and environment receipts; do not rediscover their history.

For consequential adaptations, state the source trigger and required result, which target owner
(driver, framework or both) guarantees it, and the caller/context/lifetime preconditions of that
guarantee. Preserve early returns, failed acquisition, progress and cleanup conditions. An API
name or guard type alone is not a semantic mapping. Distinguish frozen behavioral requirements,
accepted implementation choices and unresolved hypotheses. Keep each decision once in this report
with original source/target locations; link to it from delivery essentials where useful. No new
heading, responsibility quota, per-function table or separate semantic report is required.

When prepared_public_tests is supplied, its listed case IDs are the complete required runtime
check set, owned by the operator. Your report cannot add mandatory ktests, mocks, fault-injection
or new executable checks. Map obligations to existing cases where applicable. For other obligations,
state the bounded source argument (trigger -> implementation owner/preconditions -> guarantee)
and its validation limits. Missing exhaustive runtime coverage alone is not a blocker. A concrete
violation or an unresolved necessary fact that changes correctness still needs resolution or a
specific blocker; never label an unproved obligation as proved because public tests pass.
Suggestions for future benchmark extensions are limitations, not tasks for the implementation model.

When no prepared suite is supplied, choose the smallest sufficient tests for each obligation.
Reuse one scenario for multiple claims
when its observations genuinely distinguish them. Additional tests need a concrete uncovered behavior
or uncertainty; do not enumerate combinations just because a generator supports them. A planned test
must identify stimulus and assertion, not only a file name or PASS marker. Never remove required scope
for convenience. Do not claim PLANNED/NOT_RUN as executed. Keep source-derived tests distinct from new
migration assertions, and separate QEMU model observations from actual driver execution.

On a contract repair, retain unaffected conclusions and IDs. Correct the cited mapping/oracle and its
actual dependents, update Delivery essentials if necessary, and stop when the identified defect and
related obligations are resolved. Do not restart source closure or platform discovery without new evidence.
The supplied Skill references provide detailed techniques on demand; this task does not require
executing every phase or checklist in a referenced workflow. Original evidence and frozen requirements
remain authoritative. No cosmetic formatting or optional navigation requirement justifies rework.

Only when submitting knowledge retrieval evidence, save optional report.probes.json beside report.md:
{"mode":"focused","probes":[{"topic":"registration-lifecycle","query":"actual target API symbols",
"applicability":"APPLICABLE","interpretation":"What this lookup establishes or leaves uncertain",
"evidence":[{"evidence":"evidence_ref from knowledge search","line_start":1,"line_end":10}]}]}
Use knowledge retrieval only to answer a current information gap. Record only queries actually
needed, grouped by topic; no seven-topic survey or inapplicability proof is required. Omit this file when submitting no retrieval evidence. The controller records
NO_RETRIEVAL_REQUESTED, never semantic coverage; no empty file is required.
The known topic names are registration-lifecycle, resources-io-dma, interrupts-concurrency,
ownership-errors-recovery, rust-safety-style, analogous-driver-framework, artifact-packaging-qemu.
For a recorded query use knowledge search --domain target --limit 20; citations must be within
returned chunk ranges. The controller replays queries, verifies original bytes and binds this
receipt to the report. These checks establish provenance, not semantic correctness or completeness.
Direct inspection of the pinned target tree remains valid analysis evidence: cite path/lines in the
same report. A search miss alone is not evidence_closure rework; retrieve the known definition from
the acquired tree. Request corpus repair only if a needed original is actually unavailable or the
frozen evidence boundary needs revision. Never invent chunk IDs or claim an unresolved API guarantee.
Self-check mappings against source obligations and target definitions whether or not review is enabled.
knowledge_probe_check --report <report.md> is an optional local replay, not another model task.
Contract repairs return to this joint analysis. Preserve unaffected conclusions; the controller
rebinds handoff and contracts after accepting the corrected report.


## Route protocol

Work in this order: source obligations -> coarse target route -> design-changing premises ->
original definitions or necessary minimal observations -> contracts and meaningful behaviors.
Keep one report.md and report.route.json. The controller uses the latter directly to seed
implementation; there is no second planning task. Route steps are not implementation units.
A B entry is a complete implementation work package, not a source function, contract, API,
test case or numbered route step. For a small cohesive driver, use one B for the whole scoped
implementation and its checks. It may cover registration, capability/state, event/control paths,
framework adaptation, synchronization, failure handling and cleanup together. Multiple C obligations,
source entrypoints or test scenarios do not imply multiple B entries.
Split only where separate goals help manage genuinely substantial or independent work. A separately
observable path is not by itself a reason for another handoff. Keep necessary lifecycle and cleanup
inside the goal that owns them; do not create standalone initialization, locking or cleanup tasks.
No minimum, target count or fixed maximum of packages; do not expand a simple driver into a template.
The controller's one-package-per-round boundary limits scope, not how many code edits, lookups,
builds or tests the worker may perform inside that package. One package may finish in one round,
or continue in the same session if unfinished. Keep later implementation details on demand.
Use the existing B prose and contract links; no split-justification form, extra reviewer or report.

Use short IDs as Markdown headings: `## R1`, `## B1`, `## C1`, `## P1`.
Write ordinary prose under them. Each B section describes its complete implementation goal, including its cleanup;
each C section states a source obligation and its existing test evidence or bounded source argument
and validation limits. With a prepared suite, do not invent additional required tests. IDs identify
sections so you need not repeat long titles. A `section` override is available for an existing heading;
it must name that exact heading. Do not put explanations in JSON.

Minimal reference index (replace paths/lines with inspected originals):
```json
{"main_route":[{"id":"R1"}],
 "behaviors":[{"id":"B1","route":["R1"],"contracts":["C1","C2"]}],
 "contracts":[{"id":"C1","sources":[
   {"repository":"source","path":"driver.c","line_start":1,"line_end":8}]},
 {"id":"C2","sources":[
   {"repository":"source","path":"driver.c","line_start":9,"line_end":16}]}]}
```
This single B1 covers both C1 and C2; write their distinct obligations in the C sections.
Add `depends_on:["B1"]` only if a separate work package is useful and actually depends on B1.
`premises` and `learn` default to empty; do not create placeholder entries.
Every contract links to a behavior; every behavior links to its route and contracts. These links
are navigation, not proof of semantic coverage. Source obligations and test oracles are frozen;
target routes may be corrected locally without changing them. Route steps never become behaviors
automatically. Keep distinct meaningful behaviors in their own sections.

For a design-changing premise established by source, add a P section explaining the fact, route
consequence, evidence and limitations, and a row such as:
```json
{"id":"P1","route":["R1"],"status":"supported","sources":[
  {"repository":"target","path":"api.rs","line_start":10,"line_end":20}]}
```
Place this row in `premises`. Source repository names are source, target or qemu; paths are
repository-relative. Source-backed premises need NO question_section or probe_receipts fields.
Use status `open` for an unresolved premise; do not label missing evidence as supported.
No checklist of all APIs or hypothetical risks. Local coding details remain in implementation.

Only if a critical fact needs execution: add `question_section:"Q1"` to that premise and write
`## Q1` with the stable fact to determine, how either answer changes the route, and the smallest
observation that resolves it. Keep the answer in the P section. Save the report/index, then call
`driver_checks.analysis` action=probe with report, premise ID, script (target-worktree path), and
optional timeout. The controller returns a short receipt ID. After interpreting a successful
observation, put ONLY that ID in `probe_receipts`, e.g. `["P1"]`, and update the P conclusion/status.
Never put source explanations in probe_receipts. No execution means omit that field entirely.
Do not change the Q section after observing it and reuse its receipt. Use the configured platform;
failed/unrun/stale probes cannot support a premise. No obligatory probe count or separate report.
If a critical fact cannot be resolved, report blocked with its implication and evidence.

Submit report.md with the supplied submission command. It checks structure, references, source
locations and receipts BEFORE recording your submission, and returns the detectable issues together.
If it fails, correct the indicated entries and resubmit IN THIS SAME TURN; no new analysis report,
restarted survey, extra model phase or final success message. Success records a candidate, not driver
acceptance. `analysis inspect` is optional; do not call it as a mandatory extra step. The controller
owns hashes and internal bookkeeping. `learn:["P1"]` optionally reuses that P section in shared
knowledge; omit it when there is no useful reusable finding. No separate learning report.

If prepared_public_tests is provided, read its short interface before choosing the route. Public
stimuli and assertions are supplied: plan driver/interface adaptation and cite existing case IDs,
not a test-framework design project. Those scenarios do not prescribe separate behaviors. Keep
source obligations beyond observed test coverage explicit using source arguments and limits;
analysis prose never expands the operator-provided runtime acceptance set. Do not replace full native scope with the test interface's narrower view.

When configured, `driver_checks.knowledge_learn` can save an already-discovered reusable lesson:
lesson, conditions, sources (repository:path:start-end, one per line). Optional; no extra
research, report, hashes or completion requirement. Publication is controller-owned after acceptance.
