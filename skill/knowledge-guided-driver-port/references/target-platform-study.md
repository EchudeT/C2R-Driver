# Target-platform study in Driver Port Factory

DPF performs one bounded source/target analysis under the current analysis-task and functional_scope.
The source obligations and accepted tests remain mandatory. This reference supplies techniques, not
an additional platform survey, profile template, every-API table or model review gate.

Start from source entrypoints and observable behaviors, then inspect the closest target definition
and call site for a question that changes the mapping. Check relevant caller context, ownership,
resource publication/release, error and progress conditions. For a nonblocking source path, mutual
exclusion alone is insufficient. For transferred resources, identify the target owner and necessary
lifetime conditions. Resolve important facts from pinned originals; API names are not guarantees.

Use a similar driver only for the needed registration, ownership, access or integration mechanism.
Do not trace its unrelated hardware or lifecycle. Inspect relevant retrieved originals, not every
hit. Knowledge records are navigation and evidence, not a substitute for reasoning or runtime tests.
The optional original-kernel source map can help a current navigation gap; it is not required reading.

In the same analysis report, record the source behavior, target responsibility and preconditions,
compatibility differences, and planned stimulus/assertion once, with source locations. Reuse these
conclusions in contracts and implementation. Required kernel integration cannot be replaced with
callback tests unless the operator's frozen boundary explicitly allows that delivery level.

Stop when required behavior, intended target ownership/path, acceptance and design-changing premises
are settled. Defer local API spelling, file organization and helper choices to the selected implementation
behavior, where they must be checked before use. Never mark a deferred choice VERIFIED. A missing fact
that changes correctness or integration needs a bounded investigation or blocker; lack of exhaustive
API research, a platform-profile heading, or unrun planned tests does not itself require more analysis.

On repair, update the affected decision and its dependents, preserving stable behavior IDs and code.
Do not restart an unrelated survey or add a reviewer. Driver execution and final acceptance remain
separate from the provenance checks that accept this analysis artifact.
