# Pure Codex + goal: ${driver}

## Task and environment

Translate the Linux C **${driver}** driver into Rust for the hollow Asterinas kernel.
Work in `${workspace}`. The complete Linux kernel is at `${source}`; the target
kernel is at `${target}`. Source entry points and context-navigation hints:

${source_entries}

Target implementation location: `${target_location}`. Target baseline: ${target_baseline}.
Retain the existing bus, transport, queues, DMA, IRQ and upper
subsystems; make the necessary real integration changes.

Linux revision: `${linux_revision}`.
Asterinas upstream revision: `${target_revision}`.
Docker image: `${image_tag}`; actual frozen image: `${image_id}`.
Architecture: `${architecture}`. QEMU machine: `${machine}`; accelerator:
`${accelerator}`; CPUs: `${cpus}`; guest memory: `${memory}`.
Device: `${qemu_model}`. Firmware: `${firmware}`.
QEMU source evidence: `${qemu_source}` at `${qemu_revision}`.
The host Rust toolchain is not the execution environment.
Use the upstream Docker/OSDK/QEMU route described below;
there are no Factory helper tools in this workspace. Only the configured host devices are visible; the
Docker daemon socket is hidden; standard Docker commands reach the fixed
environment through the experiment's task-scoped infrastructure. Do not change
these settings or substitute devices.

Frozen behavior scope:

${scope}

## Two required outcomes

1. **Semantic alignment.** Preserve the source driver's observable semantics
   within the frozen device and feature scope. Find and read relevant Linux
   callers, shared driver code, headers and subsystem implementations yourself.
   The listed source entries are starting points, not a reading boundary.
   Account for initialization, normal operations, early returns, errors,
   ownership, concurrency, progress, resource lifetime and cleanup where they
   occur in the source behavior. Adapt the implementation to target APIs and
   identify which guarantees are provided by the driver, framework or caller.
   Match behavior and preconditions rather than C syntax or function count.
   Do not silently drop a required source behavior because a public test does
   not exercise it. Report any unresolved incompatibility honestly.
2. **Functional alignment.** Implement and register a real device driver that
   passes the complete, provided public test set on one final kernel artifact.
   Public tests are validation observations, not the specification of the
   translation. Derive the implementation from Linux behavior and target
   requirements. Do not implement only what the tests inspect, hard-code test
   inputs or expected outputs, bypass the device, return fake success, use a
   dummy/loopback replacement, or treat a skipped test as a pass.

Provided public cases:

${public_cases}

${public_interface}

## Autonomous execution

Pursue the native persistent goal established for this session. Decide how to
investigate, implement, check and repair autonomously within the operator's
budget. No mandatory phase reports, contract tables or reviewer calls are
required. No Factory planning, specialized migration Skill or shared knowledge
base is supplied to this baseline.

Use standard terminal, Git, Docker and the upstream OSDK/QEMU commands. No
Factory-written build.sh, check.sh, test runner, migration tool or knowledge
retrieval interface is provided to you. Read the kernel's own build and test
instructions. The original public test sources are visible in target/; the
fixed prebuilt SDK, initramfs and disk seeds are available at `${shared_assets}`.
These are common environment assets, not a translation assistant.

For Docker commands, use only the fixed image and current source/target mounts.
Mount `${shared_assets}` read-only at `/experiment-assets`, and put
`/experiment-assets/sdk` on PATH for the original `cargo osdk` command. Build
with `OSDK_LOCAL_DEV=1`, `RUSTUP_TOOLCHAIN=nightly-2026-07-21`,
`CARGO_INCREMENTAL=0`, `CARGO_PROFILE_DEV_DEBUG=0`, `CARGO_BUILD_JOBS=4`, and
`VDSO_LIBRARY_DIR=/root/linux_vdso`. The full test initramfs is
`/experiment-assets/initramfs.cpio.gz`. Use the frozen kernel command line
`console=ttyS0 earlycon loglevel=${kernel_log_level}` for builds and boots. This
log level is part of the shared environment; diagnostic assertions require its
observable output. For KVM execution, Docker must receive `--device /dev/kvm`;
seeing the device in the worker namespace does not expose it inside Docker.
The official image's QEMU executable is
`/usr/local/qemu/bin/qemu-system-x86_64`; use this absolute path or add
`/usr/local/qemu/bin` to the container PATH. A login shell may omit it.
${runtime_profile_rule}

You may inspect tests, adapt real driver interfaces and create your own command
scripts for native build/test execution. Do not write substitute assertions.
Store self-written scripts inside target/; source/ and PROMPT.md are read-only.
Do not modify test sources, frozen boot configuration or shared assets. Validate real
device behavior as needed during development. At the end, the operator runs the
same complete fixed suite used for Factory on the final candidate, outside this
model session. You do not have access to that evaluation runner. Public test
source availability never authorizes deriving the driver only from assertions.

Do not obtain or consult the withheld original Rust driver, other task targets,
operator ground truth, prior candidates or their results. The source and target
frameworks supplied for this task remain available for autonomous investigation.

Finish with the actual code and a concise description of semantic adaptations,
the actual checks you performed and remaining limitations. Goal completion alone is not
test acceptance. If the budget expires or execution fails, preserve the current
candidate, logs and real outcome.
