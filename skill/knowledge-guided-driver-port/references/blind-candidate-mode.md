# Blind-Evaluation Candidate Mode

This mode produces a sealed migration candidate for a separate evaluator. It does not create, discover, run, or repair from official hidden tests.

## Intake gate

Require and freeze:

```text
experiment, task and attempt IDs
evaluation track
public task bundle and digest
public private-bundle commitment and timestamp receipt
append-only state ledger path and previous event digest
Public Migration Contract (PMC) and digest
public source/upstream/smoke tests
public device-class control-plane version
candidate bundle format
frozen migrator, prompt, rules and budget
held-out batch identity and no-cross-task-update policy
private evaluator material access: false
```

If private assertions, checker code, seeds, evaluator diagnostics, mutation survival data, or credentials are visible in the workspace or retained context, stop blind-candidate processing and report `NON_INDEPENDENT`. Do not hide the exposure by copying only selected files to another folder.

## Public development loop

Follow the normal evidence, target study, source closure, contract, implementation, QEMU, attribution, and repair gates using only public evidence. It is valid to create and iteratively run migration tests for uncovered PMC obligations. Label all such tests `NEW_MIGRATION_TEST`; they are developer tests, never hidden evidence.

The migration contract may state public properties and expected behavior. It must not incorporate candidate-specific hints derived from private results. The public control agent may configure workloads and expose declared counters, but it contains no private inputs, expected values, seeds, pass/fail logic, or candidate-specific branch.

## Held-out batch rule

The migration workflow is frozen before the first held-out task. Start each task from a fresh environment without model conversation, task-local knowledge, patches, or repair rules from prior held-out tasks. A task may build a local knowledge base from its allowed source and documentation; archive it with provenance, then discard it rather than updating global Skills or later tasks.

## Seal the candidate

At the frozen budget or completion condition:

1. stop code and public-harness modification;
2. run and retain all predeclared public checks, including failures;
3. rebuild in a clean public environment and prove driver insertion;
4. produce the PMC capability map, marking unsupported and inferred items;
5. package candidate source, target patches, build inputs, dependency identities, artifact, public run records, session provenance and human interventions;
6. hash and timestamp the bundle and transfer it read-only to the evaluator;
7. append a `CANDIDATE_SEALED` event that links the prior ledger digest, public task, migration start and candidate digest, then obtain an external WORM/transparency-log or trusted-timestamp receipt; an actor self-signature alone is insufficient chronology evidence;
8. record private evaluation `NOT_RUN_BY_MIGRATOR`.

Any later code, dependency, flag, control-agent, packaging, or image modification creates a new attempt and digest. Never replace the first blind attempt. Do not request case-level, coverage, mutation, fault, seed, or oracle feedback while the official evaluation is running.

## Candidate record

```text
experiment_task_attempt_ids
public_bundle_and_PMC_digests
frozen_migrator_identity_and_budget
source_target_toolchain_dependency_identities
candidate_source_and_target_patch_digests
build_command_artifact_mode_and_insertion_proof
runtime_artifact_digest
public_test_plan_results_and_run_ids
PMC_capability_map_and_unresolved_items
session_provenance_and_human_interventions
sealed_timestamp_signer_and_bundle_digest
state_ledger_event_and_digest
evaluator_transfer_record
private_evaluation: NOT_RUN_BY_MIGRATOR
```
