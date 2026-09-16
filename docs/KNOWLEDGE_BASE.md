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

The probe plan follows `schemas/knowledge-probe-plan.schema.json`. It must contain source-entry,
QEMU-model and hardware-or-gap probes plus target probes for registration/lifecycle, resources,
interrupts, concurrency, ownership/errors, Rust safety, analogous implementation, packaging, and
the QEMU runner.

```sh
dpf knowledge bootstrap RUN --probe-plan knowledge-probes.json
dpf knowledge status RUN
dpf knowledge inventory RUN --domain target
dpf knowledge search RUN --query "interrupt acknowledgement" --domain target
dpf knowledge show RUN --chunk-id CHUNK_ID
dpf knowledge rebuild RUN
```

Every passing probe is resolved through `search`, fetched again through `show`, and checked against
the current original-file hash and line range. A failed required probe leaves the stage `RUNNING`.
Missing target originals must be returned to the controlled acquisition/source-closure path before
the index is rebuilt; an empty search is never evidence that the target lacks a capability.

On success DPF fills the upstream `project-kb-skill/SKILL.md` template without placeholders and
records the template hash, corpus digest, generated Skill path, integrity command, search command,
exact retrieval command, and rebuild command. Changing controlled bytes makes
`status/search/show` fail until a validated immutable corpus successor is published and indexed.
