# Checks inside one behavior

Use driver_checks.check to execute a worktree script and wait for its result without polling.
level=development runs the selected format/build script; it does not establish runtime behavior.
level=runtime runs registered cases or a selected script against .dpf-output/runtime-artifact.
The tool returns receipt, stdout, stderr and observation paths. Inspect the relevant failure
and actual assertion. Never edit/build concurrently with a check. Full logs remain on disk.

For the managed platform use driver_checks.platform directly: action=format with affected packages
and write=true, action=build, then action=run_case with the worktree JSON case path. Await each tool
result; do not launch these operations through exec_command or poll write_stdin. Its executor owns
waiting, cancellation and logs; the human monitor shows progress without model calls. The platform
build resolves dependencies offline before freezing inputs; do not hand-edit lockfile hashes or
rebuild merely to accept a generated lockfile. tool_runtime.platform_execution.case_interface
specifies the JSON format. Put cases/helpers under .dpf-output/harness; put observations under
.dpf-output/qemu-runs, outside rootfs/ISO inputs. Reuse the verified container/toolchain/firmware
route; do not recreate transports or switch to a host command. After a source change rebuild the
artifact before runtime checks. A report-only edit is not a reason to rerun a passing check.

Before the first runtime check, implement the current behavior's required initial state, valid
transition and applicable rejection/cleanup assertions together. Do one local source self-check
of actual linkage/initialization, resource lifetime and lock scope before format/build/run; no
separate reviewer, report or self-check tool is required. Do not start with a generic success
substring if the frozen obligation already requires a value or state transition. Real failures
still require repair and affected checks; this ordering is not permission to ignore later defects.
For shell checks use case steps {"guest_assert": "<command exiting 0 only when the assertion holds>"}
after shell readiness. The executor checks a fresh echo-resistant exit marker. Do not construct
echo SUCCESS plus wait_serial SUCCESS: terminal command echo can falsely satisfy that check.
If adding a component exposes a wiring gap, use platform action=integration with an existing
relevant package for bounded, version-bound source examples. It is optional, not a query quota.
Workspace/Cargo registration alone is insufficient: retain a Rust reference from a linked crate
and verify the initializer/device path. Do not copy another device's protocol or expand scope.

Check the current behavior's necessary real device path, including relevant failure/cleanup.
Driver registration, QEMU enumeration and a booted shell are not proof of MMIO or event delivery.
A framework rejection before driver invocation does not verify the driver's error path. Inspect
stderr/serial/QMP before attributing a timeout. Probe only an unresolved fact that changes this
behavior; preserve actual failures and unresolved observations. No full public suite or final
packaging report is required for a progress handoff. Later final delivery still requires all
frozen obligations, actual controller-captured execution and the final self-check.

If managed execution finds a concrete API/framework mismatch, repair it inside this behavior.
Use progress continue for unfinished work. Stop with the specific blocker if the configured
route or frozen obligations cannot be satisfied; do not invent PASS, weaken an assertion or
start another survey. Optional diagnostics are not acceptance. Match reusable receipts to
current source, artifact, harness and environment identities through the controller.
