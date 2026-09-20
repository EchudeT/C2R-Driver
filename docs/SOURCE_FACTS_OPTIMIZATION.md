# Source-first compiler facts

## Evidence and choice

Direct Skill histories did use compiler facts: NE2000 retained 107 summary rows
(55,255 bytes, 18 MiB source-facts directory); E1000 retained 488 rows
(337,928 bytes, 176 MiB directory). Their prefix-filtered exports are not equivalent
to complete typed dependency, callback, layout and CFG extraction. Removing all
compiler analysis is therefore not supported by the comparison.

The stopped cost-01 run actually queried compact C facts in six successful command
batches, including implementation and contracts. Generating every header's complete
semantic subtree was not justified by that use. The prior two-TU static analysis
took about 345 seconds and produced 2.49 GB of distinct structured CAS contents.
This does not establish how much model cost was attributable to analysis.

## Implemented boundary

- Read pinned source first. Compiler facts answer type/layout/call/control-flow
  questions; they are not another mandatory document for the worker to summarize.
- Root the projection in frozen C files, shared C cores and their local headers.
  Follow compiler declaration IDs, redeclarations and compiler type dependencies
  into shared headers. Preserve all ambiguous type-name candidates, not a prefix
  heuristic. Keep complete included subtrees and the original configuration.
- Keep AST, layout, effects, CFG and LLVM extraction for this relevant closure;
  no semantic domain has been silently replaced by grep. Full Clang AST stdout is
  temporary, with command, diagnostics and digest retained. Other raw compiler
  outputs remain available for macro/IR inspection. Persist compact semantic JSON.
- Serve summaries by default, full calls/CFG on request, and resolved callback names
  with locations. No extra worker, coordination report or compatibility path.
- Skip payload loading when no bundle validator exists. Otherwise read dependency
  payloads by requested kind. Navigation verifies its bound inputs, not unrelated
  historical runtime artifacts. Explicit full audits remain available/default.

Compiler extraction now runs as the SOURCE_ANALYSIS operation inside source_closure,
with an immutable intermediate receipt; it is not a separate workflow stage. Extraction
is explicitly requested by the worker, then performed and validated by the controller
for the compilation closure. Symbol-level retrieval does not invoke a fresh compiler.
A source question outside the retained scope still needs
original-source/compiler evidence; a zero-match lookup is not proof of absence.

## Offline NE2000 check (2026-09-20)

Reprojected the original captured Clang outputs in a temporary directory. Rebuilt
semantic indexes, parsed every raw fact domain, ran cross-TU pointer resolution and
semantic validation. Historical run files and paid sessions were not changed.

| Metric | Previous | New |
| --- | ---: | ---: |
| Two retained AST projections, bytes | 1,144,999,907 | 82,538,237 |
| Two semantic indexes, bytes | 1,263,073,369 | 63,451,554 |
| Function definitions per TU | 6,648 / 6,754 | 199 / 177 |
| Driver-local function identities per TU | 31 / 23 | 31 / 23 |

AST + semantic storage drops about 93.9%. `__ei_start_xmit`'s block-output callback
and `ei_receive`'s two input/header callbacks remain resolved with complete target
sets. All retained record/CFG correlations validated. An initial roots-only version
omitted two uncalled allocation wrappers in the local driver header; local headers
were added as roots before accepting the comparison, and a regression covers this.

This is not a new paid migration, not a full correctness equivalence proof, and not
a matched end-to-end latency or dollar benchmark. Complete compiler execution still
occurs transiently; further on-demand extraction should only replace it after proving
configuration, cross-TU callbacks and missing-fact repair equally reliable.

Final offline regression: 248 tests and 43 subtests passed in 168.23 seconds
(`pytest -o addopts='' -q`; log `/tmp/dpf-source-scope-final.log`). These include
real-Clang fixtures and synthetic workflow/QEMU tests, not a fresh full driver port.

Prompt changes stay in the common evidence-economy section: source-first, the actual
`results` field, availability timing and selective detail. This follows the concise
goal/context/boundary approach in [OpenAI prompting guidance](https://developers.openai.com/codex/prompting/),
without changing the model, API or adding per-stage boilerplate.
