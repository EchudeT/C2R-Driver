# End-to-End Workflow

Use phases as evidence gates. A later phase may reveal an earlier assumption was wrong; update the records and return to the smallest affected gate.

## 0. Establish the migration envelope

Record:

- user-specified source and target platform names;
- fixed C-to-Rust and QEMU tool choices;
- driver/device family, exact emulated device identity, bus, architecture, and intentionally excluded variants;
- source, target, and QEMU revisions plus toolchain versions;
- source module/test paths and discovered dependency closure;
- expected driver-owned paths plus any pre-existing target paths tentatively requiring minimal integration changes;
- user-provided or bootstrap-generated knowledge-base skill and its status/integrity command;
- target artifact mode: existing runner/cache, official container/SDK/release image, module/component packaging, image injection/repack, CI-derived build, or full source build;
- first executable QEMU route and how the migrated driver will enter the runtime artifact;
- target lifecycle model and measurable completion criteria;
- evaluation mode: `DEVELOPER` or `BLIND_CANDIDATE`; for the latter, experiment/task/attempt IDs, public-bundle and PMC digests, fixed batch migrator and budget, public control-plane version, candidate format, and private-material access declaration.

Inspect Git status before editing. Do not absorb unrelated changes. Freeze an unmodified-target baseline. If existing extension points are insufficient, use `target-changes.md` to prove necessity and select the lowest sufficient target change level before editing.

## 1. Establish evidence, runnable environment, and baselines

Use the knowledge base to locate and verify:

- hardware manuals and register/state-machine rules;
- source driver implementation, framework contracts, and tests;
- target driver APIs, safe/unsafe boundary, coding rules, build/test conventions, and similar in-tree drivers;
- QEMU device-model source, documented options, and observable/injectable behavior.

Do not assume a conventional target source build. Inspect existing project runs, official launch scripts, development containers/SDKs, release artifacts, CI workflows, images, package/component manifests, and repacking or module-loading paths. Select the least expensive reproducible artifact mode that can eventually contain the migrated driver.

Create separate source and unmodified-target baselines in the same pinned QEMU topology where practical. A source baseline proves only source behavior; an unmodified target baseline proves only that the target environment and harness work. Capture exact commands, serial logs, device enumeration, configuration, exit codes, and traffic artifacts.

Environment setup must produce execution. If the intended target route fails, recover through applicable local runner, official container/SDK/image, supported injection/repack, CI-derived command, project-local/containerized QEMU, and direct QEMU device-model/qtest routes. Preserve each distinct attempt and change one causal variable per retry. Do not use “missing build information” as a terminal reason. Continue source and model experiments while repairing target integration.

## 2. Study the target platform from its originals

Apply `target-platform-study.md`. Directly map the downloaded target source and original documentation, choose the closest analogous in-tree implementation, trace registration through cleanup and artifact inclusion, and complete the target-platform profile plus API evidence table.

Run the target knowledge-quality probes defined by the knowledge-base Skill. For every weak or missing result, use direct repository search to locate the original target evidence, expand the controlled target corpus, rebuild the index, and verify retrieval again. Do not proceed to contracts with unresolved target interactions hidden behind generic assumptions.

## 3. Close the source scope

Start from the requested C module and recursively identify required shared cores, headers, macros, generated configuration, callbacks, registration tables, compile flags, and conditional branches. Freeze the actual translation units with hashes and compile commands.

Export structured syntax and semantic facts. Build coverage over functions, callbacks, types, globals, state transitions, I/O effects, IRQ paths, concurrency, resource ownership, lifecycle, and error paths. A generated stub, omitted callback, or body marked pending is uncovered work, not partial success.

## 4. Build migration contracts before coding

For every behaviorally relevant requirement, record:

| Field | Meaning |
| --- | --- |
| ID | Stable contract identifier |
| Requirement | Observable behavior or invariant |
| Hardware evidence | Manual/register/state-machine source |
| Source evidence | C behavior and framework assumptions |
| Target evidence | Target-profile/API-table entry plus original Rust API, coding rule, lifecycle, and safety source |
| QEMU evidence | Model support and observability limits |
| Rust design | State, ownership, synchronization, API mapping |
| Verification | Static check or QEMU stimulus and oracle |
| Status | Evidence and execution status kept separately |

At minimum consider identity/matching, resource discovery, MMIO/PIO/DMA, initialization order, reset, transmit, receive, interrupts, polling, synchronization, memory ordering, buffer ownership, errors/recovery, registration, teardown, observability, and device-specific boundaries.

Reject a contract whose target column contains only an API name, summary, or guessed analogy. Resolve it through target source/documents, mark the target interaction `UNKNOWN`, or create a bounded target-change record.

## 5. Triage and map public source and development tests

Apply the taxonomy in `test-porting.md`. Produce a test-selection matrix before translating tests. Preserve test intent, inputs, oracle, boundaries, negative paths, cleanup, and provenance. Drop source-platform-only tests rather than simulating source internals on the target.

Add only the smallest new tests needed to close driver requirements not covered by retained source tests. Label them as newly designed; never present them as upstream/source tests.

In `BLIND_CANDIDATE`, this matrix and every migrated or newly designed test are public developer evidence. They may guide implementation and repair, but cannot be described as independent or hidden. The separate evaluator owns its private assertions and does not expose them through this workflow.

## 6. Design and implement the Rust module

Implement in dependency order, usually:

1. typed registers, constants, state, and hardware access abstraction;
2. bounded reset and initialization state machine;
3. resource ownership and target registration/probe;
4. transmit and receive paths;
5. IRQ top/bottom halves or the target's evidenced equivalent;
6. lifecycle, error propagation, recovery, counters, and logs;
7. adapted public tests and QEMU harness hooks.

Keep changes small and reviewable. For every increment, update contract and source-coverage mappings. Separate driver implementation from pre-existing target-file modifications in local patches or checkpoints when practical, and never include unrelated paths.

Before introducing a target-facing symbol, verify it exists at the pinned revision and add its definition/call-site evidence to the target API table. Follow the analogous target implementation's framework patterns while preserving the migrated hardware contracts; never translate source-platform framework machinery literally.

## 7. Target compliance review

Before preparing the first runtime artifact, query the knowledge base again for target rules. Audit the driver and every migration-related target change for formatting, naming, API usage, architecture layering, feature/configuration conventions, safe Rust expectations, unsafe justification, lock/IRQ context, ordering, allocation context, error semantics, lifecycle cleanup, compatibility, logging, and documentation.

Repeat this review after repair loops because fixes commonly violate a target rule that the initial design satisfied.

Reopen the original target coding/safety documents and analogous source during this review. If implementation changed an API choice, lifecycle, lock, interrupt context, error path, or artifact wiring, update the target profile and query the knowledge base again before execution.

## 8. Prepare the runtime artifact and run the public QEMU evidence ladder

Use `qemu-evidence.md`. Build only what the selected artifact mode requires. For a prebuilt/container/image workflow, verify the immutable base identity and the exact driver/component/overlay or repacked-image identity instead of forcing a full source build. Start with the narrowest static/artifact check, then prove the current driver is in the runtime path before probe, single-operation data paths, interrupts, retained public functional tests, boundary/error/recovery cases, and repeated regressions. Preserve failed attempts as distinct runs; never overwrite a run directory or reinterpret a stale process as active work.

## 9. Attribute failures and repair narrowly

Classify each failure before editing:

- translation/driver defect;
- migrated-test defect;
- harness or packaging defect;
- source-platform assumption retained by mistake;
- unsupported target API or target-platform defect;
- QEMU model limitation;
- environment/tooling failure;
- inconclusive/insufficient evidence.

Driver/module and migrated-test/harness defects are writable. A target-platform defect or missing extension may be changed only when it is necessary for a confirmed migration contract and passes `target-changes.md`; otherwise record the smallest reproducer and mark the dependent requirement `BLOCKED_TARGET_CHANGE`. For a QEMU limitation, use a supported lower-level observation or injection only if it tests the same contract; otherwise mark the requirement unverified.

For environment/tooling failures, return to the runnable-environment recovery routes rather than stopping. A legitimate `BLOCKED_FULL_INTEGRATION` requires concrete failed commands, explored alternatives, a named external prerequisite, and all independently runnable QEMU/source/model experiments completed.

For target API, compilation, lifecycle, or runtime-integration failures, inspect the actual target definition and closest call sites before editing. Repair the knowledge corpus/profile when the earlier target assumption was wrong; do not patch by trial and error against diagnostics alone.

Rerun the smallest failing gate, then all affected regressions. A workaround used only for diagnosis must be clearly temporary, restored, and never reported as production behavior.

In `BLIND_CANDIDATE`, this repair loop consumes public evidence only. Do not ask the independent evaluator for a failing case, coverage gap, mutation survivor, seed, expected output, or fault location.

## 10. Seal a blind-evaluation candidate when requested

For `BLIND_CANDIDATE`, apply `blind-candidate-mode.md` after the public repair loop. Freeze source, target patches, build inputs, public tests, session provenance, capability map and runtime artifact; calculate the candidate digest and transfer it read-only. Any subsequent change creates a new attempt. Leave private evaluation `NOT_RUN_BY_MIGRATOR`.

For `DEVELOPER`, skip sealing and do not imply that the resulting tests were independent merely because they were written before or separately from some driver functions.

## 11. Final evidence audit

Deliver:

- fixed input/revision manifest;
- source closure and structured-fact coverage;
- migration contract/evidence matrix;
- completed target-platform profile, target API evidence table, analogous-driver trace, and target knowledge-quality results;
- test selection and adaptation matrix;
- evaluation mode; in blind-candidate mode, role-boundary evidence, frozen batch identity, public task and PMC digests, candidate bundle/digest, provenance and evaluator transfer record;
- Rust driver changes plus a separate inventory of every retained pre-existing target-file change, necessity record, safety obligation, validation, and rollback;
- selected artifact mode, reused and newly produced artifact identities, driver-insertion proof, and applicable compile/static-check results;
- immutable QEMU run records with commands, logs, captures, expected/actual observations, and exit codes;
- failure attribution and unresolved items;
- target compliance review;
- scope limits, especially QEMU versus real hardware and unsupported platform lifecycle features.

Do not aggregate a mixed result into a single optimistic PASS. Report each contract and test independently. Public developer PASS is not official hidden-test PASS.
