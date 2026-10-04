# Environment execution

08 validates the configured execution route: clean baseline build, guest boot and check transport.
It does not study target APIs, design driver tests or require a device-model probe. Use supplied
reference_material.environment_setup and the explicitly selected image, OVMF and accelerator.

When platform_execution.required is true, await driver_checks.platform with only {"action":"bootstrap"}.
platform_execution.configured_route already specifies the Docker image, accelerator and OVMF path.
The host may be WSL2; this does not imply TCG or cross-architecture emulation. Build and QEMU run
inside the configured Docker container; KVM uses the passed-through /dev/kvm. Never infer or select
these parameters, inspect image catalogs to select a route, or override them on failure.
The controller builds/boots the clean baseline and returns a short report. Submit
that report with --kind report --decision pass; the controller binds the existing execution receipt.
No worker-authored probe, shell wrapper, second debug run, transcript or command/hash table is needed.
Do not launch bootstrap in a shell and poll it. Logs are available in the human monitor.
A current verified baseline is reused within the project. Missing/failed/stale evidence stops with
its specific error; there is no image pull, firmware substitution or automatic fallback.

For a platform without a managed adapter, provide the minimal executable route check using the
existing prepare_smoke interface and a short report identifying the actual build/insertion limits.
Do not pretend an unsupported platform has a managed adapter. The check must exercise the route,
exit nonzero on failure, and clean up its own processes; --version is not runtime readiness.

Device-model operations are deferred to a concrete design-changing question in joint analysis.
Driver behavior and target API adaptation belong to implementation. Baseline boot is not a claim
about device BAR assignment, migration feasibility or translated-driver correctness.
Infrastructure errors retain their receipt and stop; report the blocker without redesigning the
executor. No full platform survey or extra reviewer is required.
