# Independent evidence review

Write only your review report; source, tests, worker reports and frozen evidence are read-only.
Apply the supplied Skill originals and the agreed scope. Review the whole assigned material before
returning one consolidated report containing ALL discovered substantive findings and unresolved
questions. Do not return after the first defect or drip-feed already discoverable issues across rounds.
An unresolved question is not a proven defect. State review limits honestly; do not invent completeness.
Use the controller-only stage-ownership and repair-routing map below. It describes
workflow ownership only; the supplied Skill originals remain the sole source of
technical porting requirements:

| Finding | Earliest affected stage | Do not use |
| --- | --- | --- |
| Missing/wrong original file, external document, provenance, hash, or controlled material | `evidence_closure` | `migration_contracts` merely to acquire a missing original |
| Environment, artifact mode, QEMU route, or executable entrypoint | `environment_recovery` | `evidence_closure` |
| Target API definition/call path, initialization order, ownership/context, or bounded target change | `target_platform_study` | `evidence_closure` |
| C compiler/configuration, generated defines, preprocessor output, struct layout, ABI, volatile-I/O effect, translation contract, or test stimulus/assertion provenance | `migration_contracts` | `evidence_closure` |
| Changed Rust/source implementation or source snapshot | `driver_implementation` | any evidence/design stage |
| Packaging, image inclusion, entrypoint, artifact identity | `artifact_preparation` | `driver_implementation` unless source actually changed |
| Unchanged-artifact runtime harness/oracle/receipt | `public_qemu_validation` | `evidence_closure` |

Do not route a semantic contract defect to `evidence_closure` merely because a report calls it
an evidence gap. A repair target must name the earliest stage that owns the missing fact or
decision, and the report must explain why later related findings remain in the same review.

## What to verify

- When optional platform navigation was used, reuse the selected paths and evidence IDs in the analysis.
  Check whether the actual bus, device type and mechanisms have been covered, not whether every guide
  was read. You may consult relevant platform/comparison pages if a specific question warrants it;
  verify claims against this run's originals. Reading the optional guide is not an acceptance condition.
  Recheck mappings affected by revision, architecture, configuration or implementation changes; preserve
  closed findings otherwise. Do not add a separate navigation audit or reject work for missing optional pages.
- Follow knowledge-contract.md, especially Evidence domains and Evidence record: use the project KB
  to locate evidence, then open originals and relevant surrounding definitions. Keep hardware, source,
  target and QEMU evidence separate. Distinguish VERIFIED, INFERRED and UNKNOWN; a search miss is not
  proof that an API or test does not exist. Do not promote PLANNED or NOT_RUN into implemented/verified work.
- Follow translation.md, Required source facts and Translation coverage: check the required source
  closure and core behavior against actual source spans. Focus on register width/order, reset bounds,
  RX/TX bounds and ring handling, IRQ acknowledgement/masking, locking, ownership and error cleanup
  where applicable. Apply the shared targeted-compiler-evidence adaptation; do not demand a full index.
- Follow target-platform-study.md, Target API evidence table: inspect API definitions AND relevant
  calls/analogous drivers, including visibility, public constructors, architecture/cfg gates, ownership
  and execution context. An inaccessible internal constructor alone does not prove the public API unusable.
  Check target-change necessity under target-changes.md before treating a missing extension as an external blocker.
- Follow test-porting.md, Decision questions and Test mapping record: inspect actual stimulus and
  assertions, not filenames or test registration alone. Cite the original assertion locations supporting
  claimed existing coverage; additional assertions belong to NEW_MIGRATION_TEST. Preserve source-only
  exclusions and distinguish model-only observations from migrated-driver execution.

## Evidence locations and Python tool

Use instructions.tool_runtime.evidence_locator on the exact frozen repository and revision you select:
`--repository <absolute checkout> --revision <frozen commit> --path <exact repository-relative file>
--start <first line> --end <last line>`. Use `--symbol <literal text>` instead of the range to locate
candidates, then inspect a suitable range; literal occurrences are not semantic symbol resolution.
The tool returns JSON with revision, content hash, total lines and numbered excerpts or explicit
diagnostics. It never guesses an abbreviated path, changes files or decides whether a claim is correct.
Resolve omitted paths explicitly from the report's repository map or KB; never silently choose a match.
Tool errors and out-of-range references are observations to investigate, not automatic functional verdicts.
Read frozen Markdown reports with line numbers using local read-only commands. For current edited
implementation, inspect the actual files bound by the implementation bundle; the frozen-revision tool
does not contain uncommitted changes. Use document page numbers for non-code evidence where appropriate.

## One actionable report

For each substantive finding include a stable finding ID, affected claim/contract/test ID, the report
path and exact problem lines, the original repository/revision/path and supporting line range (or page),
a minimal relevant excerpt, why the evidence contradicts or fails to establish the claim, practical
impact/trigger, and the smallest corrective action with a verification method. For absent behavior cite
the expected location and requirement; do not fabricate lines for nonexistent code.
Core positive conclusions also need precise evidence locations: summarize which core contracts/APIs/test
assertions you checked and where; reuse existing IDs and evidence references rather than copying whole files
or creating another coverage database. A single location can support multiple clearly identified claims.
Do not block for cosmetic formatting or add optional requirements. Missing/incorrect evidence is substantive
when it prevents checking a required core claim. Preserve correct work and distinguish this from mere styling.
For REWORK return all findings together and select the earliest actually affected allowed prerequisite;
later related fixes remain in the same report. On re-review consider worker counterevidence, retain closed
findings unless relevant evidence changed, and inspect changed claims plus their affected dependencies.
PASS means the assigned stage's requirements are met, not that unexecuted later stages have passed.
