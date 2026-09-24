# Final evidence review

Write only the independent final evidence review report. Source, current implementation files,
artifact metadata, runtime receipts and worker reports are read-only. Review all assigned delivery
evidence once and return one consolidated report containing every substantive finding.

This stage does not reopen the evidence-and-design phase. The supplied contract and test-matrix
digests are frozen acceptance oracles: use their existing IDs to map observed behavior, but do not
redesign, re-prove, or criticize their source provenance, target-platform study, handoff, or API
research. Those questions belong to `analysis_review` and its prerequisites. If a sealed design
premise appears invalid, report the concrete impact as a blocker; do not request a design-stage
repair from this stage.

## What to verify

- The current Rust implementation and target-change inventory match the implementation snapshot.
- The target-framework enablement bundle, change inventory, report and target worktree snapshot
  match each other and the implementation inputs; any target API/framework change is minimal,
  evidenced and checked.
- The runtime artifact, packaged variants, entrypoint and artifact identity match the current code.
  Treat the CAS runtime bytes, the artifact-preparation attempt, the checker result and the
  implementation snapshot as the authoritative artifact identity. Human-readable artifact
  receipts, presence summaries and worker prose are derived metadata; a stale or refreshed
  receipt alone is not a packaging defect and must not be routed to `artifact_preparation` or
  trigger another QEMU run.
- The public QEMU receipt, script, helper inputs, logs, traces, oracle outcomes and limits are
  internally consistent and actually bind the runtime artifact.
- Each frozen contract/test ID that is exercised by the public evidence has a traceable
  PASS/FAIL/INCONCLUSIVE/BLOCKED status. Do not invent new contracts or test obligations.
- Findings are routed only to the delivery owner: target framework enablement, driver
  implementation, artifact preparation, or unchanged-artifact public QEMU validation. Route
  to `artifact_preparation` only when authoritative runtime/CAS, checker, variant, entrypoint,
  or packaging identity is wrong. Route an unchanged-artifact harness/oracle defect to
  `public_qemu_validation`. The reviewer never edits source or runtime files.

Use precise paths, line numbers, receipt fields and log locations for findings and core positive
conclusions. A PASS means the assigned delivery evidence is complete; it does not certify private
blind evaluation or real-hardware behavior that was not run.
