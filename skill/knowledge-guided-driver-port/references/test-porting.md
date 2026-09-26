# Source-Test Selection and Porting

The goal is to retain tests that validate the device driver's behavior, not the source platform's implementation choices.

These rules govern source-derived and migration-authored **public developer tests**. They do not make a test independent merely because its intent was recorded before implementation. In an official blind experiment, an actor outside the migration workspace owns the private assertions and evaluator.

## Classify every discovered source test

Use exactly one primary class and record evidence for the decision:

| Class | Action | Meaning |
| --- | --- | --- |
| `DEVICE_FUNCTIONAL` | Retain | Stimulus and oracle concern externally visible device/driver behavior. |
| `DEVICE_PROTOCOL_INTERNAL` | Retain/adapt | Verifies register sequences, ring/descriptor rules, state transitions, bounds, timeout or recovery. |
| `PORTABLE_INTENT_PLATFORM_HARNESS` | Adapt | Driver-relevant intent is valid, but setup/observation uses source-platform APIs. |
| `SOURCE_PLATFORM_SEMANTICS` | Exclude | Oracle specifically tests source kernel internals, ABI, module loader, scheduler, namespace, sysfs/procfs, tracing, or framework bookkeeping with no independent driver contract. |
| `OUT_OF_SCOPE_DEVICE_VARIANT` | Exclude | Targets unsupported hardware, bus, architecture, mode, or optional feature. |
| `TARGET_CAPABILITY_BLOCKED` | Preserve intent, do not count | Valid driver test cannot run because the target lacks a required non-driver capability. |
| `QEMU_MODEL_BLOCKED` | Preserve intent, do not count | Required hardware behavior is absent or not controllable/observable in QEMU. |

A test can contain mixed assertions. Split it when useful: retain driver assertions and exclude source-platform assertions. Do not exclude a test merely because it uses a source API; first determine whether that API is only a replaceable harness.

## Decision questions

For each assertion ask:

1. What driver or hardware requirement does this assert?
2. Would the requirement still exist on another kernel?
3. Is the stimulus delivered to the device or only to source-framework bookkeeping?
4. Can the target expose an equivalent observation using documented APIs, serial logs, counters, packet capture, QMP/qtest, or external traffic?
5. Does QEMU model the required behavior?
6. Does adaptation preserve timing, ordering, boundary values, negative paths, cleanup, and failure semantics?

If question 1 has no answer independent of the source platform, classify it `SOURCE_PLATFORM_SEMANTICS`.

## Test mapping record

Each retained/adapted test records:

```text
source_test and provenance
original command/framework
class and rationale
driver_contract_ids
setup
stimulus
oracle/assertions
cleanup
source-only assertions removed
target adaptation and target evidence
QEMU prerequisites/limitations
expected result
actual result and run ID
status
```

Keep original source tests immutable. Migrated tests must live separately and identify copied/adapted portions and licensing provenance.

## Common portable intents

Typical candidates include exact device matching, probe/resource failure, initialization/reset, identifier or MAC readout, single and repeated TX/RX, payload integrity, legal length boundaries, ring/descriptor wrap, interrupt-driven completion, masking/re-enable, timeout, malformed input containment, reset/retry, teardown and reinitialization, and negative controls proving traffic uses the intended device.

These are candidates, not mandatory universal tests. Derive the actual matrix from the device contracts and source tests.

## Lifecycle tests

Build is always tested. Load/unload is tested only when the target has evidenced dynamic module support. Otherwise translate lifecycle intent to the target's real mechanism, such as component registration, probe/start, stop/detach, reset, image reboot, or repeated cold boot. Mark non-existent operations `NOT_APPLICABLE`; never fake source module semantics.

## New tests

Create new tests only for uncovered migration contracts or to replace an unusable harness while preserving the same oracle. Label origin as `NEW_MIGRATION_TEST`, distinguish it from source/upstream tests, and explain why existing tests were insufficient.

The migrator may use `NEW_MIGRATION_TEST` in its incremental repair loop. Never relabel it as hidden, held-out, or independently authored. Private-test inputs, schedules, checkers, seeds and results must not enter this matrix or the migration workspace before candidate sealing.
