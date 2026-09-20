# Runtime supervision and cost audit

Updated: 2026-09-20. Priority: stability first, cost second.
This is an operator-maintained record, not another worker deliverable or review gate.
Do not change worker code or inject messages into the running conversation.

## Comparable historical baseline

User-identified Codex workspace: `/home/unix/file/C2R-Driver`.
Read-only session lookup identified thread `01a099fb-6bca-7c81-8f75-7c77f42a30e6`.
The thread also includes e1000 work, documentation, and skill development: its whole-thread
cost is NOT the NE2000 baseline.

NE2000 migration request: 2026-09-13 09:21:35 UTC; initial completion: 10:44:46 UTC.
Approximately 83 minutes including the ISA/PCI clarification (PCI selected 09:24:31).
Subtracting the token-count snapshot before that request from the snapshot at completion:

- Input: 47,777,249; cached input: 45,208,437; uncached input: 2,568,812.
- Output: 191,033 (reasoning already included).
- 357 tool calls; five recorded compactions.
- At this project's existing short-context rates (4 / 0.4 / 20 dollars per million
  uncached / cached / output tokens), approximately **$32.18**. This is a normalized
  comparison, not verification of the relay invoice or original context-tier billing.
- Completion message claims TX/RX, IRQ, ring wrap, frame boundaries, two cold boots,
  negative identity, VirtIO coexistence and SMP=2. These claims have not been independently
  rerun in this audit. Source revisions and skill version differ from the current run.

Evidence: local rollout
`/home/unix/.codex/sessions/2026/09/13/rollout-2026-09-13T16-56-26-01a099fb-6bca-7c81-8f75-7c77f42a30e6.jsonl`.
The initial result is also described in `/home/unix/file/C2R-Driver/NE2000-PCI-MIGRATION.zh-CN.md`.

E1000 in the same thread: request window 2026-09-14 13:02:51–17:23:11 UTC includes waiting
and repeated requests. The explicit continuation started 15:12:49; completion at 17:23:10
is about 130 minutes later. Do not describe the whole request window as active compute time.
Recorded token deltas: input 64,155,104; cached 62,037,460; output 256,670. At the same
normalization rates this is about $38.42, not an invoice or a matched-revision experiment.

## Direct-skill context comparison (re-read 2026-09-20)

The original conversations do contain contracts, test matrices, compliance checks and final
documents. Their advantage is not skipping those requirements:

- NE2000 09-13 09:55:44: the worker detected an RX ring next-page invariant and performed
  the pre-artifact compliance audit itself.
- NE2000 10:09:00: run 0008 hung at interface enumeration before ping, with an empty PCAP;
  the worker attributed it to the harness, changed that script and retained driver conclusions.
- E1000 09-14 15:54:27: self-check found incorrect TX gap parameters (10/10/10 vs 8/8/6),
  corrected code/contracts and reran affected static checks.
- E1000 17:08:12 and 17:19:06: final evidence/handoff fixes reused frozen passing runs;
  the worker explicitly did not rerun QEMU for the missing handoff document.

Current design follows those patterns: one persistent worker, combined planning, implementation
plus self-check, scoped repair, and no unconditional second-model review. Twenty ledger checkpoints
do not mean twenty paid conversations. The blanket trigger for any preexisting target-code change
was removed: ordinary safe integration must not cause a second audit merely because it touches an
existing file. Independent review now triggers on unsafe syntax in changed Rust or a worker's
explicit request, including a repository mandate. The worker still performs required self-checks;
absence of a trigger is not a static proof of correctness. This is more conservative than the
historical worker-only flow, and the cost-effectiveness of that residual review is not yet measured.

This audit reduced the common wrapper from 12,681 to 6,469 bytes; detailed reviewer instructions
are only in the conditional review objective. Skill originals are unchanged. No saved-token or
price percentage is inferred from wrapper bytes. Remaining measurable costs include preimplementation
research, broad tool output, and the controller's final replay of a worker-authored harness. Do not
claim total cost below the direct-skill baseline until a comparable new paid run measures it.

Offline flow fixes: reject failed QEMU before evidence closure; check added as well as modified
source paths before/after packaging and runtime; freeze report bodies for restart/repair;
carry the active retry reason from the ledger; bind public results to captured execution;
remove obsolete failed-repair attribution and the separate diagnostic bypass. Old interface-only
tests were removed, preserving behavioral checks. No historical driver code was copied into a worker.

## Current run: e2e-ne2000-cost-01

Snapshot before resumed packaging: **$85.76 known estimated cost**, with three unknown/unpriced
calls; therefore not a complete invoice. Known input 100,542,643, output 463,225.
Elapsed stage time includes periods when the controller was stopped; do not call it AI compute time.

Largest costs:

- Implementation: $45.25 known. First implementation alone $26.77; first repair $14.64;
  second repair $3.85. The interrupted erroneous third repair has unknown usage.
- Public QEMU: $10.41, including correcting the harness/artifact boundary.
- Compliance review: $5.14 known; runtime review $2.25.
- All stages before implementation together: approximately $21.01.

The first implementation consumed 5,048,019 uncached input tokens out of 18,059,091 input
(about 72% cache hit, versus roughly 95% over the historical direct migration).
Caching/large tool responses need investigation; the data alone do NOT prove a relay cache bug.
Eliminating reviews alone would not explain or remove the cost difference.

Completed-command event inspection adds concrete context-volume evidence: first implementation
executed 95 commands returning about 930,457 characters in aggregate, with three single outputs
of 51–57k characters. Second compliance review had a single broad contract grep returning 125,560
characters. The interrupted wrongly routed repair returned 249,495 characters in just ten commands.
These are tool-output character counts, not token counts, and must not be billed as tokens directly.
Targeted section/symbol retrieval is a better candidate than adding more handoff forms.

## Confirmed failure paths and fixes

1. A failed public script unconditionally invalidated implementation, and every runtime REWORK
   did the same. The current worker (or a risk-triggered reviewer) selects the smallest affected gate:
   implementation, packaging, or public harness. Missing/ambiguous routing cannot default to 17.
2. Runtime feedback previously reached implementation but not necessarily packaging/harness.
   It is now supplied to migration contexts from the persisted review result across restarts.
3. A reviewer PASS could conflict with a failed structured execution result. Both controller
   acceptance and bundle validation now reject this contradiction.
4. Reviewed-source drift during packaging was treated as a same-stage output defect.
   Detected drift now explicitly routes back to implementation rather than repeatedly reprompting
   a packaging worker that cannot legitimately accept it.
5. Exact-artifact binding failures from otherwise observed QEMU executions now produce actionable
   same-stage harness feedback; missing collector evidence is still distinguished from driver failure.

Targeted regression run: 28 tests passed across repair routing, workflow alignment,
runtime boundaries, and public harness tests. This is NOT a claim that the full legacy suite passes.

Current-route recovery at 15:49 UTC used the original immutable implementation bundle
`ee59286757fb813070b3ef3c389bd57d5b833773b11cacfab7e835c7413d9b6a` and the original
PASS compliance report `45904a048748aacdc2bf08d7b04fe160b1b75de2eba47a4448a25acdb84e12fe`.
All 16 files matched. Ordinary finalize validators ran again; no direct SQLite state rewrite
and no invented verdict. Two post-snapshot Markdown reports were moved, unchanged, into
`.dpf-output/route-recovery-reports/` to preserve the original implementation inventory.
One database backup and the hash-pinned recovery script remain in the run/control directories.

Controller PID 2411891 resumed at 15:51 UTC, stage 19, then was stopped on user request at
2026-09-19 16:05:03 UTC. It has not been restarted. The old run is historical evidence, not a
candidate for an in-place upgrade to the new stage layout. Worker explicitly acknowledged packaging-only
repair, retaining reviewed Rust/integration sources and adding an automatic guest entrypoint under
`.dpf-output`. A first launch used a resolved Python symlink and lost the venv; it exited before any
model call. Relaunch preserved the venv interpreter path. Record this launcher pitfall, not a model defect.

## Earlier programmatic run: e2e-ne2000-persistent-02

Ledger shows two implementation-compliance reworks and a third rollback from runtime on
2026-09-18 18:43 UTC using the same unconditional implementation route. Thus the routing flaw
predates this run. Its terminal workflow stages are PASS, but old metrics lack enough pricing/model
information for the current estimator; zero displayed dollars must not be interpreted as free.
Implementation occupied roughly 140 minutes; review roughly 24 minutes.

## Prioritized improvements to validate, not add blindly

1. **Plan executable validation before freezing the image.** Packaging must receive the test matrix
   and know required guest entrypoints/observability, not merely contain driver strings.
   Current blocker was foreseeable: a boot-only image cannot run bidirectional traffic tests.
   Reuse normal worker planning and presence check; do not add a planning agent or manifest.
2. **Complete the first functional review.** Current second review explicitly acknowledges that
   multicast-MAC rejection was missed initially; another concurrency defect emerged after repair.
   Ask for all evidenced blockers in one pass and retain resolved findings. Do not assume every
   new finding is style policing: IMR masking and TX-generation ownership are functional issues.
3. **Reduce broad context reads, measure cache performance per call.** First implementation's
   unusually high uncached input matters more than a few small report fields. Compare actual
   tool output sizes/repeated source reads before changing compaction or model settings.
4. **Incremental regressions.** Reuse unchanged build/test evidence keyed to relevant files and
   environment, not merely prose saying it passed. Avoid full suite reruns for packaging-only changes.
5. **Keep stage gates but batch related work in one worker turn where safe.** Historical direct run
   combined driver and executable test development; disjoint packaging/harness planning caused late
   discovery here. Keep independent review conditional and scoped, not redundant narrative handoffs.
6. **Preflight controller boundaries offline.** Shell selection, exact artifact mounting, timeout
   cleanup and error routing should fail in fixture tests before spending a model turn.
7. **Accounting reliability.** Separate stopped time from active wall time and collect partial
   usage on interrupted calls when evidence is available. Never price unknown calls as zero.

Do not copy the historical candidate code or inject its solution into the current worker.
The comparison is for controller efficiency and validation design, not bypassing this run's contract.
