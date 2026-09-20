# Local knowledge base

DPF implements the read-only evidence interface required by `open-kernel-driver-port` without an
MCP service. Its authoritative input is the immutable `materials_manifest` artifact produced by
the passed `evidence_closure` stage. The acquisition domain owns provenance and gaps; the knowledge
domain consumes controlled materials and never accepts arbitrary file registration.

`knowledge/indexes/<corpus-sha256>/` is a deterministic, content-addressed derivative. A later
validated source closure may publish one immutable successor corpus artifact. That revision must
retain the acquisition records byte-for-byte and may add only source files proven to be blobs from
the frozen source commit. Candidate construction never overwrites the index of a passed corpus.

## Readiness and target-quality gate

The knowledge-base checkpoint is static. The same worker performs semantic probes as part of
target-platform study: registration/lifecycle, resources, interrupts/concurrency, ownership/errors,
Rust safety, analogous implementation, packaging and QEMU. There is no probe-plan JSON or separate
probe agent, and the old bootstrap/probe-plan CLI has been removed.

```sh
dpf knowledge status RUN
dpf knowledge inventory RUN --domain target
dpf knowledge search RUN --query "interrupt acknowledgement" --domain target
dpf knowledge show RUN --chunk-id CHUNK_ID
dpf knowledge rebuild RUN
```

Search returns compact locators; inspect selected originals to support actual decisions. Each query
checks its corpus/index, not unrelated runtime images; a separate status call before every search is
unnecessary. If originals are missing, inspect the pinned tree and explain the exact paths needed in
the current work report, ending with `DPF_REPAIR_STAGE: evidence_closure` then `DPF_REVIEW: REWORK`.
The controller reopens acquisition and rebuilds downstream evidence. This explicit prerequisite
route replaces the upstream template's manual manifest edits; workers must not modify frozen files
or controller state. An empty search is never evidence that the target lacks a capability.

Source closure retains all compiler dependency identities, but adds full-text search chunks only
for translation units and driver-local headers. Shared headers remain controlled originals and
are reached through selected compiler facts or direct source inspection, not blanket text indexing.

On success DPF fills the upstream `project-kb-skill/SKILL.md` template without placeholders and
records the template hash, corpus digest, generated Skill path, integrity command, search command,
exact retrieval command, and rebuild command. Changing controlled bytes makes
`status/search/show` fail until a validated immutable corpus successor is published and indexed.
