# Executable evidence

Use self-contained shell entrypoints with a shebang (Bash default), bounded waits, process/port
preflights and cleanup limited to run-owned resources. Controller and worker use the same scripts.
Environment smoke must exercise the selected boot/device route; version/help listings are not smoke.
The controller checks script exit/timeout and observed QEMU execution, not a boot-argument whitelist.
Use route-appropriate assertions; environment acceptance does not prove migrated-driver correctness.
Reuse valid check results for unchanged inputs. Batch known related fixes before expensive validation;
rerun affected checks, widening for shared APIs, uncertain impact or failed evidence. Required acceptance
checks still apply. Report-only edits need no behavioral rerun; formatting-only edits need the applicable
format/build checks, not a full QEMU suite unless behavior or acceptance evidence may be affected.
Give each execution distinct log paths and preflight mounts/cleanup before a costly run. Fixing a helper
requires checks of its affected behavior, not automatically all driver tests; invalid or overwritten
evidence still requires a replacement run. Reuse build caches even when a fresh container is required.
The container collector recognizes fresh docker runs mounting the execution directory. This is
a collection limit, not a requirement to replace another valid route. Never add dummy invocations.

Public tests exercise the delivered production DPF_RUNTIME_ARTIFACT and, when needed, packaged
variants in .dpf-output/harness/variants/. Keep their inputs frozen and results separately attributed;
instrumented variants do not substitute for production regressions. Configure
boot settings and guest test payload during packaging. The collector recognizes -kernel/-bios/
-pflash/-cdrom/-hd[a-d]/-fd[a-b] and -drive file=<path>; a log/name argument is not a boot binding.
Other valid boot mechanisms can be accepted by worker judgment with evidence; command spelling
does not determine functional correctness.
Store fresh observations in .dpf-output/qemu-runs, outside rootfs/iso-root. Indexed suffixes are .log,
.txt, .json, .jsonl, .pcap, .pcapng, .bin, .md, .sha256, .exit, .out, .err and .csv.
Keep any harness helpers and mutable oracle inputs in .dpf-output/harness/ (regular files); these
are hash-bound with public-qemu.sh and the frozen implementation/image. Do not change test inputs
between the controller run and final self-check. Request a new controlled run after relevant changes.

Self-review is part of the existing work, not another report or persona sweep. Risk findings are
advice, not defects by themselves. In developer-evidence mode you decide whether existing checks
suffice or more investigation is needed; a second agent has no automatic veto. Address real defects
or rebut findings with evidence. Keep accepted findings closed unless relevant inputs changed.
Optional polishing does not justify REWORK.
