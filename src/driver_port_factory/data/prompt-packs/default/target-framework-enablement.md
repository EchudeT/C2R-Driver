# Target-framework enablement

Target platform changes must be justified by the frozen migration contracts
and target study. This stage is the entry point for one coherent delivery task. Complete the driver, shared
integration, test harness and artifact preparation in this same task; the
controller retains separate evidence stages without requiring separate model tasks.

Before concluding that no framework change is needed, check the selected device's
actual mechanism against the proposed API's preconditions and its closest call site.
For the capability currently blocking progress, cite short source/device and target
excerpts in the existing report and name the smallest check that could disprove the
mapping. Optional capabilities are not guaranteed by an API name. Missing evidence
means the mapping is unresolved, not that the whole platform lacks support. Reuse
prior valid evidence; investigate only applicable mechanisms, without a new report
format or a requirement to execute all driver behavior at this stage.

Use an uncertainty-driven probe within this stage when a consequential mapping is
still unresolved: choose the smallest applicable check that distinguishes the
competing explanations (for example API callability, DMA ownership constraints,
interrupt routing, or teardown ordering). Reuse a prior check with matching inputs.
Do not run a fixed probe suite for every driver or add a separate review round.
If existing source evidence settles the question, proceed. Record any attempted
probe's command, inputs, observation and remaining uncertainty in the current report.
Use a bounded probe when it resolves uncertainty; then continue with device
integration in the same task. A build-only probe establishes callability, not runtime
device correctness. Stop repeating a probe that cannot distinguish the hypotheses.

## Required procedure

1. Read the frozen target-platform study and migration-contract/test-matrix
   inputs. For every capability gap, cite the contract ID, the target source
   definition/call site, the direct blocker, and the smallest permitted change
   level under `target-changes.md`.
2. Recheck the target worktree against its frozen base. Search for an existing
   extension point and the closest analogous implementation before editing a
   pre-existing target file. Do not broaden the target change for convenience.
3. Implement and test the target API/framework capability and driver required by an
   accepted contract. During a repair, update affected driver integration and
   tests coherently when necessary; final implementation will resnapshot and
   validate the complete delivery. Avoid unrelated target changes.
4. Do not add compatibility shims, fallback behavior, alternate buses, or
   dual paths. The driver must consume the single enabled target interface in
   this delivery.
5. Run the narrowest target check, then the affected driver-independent
   regression checks. Record commands, revisions, exit codes and relevant
   output. A capability that cannot be validated is `BLOCKED_TARGET_CHANGE`,
   not an implicit success.

## Report and handoff

Write the report under `.dpf-output/` and include `DPF_SELF_REVIEW: PASS` only
after checking every item below:

- capability-gap IDs and linked contract IDs;
- direct target evidence and alternatives considered;
- exact files/symbols changed (or an explicit empty change set);
- necessity, smallest behavioral effect, API/ABI/safety impact and rollback;
- checks and regressions with `PASS`, `FAIL`, `NOT_RUN` or
  `BLOCKED_TARGET_CHANGE` status;
- affected shared integration paths and checks needed by final implementation.

Submit the report through the controller submission command. The controller
creates an immutable checkpoint, report and change inventory, followed by a
complete implementation snapshot and normal smoke/public execution checks;
do not hand-write hashes or claim a target change from prose alone.
