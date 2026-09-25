---
name: blind-c2rust-driver-evaluation
description: Prepare, freeze, run, or audit a role-separated blind evaluation of C-to-Rust driver migration candidates, including prospective held-out studies and post-hoc candidates sealed before a fresh blind-test AI designs private tests. Use for benchmark evaluation with frozen contracts, private assertions, differential execution, fault injection, and virtual or real hardware; not ordinary driver implementation.
---

# Blind C-to-Rust Driver Evaluation

Evaluate a migrated driver without allowing its producer to inspect or iteratively learn from the official private tests. This Skill applies to kernel, RTOS, userspace, VFIO, firmware, and virtual-device drivers. It evaluates candidates; it does not implement or repair them.

## Evaluation mode gate

Before reading candidate source, candidate logs, public results, or migration conversation details,
read [references/protocol.md](references/protocol.md) and select exactly one mode:

- `PROSPECTIVE_BLIND`: freeze and commit PMC/PEA before migration begins. This is the only mode
  eligible for first-attempt held-out migration claims.
- `POST_HOC_SEALED_BLIND`: for an already implemented candidate, have a fresh blind-test AI accept
  and externally anchor only its opaque bundle digest before designing PMC/PEA. Freeze tests without
  inspecting candidate contents, then evaluate that immutable digest. This measures sealed-candidate
  behavior without private-test feedback; it is not prospective held-out evidence.
- `DEVELOPER_EVIDENCE`: use when the current actor/context has already inspected or implemented the
  candidate before tests were frozen, or when isolation cannot be established. Do not call it blind.

If no private bundle exists and `POST_HOC_SEALED_BLIND` is eligible, do not stop at `NOT_RUN`:
select `CURATOR`, follow [references/blind-ai-freeze.md](references/blind-ai-freeze.md), and make the
blind-test AI freeze the PMC, PEA, harness, thresholds, generators, commitment, and report schema
before it imports or semantically inspects the candidate. A blind-test AI may later switch to
`EVALUATOR` in a new recorded phase; report curator/evaluator combination explicitly.

## Hard role gate

Select exactly one active role for the current phase before reading or writing that phase's task artifacts:

- `CURATOR`: freeze the driver split, public contract, private assertions, thresholds, generators, and commitment. Do not implement the candidate.
- `EVALUATOR`: accept an already sealed candidate and run the frozen private evaluation. Do not modify or repair candidate code.
- `AUDITOR`: inspect manifests, commitments, access boundaries, logs, and claims read-only. Do not create tests or candidates.

If the current agent, process, workspace, or retained context has participated in candidate
implementation, it cannot claim independent `CURATOR` or `EVALUATOR` status for that experiment. A
fresh AI context that has not seen the candidate may curate `POST_HOC_SEALED_BLIND`, but only after
the candidate digest is anchored without exposing archive contents. If it has seen private
evaluation details, it cannot later act as `MIGRATION_OPERATOR` for that experiment. Record
`NON_INDEPENDENT` and use a genuinely fresh actor/environment or limit the result to
`DEVELOPER_EVIDENCE`.

Do not simulate independence by using two prompts, two subagents with shared context, two branches in one accessible repository, or two containers that mount the same private files.

## Evaluation tracks

Freeze one track per task:

- `LANGUAGE_ONLY`: C to Rust under the same OS, runtime, framework, and device contract.
- `FRAMEWORK_ADAPTATION`: C to Rust while changing the driver framework but retaining the platform or device.
- `CROSS_PLATFORM`: C source platform to a different Rust target platform.

Do not aggregate these tracks into one success rate without separate results. Use “C reference” rather than “Linux reference” unless Linux is actually the source platform.

## Public and private boundary

The migration operator may receive only the public task bundle:

- pinned C source and target SDK/platform;
- device manuals and allowed evidence;
- Public Migration Contract (PMC);
- versioned, device-class control protocol;
- public source/upstream tests and smoke tests;
- budgets, candidate format, and declared capability scope.

Keep Private Evaluation Assertions (PEA), concrete boundary combinations, checker code, fault schedules, mutation survival data, seeds, and expected private results outside the migration workspace and credentials. Requirements are public; their adversarial instances remain private.

Read [references/contracts-and-control-plane.md](references/contracts-and-control-plane.md) before creating a PMC, PEA, oracle, adapter, or harness. Use the templates in `assets/` or an explicitly versioned equivalent.

## Curator workflow

For `CURATOR`, read [references/curator-workflow.md](references/curator-workflow.md) and:

1. Freeze train, development, held-out, and challenge splits by driver family before evaluating candidates.
2. Build evidence-backed PMC obligations before private test implementation.
3. Implement PEA and external oracles independently of candidate code.
4. Validate applicable assertions against the pinned C reference and calibrate defect sensitivity with mutations.
5. Freeze thresholds, retry policy, test generator, and private bundle; create a salted commitment
   at the chronology point required by the selected mode.
6. For `PROSPECTIVE_BLIND`, export only the public task bundle before migration. For
   `POST_HOC_SEALED_BLIND`, verify that opaque candidate-digest acceptance predates private-test
   design and do not expose private material to the producer.

Do not use a held-out result to update the migrator and then keep later tasks in the same batch labeled held-out. Online adaptation is a separate track.

## Candidate boundary

Candidates are normally produced by another actor using an appropriate migration workflow, such as the sibling `open-kernel-driver-port` and `knowledge-guided-driver-port` Skills for kernel migration. The migrator may iteratively write and run public development tests, but it must not run or receive feedback from PEA.

Read [references/candidate-sealing.md](references/candidate-sealing.md) before accepting a submission. Require source, patches, reproducible build inputs, artifact identity, contract capability mapping, provenance, public-test results, and checksums. A submission is immutable once accepted for the official run.

## Evaluator workflow

For `EVALUATOR`, read [references/evaluator-workflow.md](references/evaluator-workflow.md). Verify the
mode-specific chronology, private-bundle commitment, and candidate digest before execution. Run
gates in the frozen order: isolation, reproducible build, mandatory contracts, external
functionality, C/Rust differential behavior, fault injection, mutation adequacy, stress,
performance, and the preselected real-hardware subset.

Run the sealed candidate as an untrusted black box separated from the controller that owns PEA, checker code, seeds and credentials. Return no case-level, coverage, mutation, seed, oracle, log or crash-dump feedback to the migrator during the official run. A build failure, timeout, crash, harness-invalid result, and unsupported capability are distinct outcomes. Never silently repair the candidate or delete a failed task from the denominator.

## Reporting and claims

Read [references/reporting.md](references/reporting.md) before unblinding or aggregating results. Report first-attempt blind success separately from any later feedback-assisted repair. Preserve all tasks, attempts, raw observations, seeds, failures, candidate digests, environment identities, hardware revisions, and inconclusive cases.

QEMU validates the pinned model; it is not real-hardware evidence. Differential agreement can preserve a C bug; it is not a substitute for a device specification. Coverage is a measured reach signal; it is not proof of correctness.

## Completion

Completion depends on the selected role:

- `CURATOR_COMPLETE`: frozen PMC/PEA, validated reference behavior, commitment, public bundle, private bundle, split manifest, and independent handoff exist.
- `EVALUATION_COMPLETE`: the exact sealed candidate has immutable results for every frozen gate and task, including failures and hardware status.
- `AUDIT_COMPLETE`: the auditor records verified and missing independence evidence without changing either bundle.

Always report `evaluation_mode`, `candidate_freeze_relation`, `test_author_candidate_exposure`, and
`producer_private_feedback`. Use `VERIFIED`, `INFERRED`, `PLANNED`, `NOT_RUN`, `NOT_APPLICABLE`,
`BLOCKED`, `FAIL`, `PASS`, `INCONCLUSIVE`, `HARNESS_INVALID`, and `NON_INDEPENDENT` precisely. Never
collapse `POST_HOC_SEALED_BLIND`, developer evidence, or a mixed/non-independent experiment into a
prospective first-attempt blind PASS.
