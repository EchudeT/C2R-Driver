# Candidate Submission and Sealing

The candidate boundary lets the migration operator use public tests during development while preventing official private results from becoming an iterative oracle.

## Required candidate bundle

Require a content-addressed bundle containing:

```text
candidate source and driver-owned tests
all target-platform patches
source and target revision identities
dependency locks and reproducible build command
compiler, model, tool and environment identities
public contract capability map
public test plans, results and run IDs
artifact mode and driver-insertion proof
final source, binary/image and bundle digests
session provenance and human interventions
known unsupported or inferred items
```

The capability map must link every PMC obligation to target source spans or `UNSUPPORTED`, `NOT_APPLICABLE`, `OUT_OF_SCOPE`, or `UNKNOWN` with evidence. It is not a private-test prediction form.

## Migration-side rules

The migration operator may:

- derive task-local knowledge from allowed C source, target source, manuals, and public tests;
- design and iteratively run public developer tests;
- repair the candidate from compiler, public harness, source baseline, QEMU model, and target runtime evidence;
- package public control-plane support that was fixed before the split.

The migration operator must not:

- read, request, infer through feedback, or execute PEA;
- receive private coverage, mutation, case-level failure, seed, or threshold-search results;
- update the frozen global migrator between held-out tasks;
- choose among candidates based on official private results.

## Seal operation

1. Stop candidate mutation and public repair at the frozen budget or declared completion condition.
2. Run the predeclared public checks and retain failures.
3. Build in a clean public environment and prove the current driver enters the runtime artifact.
4. Create the candidate manifest and hashes over source, patches, build inputs, final artifacts, public results, and provenance.
5. Sign the bundle digest for actor identity, obtain an external WORM/transparency-log or trusted-timestamp receipt for chronology, and transfer it read-only to the evaluator.
6. Append a hash-chained ledger event containing the prior event digest, migration start, candidate digest, actor and seal time, then obtain an external WORM/transparency-log or trusted-timestamp receipt before private execution.
7. Record state `CANDIDATE_SEALED` and the first-attempt identifier.

After sealing, any code, dependency, flag, test-agent, or image change creates a new candidate digest and attempt. Never replace the first blind attempt. If the protocol permits a fixed number of attempts, predeclare the selection rule without private feedback and report all attempts.

## Evaluator import

For `POST_HOC_SEALED_BLIND`, perform opaque acceptance before curation: hash the archive without
listing, extracting, symbol-scanning or reading its manifest; make the accepted object immutable;
and externally anchor its digest. Semantic validation is intentionally delayed until after the
private bundle commitment. A malformed or incomplete accepted bundle remains in the denominator.

The evaluator verifies the bundle digest, expected public task digest, source/target revisions, dependency identities, candidate manifest, externally anchored ledger chronology, and absence of undeclared external fetches. Self-signed or locally generated timestamps alone do not establish chronology. A digest mismatch or invalid chronology is an isolation/submission failure, not a driver test failure.

The evaluator may rebuild the bundle but must not patch it. If the public build contract is invalid, report `HARNESS_INVALID` or benchmark infrastructure failure with evidence; do not silently make the candidate build.
