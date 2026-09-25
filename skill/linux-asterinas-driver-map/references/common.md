# Common method: turn the map into evidence for the current task

## Establish applicability first

In the existing platform study, record the complete Linux/Asterinas commits, CPU architecture, build configuration, device ID/variant, bus, and actual data path. The baseline for this map is in the [entry point](../SKILL.md). Devices sharing a PCI bus do not necessarily share registers, reset behavior, or data paths.

Open the current driver's registration entry, probe, operations table, and shared core first; Linux headers, macros, Kconfig/Makefile files, and architecture implementations may change semantics. Follow the call chain actually used by the current driver; do not traverse the entire kernel. On Asterinas, inspect the trait definition first, then one real caller and one original implementation; do not copy Linux callback names.

## Read originals and line numbers

The links for the bound revisions are navigable in a browser. When objects are local, prefer reading Git objects to avoid network cost and dirty-workspace contamination:

```sh
git -C "$repo" show "$revision:$relative_path" | nl -ba | sed -n '30,80p'
```

`repo`, `revision`, and `relative_path` come from the currently frozen inventory; use a full revision. When an existing review locator is available, the reviewer may call:

```sh
python -m driver_port_factory.review_evidence --repository "$repo" --revision "$revision" --path "$relative_path" --start 30 --end 80
```

This tool is provided by driver-port-factory; installing it is not required when this material is used independently. `LOCATED` only means that the original text was found; it does not establish the conclusion. Symbol search may hit declarations, comments, or multiple same-named implementations, so continue into the function body and caller context.

## Merge into existing analysis records

For every important mapping retain: behavior or issue; Linux commit, path, and line numbers; the Asterinas definition and call sites; hardware basis when needed; differences and Rust invariants; planned assertions; and a `VERIFIED`/`INFERRED`/`UNKNOWN` state. Record the entry name and reference revision actually used; do not create a duplicate report.

Prioritize checks for: when the device may become active after initialization; who owns resources after failure; whether buffers may be released before completion; IRQ/shutdown races; whether reset restores all required state; and whether errors reach the upper layer. A TODO, unwrap, or compilable similar driver in target code does not automatically prove these conditions.

## Integration and validation

Trace crate dependencies, component registration, initialization order, architecture cfg, and packaging entry points from current original source; add integration only within the existing task scope. A new Cargo dependency alone cannot prove that a driver executes. Inspect the current tree's `Cargo.toml`, `Components.toml`, component-initialization macros, and call chains, and verify path existence.

Turn checks into the existing public test plan by risk: normal path + key boundary + failed rollback. A plan is not an execution result; a QEMU device-registration test is not a register/data-path assertion; running an original driver is not running the migration artifact. Keep the existing workflow's analysis review and final review; do not add a review node for this map.

The reviewer checks the selected types and related mechanisms, cites the report location and original-source location for each issue, and explains trigger, impact, and repair validation. It reports all findings together after completing the delegated scope. Not loading unrelated entries is not itself a defect.

## Workflow selection and reuse

| Stage | Default selection |
|---|---|
| Evidence closure, project KB construction | Platform entries covering the current gap; add only the needed originals to the evidence closure |
| Source analysis | Linux entries for the bus, device type, and mechanisms actually used |
| Target-platform analysis | Corresponding Asterinas entries; continue tracing by actual API/architecture |
| Migration contracts | Evidence from both sides + the relevant comparison entry |
| Analysis review | Check that the selected type covers core issues; open originals as needed, without requiring the whole map |
| Implementation, repair, final review | Entries and existing evidence affected by the current change and its mechanisms |

The execution AI chooses entries from the confirmed device and actual source, not by mechanically classifying the driver name. When using this map helps later reuse, the following small block may be merged into an existing analysis report or handoff record; reading proof is not required, and no new file or mandatory JSON field is needed:

```text
Map selection: bus PCI; type network; mechanisms PIO, IRQ; host DMA not applicable (cite source-side data-path evidence)
Reference entry: linux-asterinas-driver-map/SKILL.md (relative to the current skill_root)
Selected material: references/linux/{pci,network,io,irq}.md; references/asterinas/{pci,network,io,irq}.md
Comparison material: references/{pci,network,io,irq}.md (read by issue during contract or review)
Verified evidence: reuse existing API/contract IDs and state the corresponding report path/line
Applicable baseline: current frozen complete commits for both sides, architecture, and relevant configuration
Open questions: record only unresolved questions that affect the current task
```

Braces are a human-readable shorthand; expand them into individual real paths before reading. Pass paths and existing record locations rather than copying entry text or source. Reuse the selection from existing records; when context is missing, reopen the original for the current question. The map does not replace tool permissions or the frozen evidence scope.

When source/target revisions, architecture, relevant configuration, contracts, or implementation change, inspect affected mappings and caller dependencies and update the original record; do not reuse an old conclusion merely because this map did not change. When the map is revised, check whether related prompts affect existing judgments; if a path is invalid, relocate it from the entry point. Reusing an existing review still follows the controller's evidence/rule bindings and does not bypass invalid checks.

## File priority and path usage

The C table in each platform entry gives concrete files, key symbols, fixed-revision line ranges, and questions to answer; the Q table gives original files and conditions for opening them. A Q link identifies a navigation target only; it does not claim that every implementation in that file was reviewed. Entries with independent branches, such as input/serial, require reading only the selected branch's core files.

Bind investigation results to the existing contract/API table: actual entry → synchronous call or callback binding → asynchronous boundary → completion/cleanup, with related state, ownership, and context. An original path is a starting point; a target driver interface with the same name does not inherit every conclusion from the example.

Stop expanding into unrelated kernel files after reaching the entry's reading endpoint and handling task-relevant unknowns through the existing workflow. The reviewer checks evidence for the actual core chain and failure/shutdown paths, not “how many files were read,” and does not require untriggered Q files.
