# Runtime Artifact, QEMU, and Evidence Rules

This ladder is developer-visible evidence. In `BLIND_CANDIDATE` mode, run only public plans, stimuli, oracles and fault hooks here. The independent evaluator runs private assertions after candidate sealing.

## Artifact mode

Do not assume that every target platform publishes a from-source build guide or expects a clean full build. Determine and freeze one evidenced artifact mode:

- verified existing local runner/cache;
- official development container or SDK;
- official prebuilt kernel/image/release artifact with supported module/component insertion;
- initramfs, disk overlay, package, or image-repack workflow;
- command reconstructed from versioned repository CI/release automation;
- documented source build when required.

Record the immutable base identity, every compiled or injected payload, final image/artifact hash, QEMU command, and proof that the current migrated driver—not a stale baseline—will execute. Full source compilation is required only when no lighter supported route can contain the migration.

If no target-integrated route is initially runnable, execute a relevant source baseline and direct QEMU device-model/qtest/QMP smoke while recovering the target route. These are bounded evidence, not substitutes for migrated-driver execution.

## Before execution

Freeze a written plan before each material QEMU run: purpose, contract/test IDs, exact revision and dirty-state fingerprint, command, device identity and topology, image, CPU/memory, network/storage backend, stimulus, oracle, timeout, expected markers, cleanup, and result classification. Allocate a unique run directory. Never overwrite a prior failure.

Use bounded waits and explicit process ownership. A remembered command or stale PID is not evidence that a run is active. Capture actual exit codes and terminate only processes created by the run.

Preflight host ports, sockets, lock files, helper services, and prior QEMU processes before launching. Detect stale build metadata or images using the target's documented clean/rebuild procedure when needed. Record rather than delete unrelated state.

Prove artifact identity before trusting runtime markers: record hashes or build identities for the Rust module, immutable base image/container, injected component or overlay, final target artifact, packaged migrated-test binary, and run configuration; inspect the final image/initramfs/package when possible. A source file change, container launch, or successful build command does not prove that QEMU executed the changed artifact.

Audit the harness and oracle independently. Confirm that success and failure markers are mutually exclusive, counters cover the intended full sequence, payload generators match checker expectations, and outputs are written to the current run directory. Freeze timing thresholds, sample counts, and acceptance rules before the run; post-hoc threshold search is exploratory evidence and requires a separately planned confirmation run.

## Evidence ladder

Advance only when the preceding observation is adequate for the next claim:

1. **Environment smoke:** launch the pinned QEMU/device route and capture a real exit or bounded timeout; a version listing is insufficient.
2. **Static and style:** run target-prescribed checks that apply to the changed Rust and target files.
3. **Artifact preparation:** compile, inject, overlay, repackage, or reuse according to the selected mode; record base and final identities.
4. **Driver-presence proof:** inspect packaging and require a driver-specific build/runtime marker tied to the current source hash.
5. **Enumeration/probe:** verify exact emulated device identity, resource assignment, binding, initialization and readiness.
6. **Single data operation:** externally observe one TX and one RX where applicable; internal return success alone is insufficient.
7. **Interrupt/deferred work:** demonstrate real QEMU interrupt activity, acknowledgement and continued operation; separate this from polling.
8. **Retained migrated tests:** execute the selected device-focused tests with adapted target harnesses.
9. **Boundaries and recovery:** legal limits, malformed input, timeout, repeated operations, reset/retry and post-failure valid traffic.
10. **Regression:** repeat clean/cold starts and representative full-path runs.

Use packet capture, serial logs, target counters, QMP/qtest, or backend observations as independent oracles where suitable. Validate capture contents—identity, direction, lengths, payloads/checksums or protocol fields—not merely file existence or frame count.

## Attribution rules

- Source-platform PASS is reference behavior only.
- QEMU standalone/qtest PASS proves a model precondition only unless the migrated driver was in the path.
- Driver logs prove code-path execution, not necessarily externally correct I/O.
- Upper-layer API, userspace, packaging, or harness failure must not be attributed to the driver without path evidence.
- A temporary guard or fault hook proves only the instrumented configuration. Restore it and rerun production code before claiming recovery.
- Failure to trigger an event is `NOT_OBSERVED` or `QEMU_MODEL_BLOCKED`, not PASS or driver FAIL.
- One successful boot is not stability evidence; use the agreed repeated clean-start criterion.
- A harness control failure invalidates the corresponding driver conclusion. Run positive or negative controls when delivery, packaging, or the oracle is uncertain.

## Result statuses

Keep evidence status separate from execution status:

- `VERIFIED`: supported by inspected source or captured observation.
- `INFERRED`: reasoned from evidence but not directly observed.
- `PLANNED`: frozen before execution.
- `NOT_RUN`: no qualifying execution occurred.
- `NOT_APPLICABLE`: operation does not exist in the target contract.
- `BLOCKED`: named prerequisite is absent.
- `FAIL`: qualifying execution contradicted its oracle.
- `PASS`: qualifying execution met every stated oracle.

Compilation PASS does not propagate to probe or runtime statuses. A partially satisfied test remains partial or FAIL according to its frozen oracle; do not select only favorable markers after the run.

An environment report with no executed QEMU/source/model command is `NOT_RUN`, not a completed experiment. A device-model-only run may be `PASS` for its frozen model oracle while target integration remains `BLOCKED_FULL_INTEGRATION`.

## Run artifact minimum

Preserve environment/revision manifest, recovery attempts, selected artifact mode, base/final artifact identities, driver-insertion proof, exact command, stdout/stderr/serial logs, QEMU configuration, relevant captures, helper/checker output, exit codes, timing, expected-versus-actual table, status, attribution, and links to contracts/tests. Record excluded setup attempts and why they are not valid samples.

After any code or harness change, create a new run ID. Rerun the smallest affected test and then the dependent regression set. Report QEMU coverage as model-specific and leave real-hardware claims unverified unless separately tested.

For `BLIND_CANDIDATE`, complete the public ladder before sealing. After the seal, do not change code or harness in response to an official private result; a repaired version is a separate attempt and the first-attempt blind result remains immutable.
