# Execution interface

Controller and worker execute the same self-contained shell entrypoints with a shebang.
The controller captures exit/timeout and observed QEMU execution; these are mechanical
observations, not a functional verdict. Follow the Skill for execution and evidence rules.
Preflight advisories are hints, not rejected outputs: helpers or external controllers can
supply runtime binding or QMP continuation. Use actual observations to resolve them; do not
rewrite a working harness merely to silence a text heuristic. A preflight PASS is not boot proof.
Before broad implementation, establish the target build/insertion route and the first applicable
device operation. In the existing report, map required contract/test IDs to observations and
keep BLOCKED/NOT_RUN explicit. Registration, model enumeration, or host-printed success strings
cannot stand in for device operations. Preserve the frozen scope; repair a failed oracle without
silently removing its required behavior. N/A needs a device/target reason, not a missing implementation.

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

For an Asterinas target, all target experiments must execute inside the official
`asterinas/dev:<pinned-tag>` development image. Mount the execution worktree and every
runtime/artifact path needed by QEMU, record the exact image tag and image ID in the report,
and invoke the QEMU binary from inside that container. A host QEMU invocation is not target
runtime evidence; the controller rejects an Asterinas run unless it observes the QEMU process
inside an `asterinas/dev` container. Use `--device /dev/kvm` only when the image route requires
it; TCG is a valid bounded fallback. The model smoke and public-QEMU scripts must use the same
container boundary as the eventual target build/run route.
