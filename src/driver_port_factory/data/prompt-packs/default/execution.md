# Executable evidence

Use self-contained shell entrypoints with a shebang (Bash default), bounded waits, process/port
preflights and cleanup limited to run-owned resources. Controller and worker use the same scripts.
Environment smoke must exercise the selected boot/device route; version/help listings are not smoke.
Container QEMU requires a fresh docker run mounting the current execution directory so the collector
can bind live execution to this run. Do not use an existing container, dummy QEMU or renamed wrapper.

Public scripts boot DPF_RUNTIME_ARTIFACT unchanged through a supported boot/disk argument or exact
container bind mount. A log/name argument is not a boot binding; copied/patched images are not the
frozen artifact. Configure boot settings and guest test payload during packaging. Supported direct
bindings are -kernel/-bios/-pflash/-cdrom/-hd[a-d]/-fd[a-b] and -drive file=<path>. If the target needs
another mechanism, report the concrete collector limitation rather than adding a fake invocation.
Store fresh observations in .dpf-output/qemu-runs, outside rootfs/iso-root. Indexed suffixes are .log,
.txt, .json, .jsonl, .pcap, .pcapng, .bin, .md, .sha256, .exit, .out, .err and .csv.
Keep any harness helpers and mutable oracle inputs in .dpf-output/harness/ (regular files); these
are hash-bound with public-qemu.sh and the frozen implementation/image. Do not change test inputs
between the controller run and final self-check. Request a new controlled run after relevant changes.

Self-review is part of the existing work, not another report or persona sweep. Independent review
is conditional on unsafe risk in changed Rust or DPF_INDEPENDENT_REVIEW: <specific residual reason>
before the final verdict. A trigger is not a defect. Do not evade it by renaming files or weakening
safety boundaries. Address scoped findings together or rebut them with evidence; keep closed findings
closed unless relevant inputs changed. Optional polishing does not justify REWORK.
