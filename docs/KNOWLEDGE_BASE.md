# Local knowledge base

DPF implements the read-only interface required by `open-kernel-driver-port` without introducing an
MCP service. The authoritative inputs are `knowledge/manifests/materials.jsonl` and its controlled
files; `knowledge/index/` is a deterministic, replaceable derivative.

## Extend the controlled corpus

Files must already be inside the task workspace. Register each original or derived file with
provenance, revision and a content hash:

```sh
dpf knowledge add ./run \
  --id target-driver-api \
  --domain target \
  --file ./run/.dpf/worktrees/target-baseline/path/to/api.rs \
  --source-url https://example.invalid/target.git \
  --revision <full-commit> \
  --category driver-api \
  --authority pinned-target-source
```

When a primary source is genuinely unavailable, create an explicit gap rather than inventing a
contract:

```sh
dpf knowledge gap ./run \
  --id hardware-manual-gap \
  --domain hardware \
  --category primary-device-manual \
  --revision not-available \
  --reason "The public manual could not be located after the recorded acquisition attempts."
```

A gap is indexed for traceability but is never positive evidence.

## Readiness and target-quality gate

The probe plan follows `schemas/knowledge-probe-plan.schema.json`. It must contain source-entry,
QEMU-model and hardware-or-gap probes plus target probes for:

- registration/lifecycle;
- resources and MMIO/PIO/DMA;
- interrupts, deferred work and callback context;
- ownership, errors and recovery;
- Rust safety/style rules;
- an analogous driver and framework owner;
- artifact packaging and the QEMU runner.

Run:

```sh
dpf knowledge bootstrap ./run --probe-plan knowledge-probes.json
```

Every passing probe is resolved through `search`, fetched again through `show`, and checked against
the current original-file hash and line range. A failed required probe leaves the stage `RUNNING` so
the caller can directly inspect the pinned target tree, register omitted definitions/call sites,
rebuild and rerun the probes. Empty retrieval is never treated as proof that a target capability is
absent.

On success DPF fills the original `project-kb-skill/SKILL.md` template without placeholders and
records the template hash, corpus hash, generated Skill path and concrete commands:

```sh
dpf knowledge status ./run
dpf knowledge inventory ./run --domain target
dpf knowledge search ./run --query "interrupt acknowledgement" --domain target
dpf knowledge show ./run --chunk-id <chunk-id>
dpf knowledge rebuild ./run
```

Changing a controlled file or manifest makes `status/search/show` fail until the manifest is
updated deliberately and the index is rebuilt.
