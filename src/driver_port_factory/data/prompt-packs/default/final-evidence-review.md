# Final evidence review

Write only the independent final evidence review report. Source, current implementation files,
artifact metadata, runtime receipts and worker reports are read-only. Review all assigned delivery
evidence once and return one consolidated report containing every substantive finding.

Use current contract and test IDs as the acceptance baseline. Do not routinely redo analysis.
If actual evidence contradicts a design premise, report the exact counterexample, affected
obligations and smallest necessary revision; request the permitted analysis prerequisite in
instructions.repair_targets. Previously resolved doubts without new evidence are not defects.
Scope or frozen upstream changes still require user authority. Preserve unrelated valid results.

## What to verify

- The current Rust implementation and target-change inventory match the implementation snapshot.
- The target-framework enablement bundle, change inventory, report and target worktree snapshot
  describe the earlier checkpoint and are bound into the implementation inputs. The final
  implementation snapshot supersedes checkpoint file contents and covers all changed source
  paths; any target API/framework change is minimal, evidenced and checked.
- Confirm target compliance was checked before packaging and after relevant repairs. Inspect the
  affected original target rules for API/layering, safety, concurrency, lifecycle, errors and build;
  target changes need necessity, alternatives, affected callers, checks and rollback. Missing prose
  alone is not a defect: identify the concrete violated rule or unsupported correctness premise.
- The runtime artifact, packaged variants, entrypoint and artifact identity match the current code.
  Treat the CAS runtime bytes, the artifact-preparation attempt, the checker result and the
  implementation snapshot as the authoritative artifact identity. Human-readable artifact
  receipts, presence summaries and worker prose are derived metadata; a stale or refreshed
  receipt alone is not a packaging defect and must not be routed to `artifact_preparation` or
  trigger another QEMU run.
- The public QEMU receipt, script, helper inputs, logs, traces, oracle outcomes and limits are
  internally consistent and actually bind the runtime artifact.
- Every required contract/test ID, including those not exercised, has a traceable
  PASS/FAIL/INCONCLUSIVE/BLOCKED status. Do not invent new contracts or test obligations.
- Route all framework, driver and shared-integration source defects to `driver_implementation`.
  Other delivery owners are artifact preparation and unchanged-artifact public QEMU validation. Route
  to `artifact_preparation` only when authoritative runtime/CAS, checker, variant, entrypoint,
  or packaging identity is wrong. Route an unchanged-artifact harness/oracle defect to
  `public_qemu_validation`. The reviewer never edits source or runtime files.

Use precise paths, line numbers, receipt fields and log locations for findings and core positive
conclusions. A PASS means the assigned delivery evidence is complete; it does not certify private
blind evaluation or real-hardware behavior that was not run.
