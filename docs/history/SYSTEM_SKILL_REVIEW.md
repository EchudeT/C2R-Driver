# Skill/system audit and repair record

Baseline: `bb0ead8`. One independent project auditor (`system_skill_review`)
received the complete task and evidence locations before reviewing; no supplemental
instructions were sent during its audit. Three rounds of audit, repair and re-review are complete. The same auditor
reported no remaining definite actionable defect in the audited scope.

## Evidence and policy

- Compared acquisition and migration Skill instructions, including workflow and
  QEMU evidence references, with current controller paths and rendered-task sources.
- Direct-Skill rollout: `rollout-2026-09-13T16-56-26-01a099fb-6bca-7c81-8f75-7c77f42a30e6.jsonl`.
  NE2000 reused local Linux/Asterinas sources; E1000 final report corrections
  reused passing frozen QEMU evidence (September 14, 17:08 and 17:19 UTC).
- User-approved overrides remain: no mandatory full semantic index; one persistent
  worker performs meaningful self-checks; no automatic developer reviewer; no blind run.
- Runtime failures remain failures even when the worker accepts a checker disagreement.
  Missing outputs are not made successful by a textual acceptance marker.

## Findings and current disposition

1. **Confirmed, changed and re-reviewed:** recovery fingerprints now include
   current delivery source changes, scripts/helpers/runtime payload and acquisition
   selections, not just upstream artifacts. Report wording does not reset execution
   failure counts. A durable PAUSED request survives restart; changed inputs resume
   locally. `stage recovery-resume --reason` records operator authority for external
   changes the snapshot cannot observe. This is a coarse stagnation limit, not a
   claim to infer every causal variable. Second review identified failed-validator
   recursion and missing design-report identity; repaired with tolerant file/Git
   observations, report identities for design/study, and normalized diagnostic
   identity separating distinct failures. Updated checker-decision.md to match.
2. **Confirmed, changed:** invalid prerequisite REWORK previously bypassed recovery
   in an unlimited worker loop. It now enters the existing durable recovery path.
   The same auditor confirmed this path now shares durable recovery and its stop rule.
3. **Confirmed, changed:** public QEMU reuse included report text. Execution reuse
   now compares frozen inputs, script and helpers. Passing receipts survive prose-only
   edits; failure receipts replay for the same persisted worker job but a new worker
   operation can retry. Removed a separate prepared-repair path that reused any failed
   receipt without checking its executable inputs. Final report prose cannot redefine
   an oracle: executable oracle inputs belong in the captured script/helpers.
4. **Confirmed, changed and re-reviewed:** failed release-tag fetches now perform a
   failure-only `ls-remote --exit-code` diagnostic. Exit 2 means the remote answered
   but lacks the selected tag: normal same-stage worker recovery can correct it.
   Timeouts and unavailable remotes remain transport pauses. Successful downloads
   incur no extra probe. Failed full-commit downloads with a reachable remote go
   to worker diagnosis (not an automatic claim that unadvertised commits are absent).
   An unavailable remote remains ambiguous: normal resume retries transport without
   a paid decision; `stage recovery-resume ... --reason ...` explicitly requests
   same-stage worker diagnosis of a recorded fetch failure, including a wrong URL.
5. **Confirmed, changed:** fixed checkout/lock paths prevented correction before
   acquisition completion. Repository stores now have URL identity, checkout/worktree
   paths have commit identity, and lock paths have content identity. Same selections
   reuse their resources; corrected selections cannot overwrite previous evidence.
   Stage PASS still prevents silently changing frozen revisions.
6. **Confirmed, changed:** completion audit emitted permanently empty contract/test
   arrays. Removed those arrays and their vacuous validator; audit references the
   existing final worker report. Public task now explicitly requires actual results
   and unexecuted reasons by existing contract/test ID or section, without new tables
   or machine parsing of narrative semantics.
7. **Confirmed, changed and re-reviewed:** `port run` and `init` accept repeatable
   `--baseline-repository PATH`, frozen in project configuration and exposed to
   acquisition as supplied upstream baselines. The controller matches origin and
   requested revision, imports only committed Git objects into its own repository,
   then confirms the public revision through normal origin fetch. That fetch can
   negotiate with imported objects rather than downloading the full selected tree.
   No alternates dependency, dirty worktree copying or historical-answer scanning.
   Cache import evidence survives restart. Network confirmation remains required;
   savings on real Linux acquisition have not yet been measured.
8. **Confirmed, changed:** public prompt advertised an unconsumed independent-review
   request. Removed it; no replacement paid reviewer stage was added.
9. **Confirmed, changed and re-reviewed:** packaging supports necessary image
   variants under `.dpf-output/harness/variants/`. Each is a recorded CAS artifact,
   tied to the existing preparation identity/presence check and source snapshot;
   public receipts report per-variant observed boot and frozen identity. Worker
   reports attribute configuration/source differences and individual test results.
   Prompt and protocol no longer force all scenarios into one image. Production
   regressions remain required after instrumented runs; observed boot alone is not
   proof of a variant's functional oracle or source/configuration lineage.

Additional verified issue fixed: environment/public harnesses no longer require
strace merely to execute and capture outputs. The shared command builder creates
an honest empty exec trace plus collector availability metadata when unavailable;
commands and logs still run/capture, and missing QEMU observations remain failed
mechanical evidence subject to worker judgment. No dummy execution is added.
Second review reproduced installed-but-unusable tracing. A separate bounded
`strace /bin/true` collector preflight now records its command result and falls
back before the real script if unavailable. No fallback reruns a possibly executed
real script. Flow checks cover missing, usable and present-but-failing tracers.

The common worker agreement was rewritten into five numbered sections: authority,
evidence/workspace, work/continuity, failure/acceptance and delivery. Task JSON still
separates instructions from reference_material. This follows the official guidance
on Markdown sections and delimited reference content:
https://developers.openai.com/api/docs/guides/prompt-engineering
No model setting changed. Composite repair instructions moved out of Python into
the same prompt pack's numbered `repair.md`; substantive stages retain their own
objectives and the protocol owns available operations.

Final review covered prompt rendering boundaries, recovery paths and phase restrictions;
flow tests exercise rendered instructions/reference separation. This is evidence for
the inspected paths, not proof of every possible execution.

## Verification boundaries

Retained only three flow test files. They use actual local Git, command execution,
CAS and ledger operations, with synthetic sources/worker/QEMU fixtures; they do not
certify a real driver or measure migration cost. Extended existing journeys for
report-only execution reuse, interrupted failed-receipt replay and corrected source
selection. No new paid migration was launched. Real acquisition/build/QEMU performance
and actual cost remain experiment questions, not static acceptance claims.

## Second review

The same auditor independently confirmed eight of the original nine findings
addressed, and reproduced four remaining issues: recovery observation reran a
failing source validator, design reports were absent from progress identity,
checker-decision.md retained the old budget statement, and unusable installed
strace prevented script execution. All four were repaired and sent back together;
no supplemental messages were sent during either re-review.

## Final review and requirement evidence

The third review confirmed all four second-review defects fixed and no new
definite actionable finding. The auditor independently ran the recovery journeys:
11 passed (21.76 seconds), inspected abnormal source observations and checked the
diff. Main-agent full retained suite: 17 passed; F821/F811/F601 and diff checks pass.

| Requested scope | Evidence and disposition |
| --- | --- |
| Commit before independent audit | bb0ead8 predates system_skill_review first task |
| One fully briefed auditor, no incremental briefing | Same system_skill_review used all three rounds; each task contained full scope, paths, changes and limits |
| Redundancy versus Skill/history | First audit and re-review identified acquisition reuse, execution replay and removed developer reviewer; items 3, 7, 8 |
| Incorrect rollback/checks | Unified durable recovery, sealed phases, worker acceptance with honest raw failures; items 1, 2, 4, 5 and tolerant observations |
| Systematic prompts matching implementation | Five-section agreement, numbered repair/public task, actual operation protocol, instructions/reference separation, corrected recovery budget |
| Substantive Skill alignment | Target-original study, source/contracts/test plan, implementation self-check, artifact identities/variants, public QEMU and per-contract/test final reporting retained; user-approved AST/reviewer overrides explicit |
| Fix then review until no actionable finding | First nine findings repaired; second four findings repaired; third review found no remaining definite actionable defect |

The audit cycle is complete. It does not certify a real driver, guarantee absence
of all future bugs, or establish that this workflow is cheaper than direct Skill.
No new paid run or remote push was performed as part of this audit.
