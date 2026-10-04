# Runnable environment recovery

This phase implements the `open-kernel-driver-port` environment-recovery gate. It does not assume
that the target has a conventional full source build.

First inspect the host, frozen target tree and frozen QEMU tree:

```sh
dpf environment inspect ./run
```

The result records available QEMU/container/toolchain executables and discovers versioned evidence
for local runners, SDK/container flows, component insertion, image repacking, CI-derived commands
and source builds. Discovery does not prove that a route works.

Create one bounded route plan, then register and execute it:

```sh
dpf environment plan ./run --file experiment-plan.json
dpf environment run ./run --route-id qemu-smoke-001
```

Plans use an argv array and never a shell command. Their working directory and runner evidence must
stay inside the project. Registration rechecks the source, target and QEMU locks, origins, commits,
trees and tracked/untracked cleanliness, then freezes the QEMU executable path, hash, version and
provenance. Execution rejects any repository or executable drift.

The selected route may use any artifact mode evidenced by the Skill: an existing or official
runner, container/SDK, component insertion, image repack, CI-derived or source build, source
baseline, or direct device-model run. QMP is optional and is used only when the selected experiment
needs it.

Every route ID is immutable and may run once. The controller launches the frozen executable and
captures argv, stdout/stderr, exit or bounded timeout, runner evidence and repository identities.
A failed launch or an exit outside the plan creates an immutable attempt and leaves the stage open.

`EXPERIMENT_READY` only proves that a relevant target/source/model route executed. The generated
record explicitly leaves `migrated_driver_runtime_ready=false`; driver-presence proof belongs to the
later artifact and QEMU stages.
