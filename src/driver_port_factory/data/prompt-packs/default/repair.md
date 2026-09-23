# Composite repair task

This section supplements the current stage objective during an implementation or artifact repair;
it does not replace that objective or change the frozen migration contract.
Follow knowledge-guided-driver-port/references/workflow.md phase 9 and its routed references for the recorded findings. Use existing reports for unchanged evidence.

Controller delivery:
1. Deliver .dpf-output/runtime-artifact, .dpf-output/check-presence.sh, .dpf-output/public-qemu.sh and needed harness inputs. Put necessary packaged variants under .dpf-output/harness/variants/.
2. Complete the repair/preparation checks in the report and submit it with the tool's `pass` decision only after the checks are complete. The controller captures outputs and executes the suite, then returns observations for the Skill final self-check in the same report.
