# Evaluator Workflow

The evaluator receives a frozen private bundle and an immutable candidate. It executes and reports; it does not develop the driver.

## Preflight

Verify:

- the declared mode is frozen; batch state is `BATCH_RUNNING` for `PROSPECTIVE_BLIND` or
  `POST_HOC_RUNNING` for `POST_HOC_SEALED_BLIND`;
- the selected candidate digest is sealed or accepted and immutable under that mode;
- actor and workspace did not participate in candidate implementation;
- public task, private bundle, commitment, salt policy, and candidate digests match;
- thresholds, seeds or seed source, repetitions, timeouts, retry rules, hardware subset, and invalid-test conditions were frozen;
- private paths and credentials were never mounted in migration logs;
- candidate import has no writable path back to the producer;
- the externally anchored hash-chained ledger proves the selected mode's required chronology;
- for `POST_HOC_SEALED_BLIND`, opaque candidate acceptance predates private-test design and
  commitment predates semantic candidate import;
- candidate runs in a separate VM, physical host, or equivalent strong boundary rather than an ordinary process/container sharing the controller kernel;
- candidate has no private mounts, credentials, environment, command-line secrets, or shared crash/log path; Internet, network, and IPC access are not G0 isolation conditions;
- the hardware operator, when able to see private workloads, captures or results, did not participate in candidate production.

Failure of independence is `NON_INDEPENDENT`; it cannot be repaired by continuing to call the same result blind.

Same-credential procedural isolation may be reported as `PROCESS_BLINDED` only in
`POST_HOC_SEALED_BLIND`; it does not establish organizational independence. Candidate exposure by
the curator before commitment is `NON_INDEPENDENT` and forces `DEVELOPER_EVIDENCE`.

## Evaluation gates

Run every frozen task through the applicable gates in order:

1. `G0_ISOLATION`: manifests, commitments, access and candidate integrity.
2. `G1_BUILD`: clean reproducible build, packaging, boot and driver-presence proof.
3. `G2_CONTRACT`: mandatory static and dynamic PMC obligations.
4. `G3_FUNCTIONAL`: externally observed normal, boundary and negative behavior.
5. `G4_DIFFERENTIAL`: identical generated workloads at C/Rust common boundaries.
6. `G5_ROBUSTNESS`: device/environment fault injection and recovery outcomes.
7. `G6_TEST_ADEQUACY`: reproduce the suite's score on pre-frozen reference or synthetic mutants without changing the official candidate.
8. `G7_STRESS`: concurrency, repeated cold starts, soak and resource cleanup.
9. `G8_PERFORMANCE`: frozen throughput, latency, CPU/memory and regression limits.
10. `G9_HARDWARE`: preselected real-device subset using the exact sealed artifact.

Critical safety failures such as out-of-bounds DMA, silent data corruption, invalid ownership transfer, or undeclared unsafe memory behavior cannot be averaged away by performance or other tests.

## Fault accounting

Report attempts, injected faults, manifested faults, detected faults, recovered outcomes, safe failures, crashes, hangs, silent corruption, and non-manifesting injections separately. Do not report recovery rate with an ambiguous denominator.

Fault injection changes device or environment behavior. Mutation testing evaluates the suite against separate, pre-frozen mutated artifacts. Keep their results separate and exclude equivalent or uncompilable mutants from the executable-mutant denominator. Never rewrite the sealed official candidate for G6. If candidate-derived mutants are scientifically required, create read-only `EVAL_DERIVATIVE` artifacts linked to the parent digest; they are neither new submissions nor replacements for the official candidate, and their outcomes affect suite-adequacy reporting only.

## Differential execution

Use identical generated inputs and schedules where both platforms support them. Compare the PMC projection, not internal API traces. Record source failures, allowed divergences, semantic mismatches and inconclusive observations. If the C reference violates a normative property, retain the conflict and use the predeclared adjudication rule.

## Real hardware

Run only the sealed artifact on recorded vendor/device/revision, firmware, CPU, IOMMU, topology and peer equipment. Reset or power-cycle as prescribed. Drive and observe I/O from another machine or external controller. Archive packet captures, media hashes, bus traces, counters, serial logs, temperatures or other relevant raw evidence. An operator who can see private workloads, captures or results must be independent of candidate production and listed in G0 role-conflict checks.

QEMU PASS and hardware `NOT_RUN` must remain distinct. A hardware failure remains in the result even when the emulated device passes.

## Feedback rule

During the official attempt return no detailed status to the migration operator. The evaluator may expose only administrative state such as accepted, queued, running, or completed. Candidate logs, serial output, traces, coverage, crashes and dumps remain on the controller side and are embargoed. Unblind only after all tasks, attempts and gates in the frozen batch have completed or reached frozen termination conditions.

If a later repair study is desired, first finalize the blind result. Then disclose a predeclared feedback level and record the repaired candidate as a separate `POST_FEEDBACK` experiment.
