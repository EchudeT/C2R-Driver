# Target-Platform Study Gate

The migrated driver must be designed from the pinned target platform's actual source and original documentation, not from generic Rust or kernel experience. A knowledge base is a navigation and traceability layer; it does not replace reading target originals.

## Optional Linux → Asterinas source navigation

For Linux-to-Asterinas target study, you may consult [the original-kernel source map](../../linux-asterinas-driver-map/SKILL.md) if it helps locate target APIs or core paths. If used, select only relevant Asterinas pages and reuse existing evidence records. This is optional: neither reading the guide nor recording its use is an acceptance requirement, and its absence is not a blocker. The guide describes original kernels, not the current migration implementation; verify conclusions against this run's frozen originals. Do not preload the whole collection.

## Required study inputs

Locate and freeze:

- the target source root and exact revision;
- normative architecture, driver API, development, safety/style, concurrency, lifecycle, error, packaging, and QEMU/runtime documents;
- the closest in-tree driver or component using the same bus, device class, I/O mechanism, interrupt model, or framework;
- the framework owners and traits/types called by that analogous implementation;
- versioned runner, container, CI, image, module/component, and packaging definitions used to execute changes.

Do not rely on README summaries alone. Explore the source tree directly with repository search, language-aware navigation, call sites, manifests, and project-provided metadata tools. Open every knowledge-base hit at its original local path and surrounding context.

## Build the target-platform profile

Copy and fill [the target-platform profile template](../assets/target-platform-profile.md) before migration contracts or Rust implementation. The profile must answer, with target-source or original-document locations:

1. How a driver is declared, selected, registered, matched, initialized, started, stopped, and released.
2. How bus resources, MMIO/PIO, DMA, buffers, interrupts, deferred work, timers, and logging are represented.
3. Which execution contexts invoke each callback and which operations may block, allocate, lock, or access userspace.
4. Which lock, guard, atomic, memory-ordering, ownership, lifetime, and error conventions the framework expects.
5. Where safe wrappers end, when `unsafe` is permitted, and what safety comments/invariants are required.
6. How errors propagate into the subsystem and which failures require retry, drop, reset, detach, or panic avoidance.
7. How comparable in-tree drivers structure state, cleanup symmetry, observability, and tests.
8. How current source changes become a runnable QEMU artifact under the selected artifact mode.
9. Which exact existing files require integration wiring or necessary target changes.

For each answer distinguish normative documentation, target source behavior, analogous example, and inference. Source at the pinned revision resolves implementation questions; normative documents govern intended architecture and coding rules. Record conflicts rather than selecting whichever answer is easier.

## Target API evidence table

Before writing Rust, list every target-facing API or type the design expects to use:

```text
api_or_type
purpose_in_driver
definition_path_and_lines
relevant_impl_or_call_site
analogous_driver_path_and_lines
documented_contract
execution_context
ownership_lifetime_and_cleanup
error_behavior
safety_or_unsafe_obligations
confidence: VERIFIED | INFERRED | UNKNOWN
```

An API name without a definition and relevant call site is not established. Do not invent methods, traits, callbacks, feature flags, manifests, or lifecycle behavior from names or memory. Unknown entries trigger direct source investigation and knowledge-base repair.

## Trace one complete analogous path

Trace at least one closest target implementation end to end, as applicable:

```text
selection/configuration
  -> registration and match
  -> resource acquisition
  -> device initialization
  -> request or packet submission/completion
  -> interrupt/deferred processing
  -> error propagation/recovery
  -> stop/detach/cleanup
  -> artifact inclusion and QEMU launch
```

Do not copy its hardware logic. Use it to learn target framework semantics, file placement, API ownership, callback context, coding conventions, packaging, and observability.

## Gate result

The gate is ready only when the profile and API table cover every target interaction required by the initial migration contracts, each verified entry points to an original, and unknowns have a concrete investigation or target-change record. A low-quality knowledge base is not a reason to bypass this gate; repair retrieval from the directly inspected target source and documents.
