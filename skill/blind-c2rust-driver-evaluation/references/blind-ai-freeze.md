# Blind-Test AI Freeze Workflow

Use this workflow when a migrated candidate already exists but a fresh blind-test AI has not seen
its implementation, logs, test outcomes, or migration conversation. The outcome is
`POST_HOC_SEALED_BLIND`, not prospective held-out migration evidence.

## Eligibility gate

Before opening any candidate archive or source tree, record the AI/session identity, retained-context
scope, workspace mounts, credentials, and every candidate-related input already seen. Eligible input
is limited to:

- pinned source C, device specification, target-platform public API and manuals;
- generic public task schema, capability scope, budgets and candidate format;
- an opaque candidate ID, archive byte length and digest produced by a non-semantic intake step.

Candidate source, patches, manifests containing implementation details, public-test results, logs,
crashes and prior evaluation reports are forbidden until the private bundle is committed. If the
context has seen any of them, stop curation and use a fresh context or `DEVELOPER_EVIDENCE`.

## Phase A — opaque candidate acceptance

1. Have an intake registrar hash the candidate archive without listing or extracting it.
2. Record the archive path or object ID, byte length and SHA-256 only.
3. Make the accepted object read-only and prevent the producer from replacing it.
4. Anchor the digest and acceptance event in the experiment ledger and an external append-only or
   trusted timestamp service.
5. Record state `POST_HOC_CANDIDATE_ACCEPTED`.

The blind-test AI may perform the hash command if the tool output reveals only digest, size and
administrative errors. It must not inspect filenames, manifests, symbols or archive members.

## Phase B — AI curation and freeze

Activate role `CURATOR`. In evaluator-private storage, derive the PMC and PEA only from eligible
public inputs. The AI must create actual executable assertions and external oracles, not only a test
plan or a report saying that tests are missing.

1. Freeze the track, declared capability scope and claim boundary.
2. Author stable PMC obligation IDs and the public device-class control protocol.
3. Implement PEA covering normal, boundary, negative, concurrency, recovery and resource behavior.
4. Implement C-reference differential and device-specification oracles where applicable.
5. Add fault schedules, frozen reference/synthetic mutants, stress repetitions, performance
   thresholds and the hardware subset, explicitly marking justified `NOT_APPLICABLE` items.
6. Validate the harness against the pinned C reference and positive/negative controls; correct only
   before commitment.
7. Freeze seeds or the seed source, timeouts, retries, invalid-test rules, thresholds and report
   schema.
8. Create the canonical private archive, fresh salt, bundle digest and salted commitment. Anchor the
   commitment externally before candidate import.
9. Record `CONTRACT_FROZEN` and `PRIVATE_BUNDLE_COMMITTED` with the accepted candidate digest.

Do not copy PEA, checkers, seeds, salts, thresholds or private paths into the candidate or migration
workspace. Export the PMC/control protocol only if the post-hoc claim permits publication; do not
allow producer changes under the same accepted digest.

## Phase C — role transition and evaluation

After commitment, close the curator phase and record its terminal artifact digests. The same
blind-test AI may begin a new `EVALUATOR` phase because it still has not seen the candidate; record
`CURATOR_EVALUATOR_COMBINED`. This permits a producer/tester-separation claim, not organizational
separation between curator and evaluator.

Now extract/import the exact accepted candidate, validate its manifest and checksums, and run the
frozen gates. Candidate import failure, build failure, timeout and unsupported capability remain in
the denominator. Never modify the candidate or use results to select a replacement under the same
attempt.

## Required chronology

```text
candidate implementation (pre-existing; provenance reported)
  < opaque candidate digest accepted and externally anchored
  < blind-test AI begins PMC/PEA design
  < private bundle committed
  < evaluator semantically imports candidate
  < private execution
  < unblinding
```

If only a local digest or same-credential procedural boundary is available, report
`PROCESS_BLINDED` and the missing stronger evidence. Do not claim organizational independence. If
the candidate digest was not fixed before curation, the result is `DEVELOPER_EVIDENCE`.

## Claim boundary

Allowed:

> A fresh blind-test AI froze the private evaluator before inspecting an already sealed candidate,
> and the producer received no private-test feedback before terminal evaluation.

Forbidden:

- first-attempt held-out migration success;
- tests committed before migration;
- curator/evaluator organizational separation when one AI performed both phases;
- real-hardware correctness from emulation alone.
