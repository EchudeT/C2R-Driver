# Driver Translation and Platform Adaptation

## Optional platform source navigation

During Linux-to-Asterinas source analysis and contract design, you may consult [the original-kernel source map](../../linux-asterinas-driver-map/SKILL.md) if useful: Linux pages locate source paths, and comparison pages highlight mapping questions. Reading the guide or recording its use is not required. During implementation or repair, reuse established contracts/evidence; consult a page only for a specific unresolved or changed API, mechanism or integration question. Do not repeat the whole platform study or preload the collection. Verify conclusions against this run's frozen originals; the guide does not describe the current migration implementation.

## Translate contracts, not spelling

Decompose every source path into three layers:

1. hardware behavior that must remain invariant;
2. source-platform machinery that expresses that behavior;
3. target-platform mechanism that can implement the same contract.

Do not translate source framework objects, lock types, callbacks, module macros, allocators, error codes, or lifecycle calls literally. Map their evidenced intent to target APIs. If no existing target equivalent is sufficient, apply the target-change policy before proposing the smallest necessary integration or API change; mark insufficiently evidenced or excessively broad changes `UNMAPPED` or `BLOCKED_TARGET_CHANGE`.

## Required source facts

Freeze compilation before interpreting C: compiler, target ABI, defines, include paths, generated headers, language mode, configuration, and source hashes. Prefer a compilation database. Use structured AST/CPG/CFG and layout/effect facts for:

- declarations, qualifiers, types, widths, signedness, layout, packing and bit fields;
- macro expansion and selected conditional-compilation branches;
- direct and indirect calls, callbacks, function pointers and external effects;
- globals/statics, initialization order and cross-call state;
- control flow, fallthrough, goto, cleanup and non-local exits;
- lvalue/rvalue behavior, sequencing, aliasing, pointer provenance and bounds;
- volatile, atomic and device-I/O access;
- ownership, allocation, release and error unwinding.

Names, source snippets, regex matches, API counts, and compile success are supporting signals only. They cannot establish these semantics.

## Driver-specific reconstruction

Build explicit Rust state and invariants for:

- device identity and resource acquisition;
- register access width, ordering, page/bank selection and serialization;
- reset and initialization sequence with bounded waits;
- descriptor/ring/buffer ownership, indices, wraparound and length checks;
- transmit padding, limits, completion and timeout behavior;
- receive validation before allocation/copy/delivery;
- interrupt acknowledgement, masking, re-enabling and deferred work;
- lock order and which contexts may acquire each lock;
- error classification, containment, reset/retry and observable counters;
- registration, start/stop/detach and cleanup symmetry.

Represent register fields and states with Rust types when this prevents invalid combinations. Avoid broad `unsafe` blocks; each unsafe operation needs a local explanation of pointer validity, alignment, lifetime, exclusivity, access ordering, and the hardware/target evidence that supports it.

## Target-platform boundary

Use the completed target-platform profile and API evidence table to discover the target's supported module/component packaging and extension mechanisms. Reopen the cited target definitions and analogous call sites while implementing. Prefer integration entirely through new or migrated module-owned files. When that is impossible, follow `target-changes.md`: permit the smallest necessary registry, manifest, build, framework, or API modification that is evidenced, scoped, reversible, and validated.

For every pre-existing target file change, record:

- exact file and extension point;
- minimal change the platform would require;
- affected migration contracts and tests;
- whether an existing module-local registration route can avoid it;
- compatibility, safety, validation, and rollback obligations;
- status `TARGET_CHANGE_PLANNED`, `TARGET_CHANGE_VERIFIED`, or `BLOCKED_TARGET_CHANGE`.

## Translation coverage

For each source unit record source span and structured fact IDs, target span, mapped contract, lowering/reconstruction choice, assumptions, safety obligations, diagnostics, and status:

- `TRANSLATED`: implemented from sufficient facts;
- `PARTIAL`: explicit residual work remains;
- `BLOCKED`: required source or target evidence is missing;
- `UNMAPPED`: known behavior lacks a target equivalent;
- `UNSAFE_REQUIRED`: preservation requires an audited unsafe boundary.

Report coverage independently for functions/callbacks, types/layout, globals/state, hardware effects, lifecycle/error paths, and tests/assertions. File or line percentages do not measure driver functionality.
