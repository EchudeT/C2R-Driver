# Composite repair task

1. Verify the recorded finding against confirmed scope, current code and originals. Explain unsupported findings; repair only evidenced functional or safety defects. Preserve unaffected coverage and conclusions.
2. Complete affected checks and the Skill self-check. Rebuild artifacts only when their inputs changed. Prepare the existing public runner for affected regressions, reusing valid builds and tests rather than running a duplicate full suite.
3. Deliver .dpf-output/runtime-artifact, check-presence.sh, public-qemu.sh and needed harness inputs. Include necessary packaged variants under .dpf-output/harness/variants/ and explain configuration/source differences in the existing delta report.
4. End the report DPF_SELF_REVIEW: PASS for completed repair/preparation checks, not unexecuted QEMU results. The controller captures prepared outputs and executes the suite without a separate packaging/planning model turn, then returns observations to this worker for final self-check. Reference unchanged coverage; no extra handoff document.
