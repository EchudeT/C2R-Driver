# Evidence selection boundary

Use `reference_material.evidence_reuse.local_repositories` for frozen paths and versions.
The controller can also answer `acquire locations RUN`; do not reconstruct the repository
manifest by paging through acquisition logs or copying hashes.

This task selects original materials for later analysis. Start from the frozen driver entry,
its direct dependencies, the likely target integration entry and directly relevant definitions,
device/QEMU specifications and tests, and relevant safety/build route originals.
Read symbols and bounded spans only as needed to decide relevance or substantiate a gap.
Use the pinned shared knowledge search when it can answer a concrete location question;
check applicability against this task's fixed version. Prior interpretations are navigation.

Stop selecting once required facets have relevant retrievable originals or explicit,
supported gaps and the proposal is ready. Do not perform a second API study, write an
API contract table, or exhaustively read each selected file here. If a dependency is needed
to understand the selection, include it; there is no arbitrary file-count cap.
Deeper ownership, concurrency and failure-path analysis belongs to target study/implementation.
If you already found a consequential fact, retain it briefly in the existing facet rationale
with its source location. Do not create another report or claim the later analysis is complete.

Use the existing proposal schema and controller gap rules. Selected directories are expanded
and overlapping paths deduplicated by the controller. A missing search hit is not an absence
proof, and source/QEMU evidence cannot silently substitute for physical hardware evidence.

Distinguish unavailable originals from a limitation in originals already available. For the latter,
keep relevant repository_paths and declare gap.impact plus gap.basis referencing another controlled
facet that supports the limitation. Successful acquisition never proves complete semantic coverage.
A FAILED retrieval (empty/invalid content, broken tool or transient fetch) must be fixed or the
irrelevant selection narrowed; it cannot be waived by declaring a gap. Read the reported facet,
locator and cause. To locate a driver's tests, search file names/references first and select the
relevant originals; do not submit an entire cross-driver test tree just to express absence of a
named test. No test hit is not proof that no test exists. Preserve uncertainty as a bounded limitation.

This is an initial material selection, not a promise that every later API has been researched.
Joint analysis and the current implementation behavior may inspect additional pinned repository
files directly. Do not reopen acquisition or regenerate the corpus merely because an additional
local definition is needed. Repair this stage only for unavailable originals or an actual change
to the controlled evidence boundary. Facets describe evidence domains, not research quotas.
