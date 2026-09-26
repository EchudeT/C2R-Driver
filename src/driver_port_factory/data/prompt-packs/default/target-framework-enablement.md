# Target-framework enablement

This stage owns only the target platform capability that the frozen migration
contracts require and the target study proved is absent or incomplete. It is a
separate enablement gate, not a second driver-implementation task.

Before concluding that no framework change is needed, check the selected device's
actual mechanism against the proposed API's preconditions and its closest call site.
For the capability currently blocking progress, cite short source/device and target
excerpts in the existing report and name the smallest check that could disprove the
mapping. Optional capabilities are not guaranteed by an API name. Missing evidence
means the mapping is unresolved, not that the whole platform lacks support. Reuse
prior valid evidence; investigate only applicable mechanisms, without a new report
format or a requirement to execute all driver behavior at this stage.

## Required procedure

1. Read the frozen target-platform study and migration-contract/test-matrix
   inputs. For every capability gap, cite the contract ID, the target source
   definition/call site, the direct blocker, and the smallest permitted change
   level under `target-changes.md`.
2. Recheck the target worktree against its frozen base. Search for an existing
   extension point and the closest analogous implementation before editing a
   pre-existing target file. Do not broaden the target change for convenience.
3. Implement and test only the target API/framework capability required by an
   accepted contract. Keep target-framework files distinct from driver files;
   do not edit the migrated driver, public tests, runtime artifact, QEMU
   harness, or unrelated target code in this stage.
4. Do not add compatibility shims, fallback behavior, alternate buses, or
   dual paths. The driver must consume the single enabled target interface in
   the following stage.
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
- confirmation that no driver-owned path overlaps the enablement snapshot.

Submit the report through the controller submission command. The controller
creates the immutable target-framework bundle, report and change inventory;
do not hand-write hashes or claim a target change from prose alone.
