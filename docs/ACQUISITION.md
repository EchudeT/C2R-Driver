# Repository acquisition and minimum evidence closure

Acquisition follows one ordered path after `migration_envelope_freeze=PASS`:

```text
revision_selection
  -> repository_acquisition (STATIC)
  -> evidence_closure (HYBRID proposal + deterministic gate)
  -> environment_recovery
```

## Revision selection

The read-only `revision_selection` Codex job proposes three repository URLs, release tags or full
commits, a selection rule, and quoted compatibility citations. The controller imports the exact
job occurrence, retrieves every citation itself, verifies the excerpt against the returned bytes,
computes its digest and size, resolves each ref, and freezes the resulting full commits. Codex does
not supply trusted hashes, retrieval status, or resolved revisions.

```sh
dpf codex run RUN revision_selection --objective "Select compatible maintained releases"
dpf acquire revision-proposal-import RUN --job-digest SHA256 --job-ordinal N
dpf acquire revisions RUN --proposal-digest SHA256 --proposal-ordinal N
```

Floating branch names such as `main`, `master`, and `HEAD` are rejected. The output
`revision_manifest`, `repository_plan`, and raw `revision_evidence_content` occurrences remain
bound to the frozen migration envelope and exact Codex proposal.

Each resolved evidence record binds a typed maintenance or cross-repository claim to exact
requested refs and the commits resolved by the controller. Retrieved bytes, quoted excerpt, and
ref-to-commit identity are `VERIFIED`; the semantic compatibility assessment remains `INFERRED`.
The controller never promotes excerpt presence into verified compatibility.

## Repository acquisition

`dpf acquire repositories` creates detached, read-only source/target/QEMU baselines and a separate
`work/target-working` branch. `repository_manifest` records origin, requested ref, commit, tree,
clean status, paths, commands and canonical repository-lock SHA256. `source_identity_verification`
must prove the frozen source entry exists and remains within the source checkout.

Interrupted acquisition remains `RUNNING`, appends a `repository_acquisition_attempt`, and can be
retried. Existing partial bare repositories and completed baselines are verified and reused.
Every retry fetches the selected ref again: ref drift fails closed instead of silently adopting a
new commit.

Repository locks prove checkout identity only. They are never controlled materials and cannot
satisfy an evidence facet.

## Evidence proposal boundary

The `evidence_closure` Codex job runs read-only and returns only candidate locators and rationale.
The prompt pack binds `evidence-closure-proposal.schema.json` to this stage automatically. The
response must cover the source, target, QEMU, hardware, test, and tooling domains. It may use only
the concrete facets needed by the frozen migration scope; duplicate facets are rejected.

The controller imports a selected `codex_job_result` by exact digest and occurrence ordinal:

```sh
dpf acquire proposal-import RUN --job-digest SHA256 --job-ordinal N
dpf acquire closure-finalize RUN --proposal-digest SHA256 --proposal-ordinal N
```

Importing creates only auxiliary `evidence_discovery_proposal`. It cannot mark the stage `PASS`.
The deterministic finalizer rechecks envelope/repository bindings, executes each locator, binds
actual bytes and provenance, and submits the complete required bundle.

## Controlled material

Each material has a stable ID, domain and facet, workspace-relative regular-file path, size,
media type, SHA256, license/redistribution policy, acquisition time, original/derived status, and
one typed origin:

- Git content: repository role, pinned commit, exact blob ID, and repository-relative path;
- documented external input: source URL, document version/revision, and controlled workspace path;
- HTTP input: requested/resolved URL, revision, retrieval time, response media type and actual
  bytes. Downloads are bounded, hash-checked when an expected digest is supplied, rejected when
  they are error pages, and published through a temporary file only after validation.

Derived material must reference a controlled original plus its original path and page map.
Indexes and gap descriptions are not material evidence.

## Facet accounting

Every proposed facet has exactly one final disposition, and all six domains must be covered:

- `CONTROLLED`: references one or more validated material IDs;
- `EXPLICIT_GAP`: references one gap with a typed reason, non-empty impact, repair trigger, and
  actual failed retrieval-attempt IDs.

The source driver entry may never be a gap. A proposed gap that resolves to content is rejected so
the proposal must be corrected instead of discarding evidence. The bundle gate rejects duplicate
or missing facets, orphan material/gaps, dangling references, successful attempts cited by a gap,
or missing domains, path escape, dirty or drifted repositories, file/hash/size/blob mismatch, repository locks used as
content, invalid media, HTML error responses, duplicate IDs, and incomplete derived provenance.

The successful stage atomically registers exactly these required artifacts with `PASS`:

- `evidence_closure_plan`;
- `materials_manifest`;
- `evidence_coverage_inventory`;
- `evidence_gap_register`;
- `evidence_retrieval_ledger`.

Only the immutable `materials_manifest` artifact in CAS feeds the knowledge index. There is no
workspace manifest or general-purpose material-registration command. `evidence_gap_register`
remains separately auditable and can satisfy only an explicit “evidence or gap” accounting gate;
it cannot support a behavioral claim.

## Verification

`dpf acquire repositories-verify` rechecks origin, commit, tree and clean state for all baselines.
It does not require the later writable target worktree to remain clean.
