# Checks inside one behavior

Development checks retain earlier passing observations; do not rerun them merely to renew records,
session IDs, temporary paths or reports. Historical results are not final acceptance.
After delivery, the controller runs the complete required public suite once on one final source
snapshot and built artifact, without reusing individual development case results. For pvpanic this
means all three prepared cases in one batch. If all pass, finish without another model review or
test run. Resuming the already-passing final batch reuses it; temporary PATH/log/report differences
are not candidate versions and never request another run. Fix actual failed cases, not metadata.

If prepared_public_tests is present, the controller already supplies the public stimuli,
assertions, cases and suite scripts. Read its INTERFACE.md once, adapt the interface to the real
driver, then implement and run the listed cases. Do not reauthor/re-register those cases or
weaken their assertions. Their IDs describe scenarios, not implementation work packages.
The case-registration instructions below apply only when NO prepared suite is supplied.
With a prepared suite, its listed IDs are the entire mandatory runtime check set. Analysis prose
cannot expand that set: treat proposed extra ktests/mocks/fault-injection as validation limits,
not mandatory work. Preserve source obligations with bounded source reasoning; repair concrete
violations and resolve necessary correctness premises. Public PASS alone is not full semantic proof.

Use driver_checks.check to execute a worktree script and wait for its result without polling.
level=development runs the selected format/build script; it does not establish runtime behavior.
level=runtime runs registered cases or a selected script against .dpf-output/runtime-artifact.
The tool returns receipt, stdout, stderr and observation paths. Inspect the relevant failure
and actual assertion. Never edit/build concurrently with a check. Full logs remain on disk.

For the managed platform use driver_checks.platform directly: action=format with affected packages
and write=true, action=build, then action=run_case with an inline case object or worktree JSON case path. Await each tool
result; do not launch these operations through exec_command or poll write_stdin. Its executor owns
waiting, cancellation and logs; the human monitor shows progress without model calls. The platform
build resolves dependencies offline before freezing inputs; do not hand-edit lockfile hashes or
rebuild merely to accept a generated lockfile. tool_runtime.platform_execution.case_interface
specifies the JSON format. Put cases/helpers under .dpf-output/harness; put observations under
.dpf-output/qemu-runs, outside rootfs/ISO inputs. Reuse the verified container/toolchain/firmware
route; do not recreate transports or switch to a host command. After a source change rebuild the
artifact before runtime checks. A report-only edit is not a reason to rerun a passing check.

For cases required by the delivery, use platform action=register_case with a short id, case
(object or existing JSON path), and contracts=[applicable C IDs]. The controller saves the case,
its wrapper and experiments.json entry. It preinstalls implementation-smoke.sh and public-qemu.sh;
do not write these wrappers. Then driver_checks.check cases=[id] executes the selected current
checks and records reusable receipts. Omit cases to run the full registered suite. Register only
required cases; exploratory platform run_case calls do not automatically join final acceptance.
Updating the same id replaces its case content; preserve frozen assertions, never weaken them to
pass. Both final entrypoints consume the complete registered suite. Empty/missing suites fail.
Registration and contract links are not proof that all required obligations are tested.

Before the first runtime check, implement the current behavior's required initial state, valid
transition and applicable rejection/cleanup assertions together. Do one local source self-check
of actual linkage/initialization, resource lifetime and lock scope before format/build/run; no
separate reviewer, report or self-check tool is required. Do not start with a generic success
substring if the frozen obligation already requires a value or state transition. Real failures
still require repair and affected checks; this ordering is not permission to ignore later defects.
For kernel startup messages use {"assert_boot_log":"literal message"}; the controller handles
color codes and excludes sent commands and their output. Do not assume dmesg exists or buffers
kernel logs. Put compatible assertions in the same boot case. On failure, read the returned guest
output first; do not change driver code to fix an unavailable logging interface.
If only a positive boot-log assertion was written incorrectly, platform action=check_boot_log,
capture=<returned T ID>, contains=["correct literal"] can inspect that saved run without reboot.
It proves only those recorded log facts for the same candidate/environment and original devices;
it does not turn the original failed case into PASS or execute shell/QMP actions. Missing facts
need a real current run. Code changes require rebuild and affected checks, as before.
For shell checks use case steps {"guest_assert": "<command exiting 0 only when the assertion holds>"}
after shell readiness. The executor checks a fresh echo-resistant exit marker. Do not construct
echo SUCCESS plus wait_serial SUCCESS: terminal command echo can falsely satisfy that check.
For QMP return fields use {"qmp_assert":{"execute":"query-status","match":{"running":true}}}.
For event fields use {"expect_event":{"event":"<required event>","data":{"<field>":"<value>"}}}.
Objects match the declared subset; lists/scalars match exactly, including types. Each event can
be consumed once by wait_event or expect_event; it may have arrived earlier in the same boot.
Order the real stimulus and expectations accordingly. Plain qmp executes a command without
checking its return fields. Supply the actual frozen oracle; a transport success is not that oracle.
Inline cases are saved by the controller at the returned case path for reuse in final harnesses.

If adding a component exposes a wiring gap, use platform action=integration with an existing
relevant package for bounded, version-bound source examples. It is optional, not a query quota.
For a new component, optional action=scaffold with package, template=<existing component>,
and dependencies=[<needed workspace names>] writes registration and a linked owner reference.
owner_source defaults to kernel/core/src/init.rs; choose another existing owner when appropriate.
It returns the exact diff and leaves a TODO initializer: implement it before building/running.
The scaffold supplies no device protocol, initialization order proof or successful test.
Workspace/Cargo registration alone is insufficient: retain a Rust reference from a linked crate
and verify the initializer/device path. Do not copy another device's protocol or expand scope.

Check the current behavior's necessary real device path, including relevant failure/cleanup.
Driver registration, QEMU enumeration and a booted shell are not proof of MMIO or event delivery.
A framework rejection before driver invocation does not verify the driver's error path. Inspect
stderr/serial/QMP before attributing a timeout. Probe only an unresolved fact that changes this
behavior; preserve actual failures and unresolved observations. Complete the current behavior's distinguishing checks BEFORE progress done; do not defer its
first correctness check to final acceptance. Final acceptance is integrated regression, not a
substitute for this check. No full public suite or final packaging report is required per handoff. Later final delivery still requires all
frozen obligations, actual controller-captured execution and the final self-check.

If managed execution finds a concrete API/framework mismatch, repair it inside this behavior.
Use progress continue for unfinished work. Stop with the specific blocker if the configured
route or frozen obligations cannot be satisfied; do not invent PASS, weaken an assertion or
start another survey. Optional diagnostics are not acceptance. Match reusable receipts to
current source, artifact, harness and environment identities through the controller.

When configured, `driver_checks.knowledge_learn` can save an already-discovered reusable lesson:
lesson, conditions, sources (repository:path:start-end, one per line). Optional; no extra
research, report, hashes or completion requirement. Publication is controller-owned after acceptance.
