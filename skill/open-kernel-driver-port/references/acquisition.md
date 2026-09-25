# Evidence and Source Acquisition

## Optional platform source navigation

Only when a Linux-to-Asterinas evidence gap concerns which original files to collect, you may consult [the platform-separated source map](../../linux-asterinas-driver-map/SKILL.md) to locate candidates. Otherwise no navigation reading is needed here. This optional guide neither closes evidence gaps nor adds a reading or reporting requirement.

Acquire the smallest reproducible corpus that can support migration. Prefer official upstream sources and primary technical documentation.

## Version selection

Choose exact source-platform, target-platform, and QEMU revisions after driver identity confirmation. Prefer mutually compatible maintained releases. If a project has no suitable release, pin a full commit hash. Record the selection rule and compatibility evidence; never use a floating branch as the frozen baseline.

This is an execution decision, not a second identity questionnaire. Select and pin revisions autonomously when doing so does not alter the confirmed scope. Ask the user only if competing revisions have a material scope, ABI, licensing, or runnability consequence. If the user has already chosen a revision or approved a “latest compatible” policy, persist that answer and do not ask again. If later evidence invalidates it, report the specific new conflict and issue one consolidated replacement question.

Before fetching large artifacts, inspect local caches and existing checkouts. Reuse them only after verifying origin, revision, cleanliness as relevant, and content identity. If the workspace filesystem cannot safely represent an upstream tree, use a compatible local filesystem/container volume while keeping all durable outputs in the project-defined local area; record the arrangement.

## Minimum acquisition closure

Acquire and preserve as read-only inputs:

- source driver entry and recursively required shared cores, headers, generated/configuration inputs, build metadata, and framework contracts;
- original source tests and test framework documentation related to the driver or device class;
- a pinned target OS source tree, or a complete task-relevant target closure when a full checkout is legally or technically impractical, covering extension points, framework owners, analogous drivers, API definitions and call sites, lifecycle, artifact packaging/QEMU entry points, coding rules, Rust safety policy, and test conventions;
- target release assets, official development containers/SDKs, runnable images, CI workflows, launch scripts, image/component manifests, and artifact-injection mechanisms relevant to the pinned revision;
- QEMU device-model source and documentation for the confirmed device/bus plus relevant qtest/QMP or backend behavior;
- primary hardware manuals, register descriptions, public errata, and bus specifications when legally accessible;
- toolchain, packaging, release, and runtime documentation needed to reproduce source baselines, prepare target artifacts, and execute QEMU runs.

Do not indiscriminately mirror websites or download unrelated platform trees. Expand the corpus only when a named contract, dependency, test, or failure requires it.

## Retrieval and provenance

Use read-only network operations. Repository clones/fetches and HTTP downloads are allowed only in this acquisition phase after the identity gate; remote writes are forbidden. Prefer a shallow fetch of the selected tag/commit when history is unnecessary, but retain enough metadata to verify the exact revision. Do not place upstream `.git` metadata inside the enclosing research repository's tracked files.

For every controlled item, add one JSON object to `knowledge/manifests/materials.jsonl` with:

```json
{"id":"stable-id","domain":"hardware|source|target|qemu|tooling|test","path":"knowledge/raw/...","source_url":"https://...","revision":"tag-or-full-commit-or-document-version","acquired_at":"RFC3339 timestamp","license":"SPDX id or review note","redistribution":"allowed|restricted|unknown","sha256":"lowercase hex","original":true,"notes":"selection and scope"}
```

Paths are workspace-relative and must not escape the workspace. A checked-out repository may be represented by a locked repository manifest plus per-file hashes for the controlled closure; do not hash only a mutable directory name. Derived PDF text and generated source facts get separate records linking back to originals with optional `derived_from`, `original_path`, and `page_map` fields. Indexes are disposable outputs and are not inputs in the materials manifest.

## Download safety and failure handling

- Check response type and expected size before accepting large downloads.
- Store partial downloads separately; publish into the controlled corpus only after revision and hash verification.
- Treat HTML error pages, generated summaries, search snippets, mirrors, and unauthenticated reposts as non-authoritative until corroborated.
- Record unavailable, access-restricted, conflicting, or license-uncertain sources. Do not evade access controls.
- Never execute build scripts or binaries merely because they were downloaded. Execution belongs to a later frozen plan.

Finish with a coverage inventory across hardware, source, target, QEMU, tests, and tooling. Missing categories become explicit evidence gaps, not assumed knowledge.

Absence of a conventional target build guide is not a completed acquisition failure. Treat versioned CI, release manifests, container definitions, task runners, image scripts, and prior reproducible project runs as candidate operational specifications, then route environment work through `environment-recovery.md`.

Do not acquire only target documentation summaries or isolated API snippets. The Agent needs enough pinned target source to search definitions, implementations, call sites, analogous drivers, manifests, feature/configuration paths, cleanup/error behavior, and runtime inclusion. Record the target source root and revision explicitly in the handoff.
