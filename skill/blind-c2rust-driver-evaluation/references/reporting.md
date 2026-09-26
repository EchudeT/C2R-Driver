# Evidence, Metrics, and Claims

Use intent-to-treat accounting: every task in the frozen held-out manifest stays in the denominator, including acquisition, build, timeout, unsupported, harness-invalid, QEMU-only, and hardware failures.

## Per-task report

Record:

```text
experiment, task, track and attempt IDs
evaluation mode and eligible claim class
public, private and candidate digests
role and isolation status
candidate freeze relation, test-author candidate exposure and producer private-feedback status
state-ledger digest and verified transition order
source C and target Rust artifact identities
declared and unsupported capabilities
gate-by-gate result and earliest terminal failure
contract assertion results
differential equivalence, allowed divergence, mismatch and inconclusive counts
fault accounting and mutation accounting
coverage by functions, branches, states, obligations and changed code
stress, repeated boot and soak results
performance distribution and frozen threshold
QEMU model and real-hardware identities
candidate sandbox and evaluator controller identities
wall time, model/tool cost, human minutes and interventions
raw artifact locations
```

## Aggregate report

Separate tracks and device classes. Report task count, reproducible builds, boots, complete passes, partial capabilities, failure stages, first-attempt success, confidence intervals, and distributions rather than a single optimistic score. Use paired comparisons for methods evaluated on the same tasks and report every seed and repetition policy.

Report coverage and mutation score together. Coverage shows what executed; mutation score shows whether the suite detects representative defects. Neither establishes exhaustive correctness.

## Baselines and ablations

Use the same public task, budget, candidate format, and private evaluator for:

- direct general-model C-to-Rust conversion;
- compilation-feedback agent;
- full workflow;
- workflow without knowledge base, source closure, contract, or public harness feedback;
- expert manual or established translation tools when feasible.

Record expert experience and time. Do not compare an unlimited manual effort with a budget-limited automated run without qualification.

## Claim discipline

Allowed claims name the frozen scope, for example:

> Under the frozen task set, budgets, contracts, fault model, QEMU versions and hardware subset, the workflow produced first-attempt sealed candidates for previously unused driver families, and an independently controlled evaluator measured their behavior without private-test feedback.

Do not claim universal driver correctness, zero model pretraining exposure, all-hardware support, source-bug repair, or real-hardware validation from QEMU alone.

Developer-generated tests and existing migrated drivers may remain valuable train/development case studies. They cannot be retroactively relabeled held-out or hidden after the migrator has seen them.

Report `PROSPECTIVE_BLIND` and `POST_HOC_SEALED_BLIND` separately. A post-hoc sealed result may state
that the blind-test AI committed tests before candidate inspection and that the producer received no
private feedback. It must not be counted as first-attempt held-out migration success. If the same AI
curated and evaluated, report `CURATOR_EVALUATOR_COMBINED`; if credentials were shared, report
`PROCESS_BLINDED` rather than organizational independence.

## Release

When licensing and confidentiality permit, release PMC, PEA, generators, salts, manifests, state ledger, candidate bundles, raw JSONL/CSV, failure logs, environment images or build recipes, hardware records, expected result ranges, and one command per experiment after the entire batch unblinds. If tests remain confidential, give reviewers controlled evaluator access and publish the commitment plus a signed audit statement.
