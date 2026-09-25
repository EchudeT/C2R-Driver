# Repository acquisition and minimum evidence closure

Acquisition follows one ordered path after `migration_envelope_freeze=PASS`:

```text
repository_acquisition (one worker selection + one retained download)
  -> evidence_closure (HYBRID proposal + deterministic gate)
  -> environment_recovery
```

## Revision selection

The `repository_acquisition` job returns only three repository choices:
`{"repositories":[{"role":"source","url":"…","ref":"…"}, …]}`.
Each ref must be a release tag or full commit. The controller binds the exact job
occurrence to the frozen migration envelope, downloads each repository once and resolves its local ref, and records
the command evidence and immutable commits in `revision_manifest` and `repository_plan`.
Annotated tags are peeled to commits, including refs written as `refs/tags/vX.Y.Z`.
Floating branch names such as `main`, `master`, and `HEAD` are rejected.

There is one input format. Legacy revision proposals and old session records are not
imported. Version compatibility is investigated through source evidence and exercised
in the environment stage; a citation table is not a prerequisite for pinning repositories.
When workflow contracts change, start a fresh run instead of migrating frozen state.

```sh
dpf codex run RUN repository_acquisition --objective "Select compatible maintained releases"
dpf acquire revision-proposal-import RUN --job-digest SHA256 --job-ordinal N
dpf acquire repositories RUN
```

## Repository acquisition

`dpf acquire repositories` creates detached, read-only source/target/QEMU baselines and a separate
`work/target-working` branch. `repository_manifest` records origin, requested ref, commit, tree,
clean status, paths, commands and canonical repository-lock SHA256. `source_identity_verification`
must prove the frozen source entry exists and remains within the source checkout.

Interrupted acquisition remains `RUNNING`, appends a `repository_acquisition_attempt`, and can be
retried. Existing partial bare repositories and completed baselines are verified and reused.
Successful downloads and their command receipts are reused on restart; tags are peeled locally
and the downloaded commit is frozen. No temporary membership-probe repository exists. A Git fetch
failure pauses acquisition with the selection and successful downloads retained; resuming retries
the unfinished download, not the model selection. This does not promise byte-level resume of a
partially transferred Git pack.

Repository locks prove checkout identity only. They are never controlled materials and cannot
satisfy an evidence facet.

## Evidence proposal boundary

The `evidence_closure` Codex job returns only candidate locators and rationale.
Its response contract is stated directly in the editable prompt pack. The response must cover the
source, target, QEMU, hardware, test, and tooling domains. It may use only
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

An official original may be selected with `url`, `publisher_url` and a concise `basis` explaining
the worker's source assessment; no mirror is required. Mirror inputs retain corroboration.
Publisher assessment remains a worker judgment, not proof inferred from a hostname. The tool binds
the actual response host and content. Successful HTTP responses are reused from CAS across binding,
finalization and restart rather than downloaded again.

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
# Reusing supplied upstream sources

Pass `--baseline-repository /absolute/upstream/repository` to `port run` or `init`
(repeat for Linux, target and QEMU). Paths are frozen in the new run configuration;
resume uses that configuration. Supply upstream repositories only, not old migration
workspaces. The worker can inspect these sources and select available versions.

The controller matches the selected origin, imports committed Git objects locally,
and performs its ordinary upstream fetch to confirm the chosen revision. Dirty
worktree content is not copied, the supplied repository is not modified, and the new
run does not depend on shared Git alternates. Missing/mismatched caches fall back to
normal acquisition. This reduces redundant transfer; it is not an offline mode.

## Importing existing local Linux, Asterinas, or QEMU repositories

When the required repositories already exist on the same machine, pass role-specific
paths to avoid downloading them again:

```sh
dpf port run RUN \
  --local-source-repository /absolute/path/to/linux \
  --local-target-repository /absolute/path/to/asterinas \
  --local-qemu-repository /absolute/path/to/qemu
```

The equivalent `dpf init` options are available when creating a project directly. Each
path is resolved and frozen in the project configuration. For a selected ref, the
controller resolves the commit with `git -C PATH rev-parse REF^{commit}`, imports only
that commit's Git objects into the managed bare repository, and creates the normal
detached baseline worktree from those objects. It never copies files from the supplied
worktree and never modifies the supplied repository, so uncommitted edits remain outside
the run. A bare repository is accepted as well as a normal checkout.

Role-specific local paths are strict offline sources: if the selected ref is absent, the
stage fails with a local-revision error instead of silently trying `origin`, a cache, or
another network source. The local fetch command and its output remain in the repository
manifest, and the import receipt is reused after a controller restart. Omit a role's
local option to retain the ordinary remote or cache acquisition behavior for that role.
