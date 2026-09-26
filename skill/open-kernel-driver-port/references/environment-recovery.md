# Runnable Environment Recovery

The environment phase must converge on executable evidence. Missing tools, absent build documentation, an incomplete source build, or an unfamiliar target layout are recovery triggers, not terminal outcomes.

## Required outcomes

Track two separate milestones:

- `EXPERIMENT_READY`: a pinned QEMU invocation actually starts a relevant target, source baseline, or device-model harness and produces captured output plus an exit/timeout result.
- `MIGRATED_DRIVER_RUNTIME_READY`: QEMU is proven to execute a target artifact that contains the current migrated driver and approved target changes.

The first milestone is mandatory before an environment blocker can be the final result. The second is mandatory for migrated-driver runtime claims. A qtest or device-model run can satisfy only `EXPERIMENT_READY`; it cannot substitute for the migrated driver being in the runtime path.

## Do not assume a from-source target build

Discover the target's actual delivery model from local evidence and pinned upstream metadata. Inspect, as applicable, launch scripts, task runners, Makefiles, Cargo workspaces, CI workflows, release assets, container definitions, SDKs, disk/initramfs images, package manifests, module loaders, image-repacking tools, and QEMU argument helpers.

Choose the least expensive reproducible route that can contain the migration:

1. reuse a verified local runner, image, cache, or prior project baseline;
2. use the target project's official development container, SDK, run script, or release artifact;
3. inject or package the driver through a supported module, component, initramfs, disk overlay, or image-repack path;
4. reconstruct the minimum local build/packaging command from versioned CI and repository automation;
5. perform a full source build only when it is documented, already working, or required to place the driver in the target artifact.

Do not require a conventional `build` command, compilation database, or full clean rebuild when the target's normal workflow is image-, container-, component-, or QEMU-based. Record the selected route as `artifact_mode` and state which portions are compiled, reused, injected, or prebuilt.

## Recovery sequence

After driver identity is confirmed:

1. inventory host architecture, available QEMU binaries, emulation/acceleration, container runtime, toolchains, disk space, filesystem semantics, ports, and existing project artifacts;
2. search local project history and target metadata for known-good run commands and artifact identities before installing anything;
3. acquire pinned public binaries, containers, SDKs, images, or toolchains into approved local storage when possible;
4. establish the smallest unmodified target or QEMU-device smoke run;
5. prove how the migrated driver will enter the runtime artifact, then record its hash/identity after packaging;
6. run the smallest probe through QEMU before expanding to functional and failure-path tests.

When a command fails, classify it and change one causal variable: host architecture, emulator binary, acceleration mode, container architecture, dependency version, filesystem, image format, packaging path, QEMU machine/device option, or harness. Preserve each failed attempt; do not repeatedly run the same unchanged command.

## Required fallbacks before environment blocking

Do not finish at `BLOCKED_ENVIRONMENT` until every applicable route has been investigated and either executed or ruled out with evidence:

- existing project runner or recorded baseline;
- official target runner/container/SDK/release image;
- supported module/component/image injection or repacking;
- repository CI/release automation as a build/run specification;
- project-local or containerized QEMU/toolchain when host installation is absent;
- a direct QEMU device-model/qtest/QMP smoke experiment for independently testable hardware assumptions.

Continue running independently valid source-platform and QEMU-model experiments while target integration is being repaired. These results narrow uncertainty but must keep their attribution boundary.

Ask the user only when progress requires credentials, acceptance of a license, administrator action, unavailable proprietary material, destructive host changes, substantial unapproved downloads/cost, or a broad target change covered by `target-changes.md`. Report the exact requested action and continue all unaffected experiments first.

## Legitimate terminal blocker

An environment blocker is legitimate only when it identifies a concrete external prerequisite, records attempted routes and error evidence, explains why remaining fallbacks do not apply, and still includes at least one executed QEMU/source/model experiment when QEMU can represent the confirmed device. Use `BLOCKED_FULL_INTEGRATION` when experiments ran but no evidenced method can place the migrated driver into the target runtime. Never report documentation absence by itself as the blocker.
