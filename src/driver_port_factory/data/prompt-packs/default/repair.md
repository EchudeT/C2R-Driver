# Composite repair task

This section supplements the current stage objective during an implementation or artifact repair;
it does not replace that objective or change the frozen migration contract.
Follow knowledge-guided-driver-port/references/workflow.md phase 9 and its routed references for the recorded findings. Use existing reports for unchanged evidence.

Controller delivery:
1. Deliver .dpf-output/runtime-artifact, .dpf-output/check-presence.sh, .dpf-output/implementation-smoke.sh, .dpf-output/public-qemu.sh and needed harness inputs. Put necessary packaged variants under .dpf-output/harness/variants/. The controller performs a generic preflight before QEMU and records every rejected artifact or command with an exact path and line. The implementation smoke must check the current driver through probe/readiness and applicable single data operations before the implementation snapshot passes. Keep failures and repair their causes locally. A missing target capability or framework interface is a prerequisite rework finding; cite the target definition/call site and request target_framework_enablement rather than claiming BLOCKED for a repairable defect.
2. Complete the repair/preparation checks in the report and submit it with the tool's `pass` decision only after the checks are complete. The controller captures outputs and executes the suite, then returns observations for the Skill final self-check in the same report.
