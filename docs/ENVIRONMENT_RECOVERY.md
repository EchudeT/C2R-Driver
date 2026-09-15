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

Create a route plan conforming to `schemas/environment-experiment-plan.schema.json`, then register
and execute it:

```sh
dpf environment plan ./run --file experiment-plan.json
dpf environment run ./run --route-id qemu-smoke-001
```

Plans use an argv array and never a shell command. Their working directory and runner evidence must
stay inside the project. Host executables are allowed and their resolved path, size and SHA256 are
captured. Direct-QEMU plans must actually name a `qemu-system-*` or `qemu-storage-daemon` binary and
cite the frozen QEMU source checkout.

Harness, source-runner, target-runner and container routes must additionally cite a concrete local
runner or metadata file; citing only a directory cannot pass route validation.

Every route ID is immutable and may run once. A missing marker, unacceptable exit, launch error or
unaccepted timeout creates an `environment_recovery_attempt` while leaving the stage open for a
distinct retry. Only a launched process satisfying the predeclared marker and exit/timeout oracle
produces `experiment_ready_run` and advances the stage.

`EXPERIMENT_READY` only proves that a relevant target/source/model route executed. The generated
record explicitly leaves `migrated_driver_runtime_ready=false`; driver-presence proof belongs to the
later artifact and QEMU stages.
