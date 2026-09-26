# Curator Workflow

The curator produces a frozen benchmark and private evaluator without inspecting or helping
implement the corresponding candidate. If the candidate already exists, use
`POST_HOC_SEALED_BLIND` only through `blind-ai-freeze.md`.

## 1. Freeze claims and tracks

State the research questions, task tracks, supported platforms and device classes, budgets, retries, success gates, performance tolerances, statistical analysis, and the exact claim that the sample can support.

## 2. Split by family

Partition train, development, held-out, and challenge tasks by driver or device family rather than file. Record source revisions and hashes before system development. Public drivers may have appeared in model pretraining; use private, post-cutoff, newly authored, or industrial challenge drivers where possible and report residual contamination risk.

## 3. Freeze the public infrastructure

Before the held-out batch, freeze:

- task schema and public bundle format;
- target SDK/runtime and device identity;
- PMC schema and obligations;
- device-class control plane;
- public smoke/upstream tests;
- candidate format and build contract;
- migrator budget and submission policy.

Validate that the public task is sufficient to implement the declared capability without PEA knowledge.

The generic device-class control schema must be based on train/development evidence, not adapted to a held-out candidate. Freeze it before writing held-out-specific PEA and never change it after exporting any held-out source.

## 4. Build private assertions independently

Derive PEA from device specifications, source closure, historical defects, public contract boundaries, and upstream intent. Do not inspect candidate code. Prefer runtime-computed reference/metamorphic oracles over hand-entered constants.

Cover complementary dimensions:

- lifecycle, identity, resources and failures;
- normal data paths and payload integrity;
- limits, wraparound and malformed input;
- IRQ, polling, progress and concurrency;
- ownership, DMA, memory ordering and cleanup;
- timeout, reset, recovery and repeated cold starts;
- device-class performance and resource use.

## 5. Validate the evaluator

Run applicable PEA against the pinned C reference. Use positive and negative controls to prove delivery and oracle sensitivity. Seed representative translation-error mutants: width, signedness, endian, register order, ring index, ownership, IRQ acknowledgement, DMA sync, barrier, cleanup, boundary, locking, and unsafe-boundary errors.

Do not count uncompilable or equivalent mutants as killed. Preserve surviving mutants and update the suite only before commitment. Freeze reference or synthetic mutant artifacts for later adequacy reproduction; do not require the evaluator to mutate the official candidate.

## 6. Freeze and commit

Freeze all tests, generators, thresholds, invalid-test rules, seed-generation method, repetitions, real-hardware subset, and report schema. Create the salted private-bundle commitment described in `protocol.md`. Export a public task bundle containing no private paths, tokens, test names that disclose cases, or evaluator diagnostics.

For `PROSPECTIVE_BLIND`, create the batch/task/attempt ledger genesis event and record the
commitment before any held-out migration starts. Existing drivers whose implementation predates
this event cannot become prospective held-out tasks.

For `POST_HOC_SEALED_BLIND`, require an externally anchored opaque candidate digest before beginning
private-test design. Record the commitment after the evaluator is validated but before any semantic
candidate import. The AI must build executable private assertions and oracles in this phase; do not
defer test creation to evaluator execution.

## 7. Handoff

Give the migration operator only the public bundle and experiment ID. Record recipient, time, public digest, migrator frozen version, allowed materials, budget, and required candidate format. Network access is not restricted by this protocol. The curator must not enter the migration workspace or answer candidate-specific questions using private information.

In `POST_HOC_SEALED_BLIND`, the producer receives no new private-derived task information and may not
replace the accepted candidate. Handoff is from opaque intake to curator and then from committed
curator artifacts to evaluator. The same AI may perform the latter transition only after recording
the role-phase boundary.

Clarifications may resolve contradictions in the public task. Any material contract change creates a new benchmark version, recomputes the commitment, and applies equally to all candidates.

## Curator completion record

```text
experiment_id
evaluation_mode
track_and_claims
split_manifest_digest
public_bundle_digest
private_bundle_digest_and_commitment
PMC_and_PEA_counts
C_reference_validation
mutation_calibration
thresholds_and_statistics
hardware_subset
role_and_access_record
candidate_digest_acceptance_and_candidate_exposure_record
state_ledger_path_and_digest
handoff_record
status
```
