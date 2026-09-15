# Git acquisition

Acquisition is split into a lightweight revision plan and a full fetch. Both are unavailable until `migration_envelope_freeze=PASS`.

## Plan

`dpf acquire plan` resolves source, target and QEMU refs to full Git commits using `git ls-remote` or local `git rev-parse`. A branch name is never stored as the frozen revision. The generated `revision_manifest` and `acquisition_plan` contain URLs, requested refs, resolved commits, selection rules, envelope digest and command evidence.

The package registry supplies repository locations for Linux, Asterinas/星绽OS and QEMU without embedding driver knowledge. Every URL/ref can be overridden, and an external registry may be supplied for other platforms.

```sh
dpf acquire plan ./run

dpf acquire plan ./run \
  --source-url /local/linux --source-ref v6.16 \
  --target-url /local/asterinas --target-ref main \
  --qemu-url /local/qemu --qemu-ref v10.1.0
```

## Fetch

`dpf acquire run` creates:

```text
.dpf/
├── git/
│   ├── source.git
│   ├── target.git
│   └── qemu.git
├── worktrees/
│   ├── source-baseline/
│   ├── target-baseline/
│   └── qemu-baseline/
├── command-runs/acquisition/
└── manifests/acquisition.json

work/target-working/
knowledge/manifests/materials.jsonl
```

Baseline worktrees are detached at frozen commits and treated as immutable controlled inputs.
`work/target-working` is the only acquisition-created branch intended for migration edits and deliberately
sits outside `.dpf`. The controller records commit, tree ID, remote URL, clean status and a canonical SHA256
repository lock. The source driver entry receives an independent file SHA256; the recursive behavior closure
is added later.

Repository lock material entries point to canonical lock files under `.dpf/manifests/repository-locks/`, not to mutable directory names. These files are integrity-checked but excluded from full-text indexing; task-relevant source, target and QEMU files receive separate per-file manifest records as the evidence closure expands.

No downloaded script or binary is executed during acquisition.

If a ref changes between planning and fetch, or another Git operation fails, the controller
preserves partial paths and command logs, emits an auxiliary `acquisition_attempt` artifact with
`FAIL` outcome and completes the stage as `FAIL`; it never substitutes the newly observed commit
for the planned one.

## Verification

`dpf acquire verify` rechecks each controlled baseline's origin, commit, tree and clean state. It does not require the writable target migration worktree to remain clean.

The first generic source identity check verifies that the frozen source entry exists and remains inside the pinned source root. Source-platform adapters must later extend this with structured device-table, bus, module and configuration checks. Any material conflict reopens the intake decision instead of silently changing the driver.
