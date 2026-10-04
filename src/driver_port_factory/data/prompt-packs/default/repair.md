# Targeted repair of an existing delivery

Repair the current finding and its affected obligations; do not restart the full initial task.
1. Read the bound finding/failed receipt and the relevant current diff. Identify the smallest causal
   change; preserve correct source, APIs, build recipes, passing observations and contract IDs.
2. Fix the cause and run the smallest check that can disprove the fix, plus affected required regressions.
   Do not expand the matrix or alter its oracle to accept the bug. If the defect is an environment or
   harness issue, keep the driver unchanged unless evidence shows a driver change is necessary.
3. Recheck affected target coding, safety, concurrency, lifecycle, error and build rules against
   originals and analogous code. If their API or integration premises changed, query the knowledge
   base again and update the same profile/report; preserve necessity and rollback for target changes.
   Rebuild affected deliverables and update the same current report. Preserve runtime-artifact,
   presence/smoke/public scripts and helpers needed for normal controller capture. Use matching receipts
   rather than rerunning unchanged passing cases. Retain diagnostic failures as history, not current PASS.
4. Submit when current obligations are met. No stylistic cleanup, full reanalysis or extra model review
   is required. With scheduled unified implementation, a target-platform design correction stays
   inside the current behavior and session. Preserve the frozen obligations; update the affected
   premise and its evidence in the final report. Reopen a prerequisite only when its actual frozen
   scope/evidence/oracle must change, not merely because an implementation mechanism changed.

The driver smoke and final public execution still require actual device evidence. This repair mode
reduces repeated work, not frozen behavior, input binding or independent final review.
