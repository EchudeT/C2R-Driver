# Execution interface

Controller and worker execute the same self-contained shell entrypoints with a shebang.
The controller captures exit/timeout and observed QEMU execution; these are mechanical
observations, not a functional verdict. Follow the Skill for execution and evidence rules.

Public execution uses DPF_RUNTIME_ARTIFACT for production and absolute paths for packaged
variants under .dpf-output/harness/variants/. Keep helpers and oracle inputs as regular files
under .dpf-output/harness/; they are hash-bound with public-qemu.sh and the implementation/image.
Store observations in .dpf-output/qemu-runs, outside rootfs/iso-root. Collected suffixes are
.log, .txt, .json, .jsonl, .pcap, .pcapng, .bin, .md, .sha256, .exit, .out, .err and .csv.
Changed execution inputs require a new controller run; report-only edits reuse valid receipts.

The container collector recognizes fresh docker runs mounting the execution directory.
Boot bindings recognized by the collector include -kernel/-bios/-pflash/-cdrom/-hd[a-d]/-fd[a-b]
and -drive file=<path>. These are collection limits, not required invocation spellings.
Use the checker-decision protocol with evidence for other valid routes; never add dummy calls.
