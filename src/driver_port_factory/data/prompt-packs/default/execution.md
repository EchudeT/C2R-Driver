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

Implement a small end-to-end path early: actual target insertion, device initialization,
then the first applicable operation, before expanding to all required behavior. Choose
the next bounded probe by the most consequential unresolved assumption, not a fixed
DMA/interrupt checklist; some driver classes need neither. Reuse valid evidence and
existing scripts. This order does not reduce the final contract or add another submission
gate. Record what the probe establishes and what it leaves untested in the existing report.

During driver implementation, use the frozen test matrix to identify which later public
stimuli require driver-owned entrypoints or observable state. Implement those applicable
hooks before freezing the image; reuse existing interfaces where they suffice. In the
existing compliance report, map each required runnable row to its entrypoint, observable
assertion and remaining execution owner. A passing first-operation smoke does not cover
the rest of the matrix. This is a planning/self-check within the current stage, not a
requirement to run the full public suite early or add a separate review.
For applicable boundary tests, distinguish semantic limits from backing allocation sizes
and test immediately outside the legal boundary. Assert promised state/effects rather
than merely printing the branch taken; a read/write test needs readback, and a negative
control needs evidence that its stimulus actually occurred. Reuse unchanged valid checks.

For long builds and QEMU runs, retain the process/session handle and inspect bounded
log tails. When the process is healthy and no input is needed, use a longer supported
wait (typically 10–30 seconds) instead of repeated one-second empty polls. Short waits
remain appropriate for interactive input or early failure diagnosis. Do useful independent
work while waiting where possible; do not relaunch an active command just to obtain output.

Reuse a proven target command from the current run before trying generic host/workspace build
commands. For an unfamiliar test runner, inspect its working-directory, firmware/boot mode,
test-filter and console-output conventions before a costly launch. A zero-test run is NOT_RUN
for the intended tests. Check real artifact insertion as soon as the first build is available,
before expanding the driver: compiling a dependency does not prove that it is linked or initialized.
Do not remove apparently unused dependencies without checking macro-generated uses. Defer optional
cleanup that would invalidate a passing artifact unless it resolves a concrete required defect.

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

Follow the selected, observed build/run route's container boundary. When that route uses a
container, mount the execution worktree and every runtime/artifact path needed by QEMU, record
its exact image reference and image ID, and invoke QEMU inside it. A host run cannot substitute
for evidence from a required container route. The usual Asterinas bootstrap uses the official
`asterinas/dev:<pinned-tag>` image; consult the current route rather than inferring a different
boundary from the platform name. Use --device /dev/kvm only when the route requires it; TCG is
a valid bounded fallback. Smoke and public experiments must follow the selected target route.

Use tool_runtime.managed_experiment followed by --script .dpf-output/implementation-smoke.sh
--timeout 300 to get an authoritative observation inside this task. Inspect the returned receipt,
repair the cause and repeat only after changed inputs. Later stage capture reuses matching passing
observations instead of executing the same experiment again. Use --fresh for an intentional new
independent repetition, not to evade a failed assertion. Runtime and source identity are still checked.

For multiple independent public cases, optionally write .dpf-output/experiments.json as a list:
[{"id":"probe","script":".dpf-output/harness/probe.sh","contracts":["C1"],
  "dependencies":[".dpf-output/harness/probe-input.json"],"timeout_seconds":300}].
Then invoke managed_experiment with --suite. Each case returns its own immutable receipt and only
changed cases rerun. Dependencies must include every helper/oracle/configuration file read by that
case; omit dependencies to bind all helpers conservatively. Independent repetitions need distinct
case IDs. Shared changed source/runtime invalidates affected execution; failed observations remain.
Keep public-qemu.sh as the delivery entrypoint; a case manifest selects the controller's case runner.
Do not claim suite completion merely because one case passed.

Optionally maintain .dpf-output/obligations.json as a small list of {"id":"C1",
"implementation":["driver.rs:init"],"tests":["probe"],"status":"implemented"}.
Use the same IDs as the current contracts. It is navigation, not a new submission schema or extra
quality gate. Reports remain valid without this file. Review all required obligations, including
NOT_RUN and unsupported cases, against their actual evidence.

When a complete public suite has been run through managed_experiment in this task, inspect its
actual receipts, assertions and limits and finish the self-review in the same report you will submit.
Then invoke tool_runtime.experiment_self_review --report <REPORT_PATH> and submit that unchanged
report. This is an explicit assertion that you reviewed the returned execution, not merely planned
it. Matching controller capture will reuse both execution and this self-check; independent final
review remains. Changing report bytes or execution inputs requires a fresh acknowledgment or the
normal downstream worker self-check. For a single public-qemu.sh use --timeout 3600 to match the
controller; suite cases use their declared timeouts. Do not acknowledge unexecuted or failed work.

Implement in dependency order inside this task: resolve uncertain target interfaces, establish
minimal working behavior, then extend to required concurrency, boundary and failure cases. Choose
increments appropriate to this device, not a fixed network-driver checklist. Compile/test early
enough to falsify design assumptions before expanding the implementation; do not create a new
model task or report for each increment.

For portable pure logic or modeled register traces, tool_runtime.early_probe accepts --reference
<original-C-adapter-script> --script <Rust-adapter-script> --inputs <fixed-case-file> --contract ID.
Declare adapter sources/helpers with repeated --dependency <absolute-path> so their bytes are
archived and bound as well as wrapper scripts. Both scripts read the same DPF_PROBE_INPUT file and emit deterministic observable results on stdout;
put build diagnostics on stderr. Comparison is exact bytes, with no filtering. Freeze reference
semantics and test vectors from original code/specification before adapting the candidate; never
change the reference or relax assertions just to match it. Inspect input/script hashes, commands,
outputs and limitations in the receipt. MATCH only covers those inputs and the authored adapters;
it does not replace device execution, DMA/IRQ validation or independent review. Use the selected
container/toolchain inside probe scripts when needed; probes do not enforce the public-QEMU route.
Recent probe receipt paths remain available across task/context boundaries. Prefer fixing primary
compiler spans and their actual API definitions to restarting full design. A timeout, missing tool
or unknown command failure is not automatically a driver bug. If unchanged inputs repeatedly fail,
inspect the cause or revise the hypothesis rather than blindly rebuilding.

Choose behavioral checks from frozen obligations and original behavior, including invalid/boundary
inputs, post-failure state/resource ownership, readiness before publication, and restoration after
stateful diagnostics where applicable. Check both positive and forbidden outcomes. Do not infer a
negative-device test from silence alone when actual device identity/topology can be observed.
