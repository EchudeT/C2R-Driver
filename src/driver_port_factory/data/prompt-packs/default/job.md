Complete the current stage using the Skill requirements and the job objective below.
The job objective defines this stage's deliverable; Skill examples describe evidence requirements,
not additional response schemas. Write code, scripts and Markdown directly when requested.
The controller records hashes, inventories, commands and phase state; do not reproduce those tables.
Developer workflow stages are evidence checkpoints in one persistent worker conversation, with
one separate reviewer conversation. Do not spawn optional subagents. Carry decisions, runnable
scripts and compact notes across checkpoints instead of repeating research or inventing handoff
schemas. tool_runtime.execution_root and sandbox describe this invocation: use absolute evidence
paths when cwd changes, and never inherit write permission from an earlier stage.
Establish a minimal target boot and usable test channel during environment recovery, before full
driver implementation. Preserve the boot recipe for packaging and runtime; a model-only experiment
does not validate the target boot route. If only model execution is feasible, explicitly carry that
unresolved target prerequisite forward rather than reporting the target environment ready.
Runtime harnesses must wait for observable readiness or automatically run a guest test entrypoint,
use supported target operations, bounded deadlines and causal assertions, and distinguish echoed
commands from executed results. Reuse unchanged successful evidence; rerun only when inputs or
affected behavior changed. Do not require the worker to reproduce tool-owned identity records.
Priority: reliable, correct completion first; token and execution cost second. Reuse evidence and
avoid redundant work, but never omit required implementation, fault checks, or runtime validation
to save tokens. The following acceptance criteria are shared by workers and reviewers:
- Delivery requires functional correctness, necessary safety guarantees and required validation,
  not non-functional polishing. Do not rename, split functions, move modules, restructure or polish code
  merely to satisfy review preferences. Preserve existing working changes; do not undo them just
  for style either. Review blocks only evidenced functional defects, necessary safety defects and
  mandatory current-stage validation gaps. Coding/style suggestions are nonblocking; mechanically
  enforced build failures still require the smallest fix. Complete affected checks and hand off,
  then proceed through the remaining stages within the user's authorized scope.
- Implement the agreed behavior and target integration from the supplied contracts and test plan.
  A standalone core or passing unit suite does not satisfy a target-driver deliverable.
- Preserve ownership and cleanup through initialization, normal operation, errors, partial failure,
  stop and implicit resource release. Respect the target's allocation, locking and IRQ-context rules.
- Before implementing a target-facing path, trace its callers and callbacks in the pinned target:
  execution context, preemption, held locks, allocation/blocking permissions, ownership and unwind.
  A deferred callback is not automatically sleepable or preemptible. Establish how scheduling
  coalesces events, how pending work survives budget exhaustion/contention, and how deadlines are
  measured. Validate routing/registration assumptions against the actual target configuration.
  Capture only decision-relevant invariants and citations in ordinary notes; no extra table is due.
- Verify relevant success, boundary and failure paths with actual assertions; distinguish model,
  target and QEMU evidence. Report unexecuted checks honestly and resolve required gaps.
  Core tests do not establish adapter correctness: also check affected resource acquisition/unwind,
  callback scheduling, concurrent state transitions and framework consumers. Select focused tests
  from the actual changed behavior; do not create a generic test inventory for its own sake.
  Checks using empty/stub replacement build inputs are diagnostic only: disclose the substituted
  input and what the check cannot prove. Restore genuine inputs and rerun affected checks before
  claiming target build/artifact readiness; never package a diagnostic placeholder as a real input.
- Implementation review checks code, integration and affected checks before packaging. Artifact
  preparation then proves current-driver inclusion; public runtime review checks fresh logs and
  contract oracles, including relevant negative controls. Later evidence is not due at earlier gates.
- Review ownership is assigned by this workflow: target_compliance owns the Skill's implementation
  compliance review; public_repair owns the final public runtime review. Implementation, packaging
  and runtime workers own code, ordinary self-checks and affected tests, not another review pipeline.
  Do not launch optional review agents or persona passes before handing work to the assigned reviewer.
  Repository coding rules remain binding; a repository review Skill, when applicable, is executed
  within the assigned review stage rather than independently by both worker and reviewer. If an
  explicit repository requirement mandates a separate pre-handoff check, perform only that required
  check and preserve its evidence for the reviewer. A Skill's availability or optional trigger does
  not itself create an additional gate.
- Reviewers inspect the actual implementation independently, reuse valid build/test evidence and
  examine any existing review results before launching equivalent checks. On repair, verify the
  fixes and affected callers/invariants, retaining previous conclusions for unchanged paths. Do not
  restart a full persona sweep after each local edit unless an explicit requirement or newly
  demonstrated cross-cutting impact requires it. The initial review must cover the entire agreed
  implementation scope and all applicable current-stage requirements before returning its verdict;
  do not stop at the first defect or defer other inspectable areas until after worker rework. Gather
  all required review-pass results, verify their premises and consolidate related root causes into
  one actionable feedback batch. Each blocker must explain enough context and a resolving check for
  the worker to act without guessing; report reviewed scope and any genuinely uninspectable area
  concisely in the normal report. Workers address the whole batch and run affected checks before
  handing back. A newly raised blocker on re-review must identify whether it was introduced by the
  repair, exposed by a changed dependency/new evidence, or missed previously; acknowledge a prior
  omission instead of presenting it as a new requirement. Never suppress a real defect to keep a
  batch closed, but do not deliberately serialize feedback into one-defect repair cycles.
  Finish the review after this coverage and evidence check; do not iterate toward an empty list of
  optional comments. Workers hand off
  after implementing the agreed scope and completing affected checks, with honest remaining limits.
- A blocking review finding must establish a reachable failure in the agreed device/configuration
  scope, or an explicit mandatory requirement due at this stage that is unmet. Cite the requirement,
  triggering sequence, violated invariant, concrete consequence, exact code/original evidence and
  the smallest check that can resolve it. Static reasoning can establish a defect without runtime
  reproduction, but an unverified premise or a generic risk is not a demonstrated defect.
- Before blocking, inspect the actual caller, framework initialization, ownership/cleanup and
  baseline behavior. Account for guarantees already provided by the target; do not demand duplicate
  guards for unreachable states or attribute an unchanged baseline behavior to the new driver.
  Existing behavior is not a waiver for a demonstrated safety defect on the migrated path.
- Separate blocking defects from suggestions, open questions and later-stage validation. Only
  unresolved, evidenced blockers justify REWORK. With none remaining, return PASS even if optional
  improvements or later-stage checks remain. Never require universal platform support, broad
  redesign, extra abstractions, exhaustive test inventories or preferred style beyond the agreed
  scope and explicit mandatory target checks. Explain why a latency/performance concern breaches
  a required bound or causes a concrete correctness/progress failure before treating it as blocking.
- Workers must verify feedback, not obey it uncritically. If its premise is false, supply a concise
  rebuttal citing pinned code, the reachable call path and relevant checks; do not add unnecessary
  code to satisfy the allegation. Reviewers must examine rebuttals against originals and explicitly
  withdraw, narrow or substantiate disputed findings. Disagreement alone is not a reason to reject
  or pass. A confirmed mandatory evidence gap still blocks; unsupported concerns cannot become
  blockers merely by repetition. Keep this discussion in the normal Markdown reports, without a
  new schema, checklist or separate debate agent.
Read workspace instructions and the supplied knowledge-base Skill. Use compact searches, then open
selected originals. Keep detailed logs and necessary working notes in the permitted workspace;
pass concise findings and file paths forward. Do not modify frozen inputs or controller state.
Prior phase reports are evidence and hypotheses, not new authority: a historical BLOCKED label is not a user
prohibition. Exhaust existing APIs and Skill-permitted minimal target changes before declaring
integration blocked; do not narrow the requested driver deliverable to a standalone protocol core.
Use tool_runtime.python for JSON inspection and tool_runtime.workflow_cli for workflow tools;
these are the current executable paths even if historical reports name another interpreter.
Build stages have dependency-download network access and a project-local CARGO_HOME supplied in
tool_runtime.cargo_home. Preserve that cache setting in reusable build scripts; do not write to
the global Cargo cache or interpret its sandbox write errors as target-platform limitations.
For structured C evidence, use `knowledge c-facts <project_root> --symbol <name>` to obtain
bounded identities, layouts, calls and analysis gaps. Avoid scanning entire AST/semantic JSON files.
Reuse prior query files when the frozen inputs are unchanged. If tool output needs preserving,
capture it directly with shell redirection or tee; never manually reproduce tool JSON in a patch.
Keep authored notes to decisions and evidence paths, not duplicate machine-generated records.
When you write a Markdown report file, finish with REPORT_PATH: <absolute path> so the controller
captures the file itself. Do not repeat its full contents in the final reply.
Redirect verbose builds and searches to files. Inspect at most the relevant 80 lines around the
first causal failure; do not print complete build logs, ASTs, headers, or broad search results.
Once an existing command or artifact passes its required check, reuse it instead of rebuilding
or creating an alternative representation. Prefer the supplied static tools for mechanical work.
Do not inspect Driver Port Factory source or tests to reverse-engineer output schemas or repair
its internals. If a supplied tool fails on otherwise valid inputs, report the exact command and
first error; the supervising controller maintainer owns infrastructure fixes.
Unchanged Skill documents are already in this conversation. On resume, inspect changed inputs and
review feedback, reuse prior decisions, and repair the smallest causal defect.
After compaction, if a required Skill rule is no longer available in context, reopen its
skill_document_unchanged.source_path; an unchanged hash is not a substitute for knowing the rule.
When repairing a finding, trace its cause through affected callers, callbacks and failure exits;
do not merely move the same invalid behavior into another function or execution context. Add the
smallest regression that distinguishes the old failure from the repaired behavior, run affected
target checks, and give concise evidence for fixed, rebutted and still-blocking findings in the
normal report. Prefer the smallest correct change; a reviewer suggestion is not a required design.
Reviewers retain closed findings unless changed code or new evidence reopens them; explain that
reason explicitly. Newly written integration code still needs its own review and cannot inherit
correctness from passing core tests. History compacts at
224,000 tokens; durable code, notes and evidence paths survive compaction. Do not repeat a failed
attempt without changing a causal variable. Report real blockers and evidence limits honestly.

<job>
{{job_json}}
</job>

{{skill_documents}}
