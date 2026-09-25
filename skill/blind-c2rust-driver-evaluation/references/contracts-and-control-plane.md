# Contracts, Oracles, and the External Control Plane

The public contract tells a migrator what must be implemented. Private assertions choose concrete values, schedules, combinations, and checks. Hiding requirements would make the task underspecified; hiding adversarial instances prevents direct overfitting.

## Public Migration Contract

Assign every obligation a stable ID and record:

```text
obligation_id
capability_and_scope
preconditions
stimulus_or_operation
allowed_outcomes
postconditions
ordering_and_progress
resource_and_safety_invariants
hardware_or_protocol_evidence
source_C_evidence
target_platform_evidence
allowed_platform_projection_or_divergence
criticality
```

Use semantic obligations, not source/target function-name equality. A source NAPI loop may map to a target worker or softirq if progress, batching, ownership, and concurrency obligations hold. A source-only module unload operation may be `NOT_APPLICABLE` only with target lifecycle evidence.

Each source obligation must map to exactly one declared status: `TRANSLATED`, `REPLACED_EQUIVALENT`, `DELEGATED`, `NOT_APPLICABLE`, `OUT_OF_SCOPE`, `UNSUPPORTED`, or `UNKNOWN`. An independent reviewer approves all exclusions and platform substitutions.

## Private Evaluation Assertions

For every private assertion record, outside the public bundle:

```text
assertion_id
public_obligation_ids
generator_version
private_parameters_or_seed_policy
setup_and_cleanup
stimulus_and_schedule
oracle_and_normalization
timeout_and_repetitions
expected_C_reference_result
fault_or_mutation_class
QEMU_or_hardware_requirements
invalid-test_conditions
```

Publicize fault classes and supported ranges; keep concrete cross-products, schedules, seeds, mutation survival results, and checker implementation private until unblinding.

## Source-test handling

Upstream/source tests are public evidence, not official hidden evidence. Keep originals immutable, classify driver intent versus source-platform semantics, and publish retained or adapted public tests. The independent team may use the same upstream intent to create PEA, but it must implement its oracle without reading candidate code or migration-generated expected values.

## Control plane outside the driver

Do not add a hidden test ABI to candidate drivers. Keep the data path native to the target platform and expose only a versioned, device-class control agent that can configure the test environment, start a public workload, read declared public counters, and shut down.

Example messages:

```json
{"version":1,"operation":"capabilities"}
{"version":1,"operation":"configure","parameters":{}}
{"version":1,"operation":"start_workload","kind":"class-defined"}
{"version":1,"operation":"read_public_counters"}
{"version":1,"operation":"stop_workload"}
```

The control agent contains no private inputs, expected values, seeds, pass/fail logic, or candidate-specific branch. Design and freeze its device-class schema using train/development evidence before authoring held-out-specific assertions, and never change it after a held-out source is exported to the migrator.

At evaluation time the candidate-side control agent is a black-box endpoint in the isolated candidate VM or hardware host. Private generators and oracles remain on the external controller. Do not mount or copy their code, data, seeds, paths or credentials into the candidate boundary.

Inject and observe the real data path externally:

- network: TAP, packet socket, peer host, pcap, hardware traffic generator;
- block: guest I/O plus host-side image or media validation;
- serial/character: host PTY or external line peer;
- I2C/SPI: device model, bus proxy, or external controller;
- userspace/VFIO: stable public application API plus device-side observation;
- firmware/virtual backend: protocol peer, bus model, or recorded transaction stream.

## Differential observation

Run the same generated workload against the pinned C reference and sealed Rust candidate at a common observation boundary. Compare external data, device transactions, lifecycle outcomes, resource/safety invariants, and declared errors. Do not compare source and target framework calls literally.

Normalize only predeclared nondeterminism:

- alpha-rename DMA addresses and allocation identities;
- compare allowed partial orders rather than one total interrupt order;
- mask reserved or undefined bits;
- admit declared batching differences;
- apply frozen timing intervals, never post-hoc thresholds.

Classify `EQUIVALENT`, `ALLOWED_DIVERGENCE`, `SEMANTIC_MISMATCH`, or `INCONCLUSIVE`. Device specifications, C reference behavior, and target safety/lifecycle rules form a triangular oracle. When they conflict, preserve the conflict for independent adjudication rather than choosing whichever favors the candidate.
