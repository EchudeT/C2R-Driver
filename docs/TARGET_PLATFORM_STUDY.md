# Target-platform study gate

This gate implements the mandatory target study from `knowledge-guided-driver-port`. Codex receives
the original English Skill, target-study reference, knowledge contract and target-profile template.
It writes task-local artifacts; deterministic code decides whether they satisfy the gate.

Required inputs are:

- the filled original target-profile Markdown template;
- a structured profile conforming to `schemas/target-profile.schema.json`;
- the API table in `schemas/target-api-evidence.schema.json`;
- the ordered analogous-driver trace in `schemas/analogous-driver-trace.schema.json`;
- the integration/change plan in `schemas/target-change-plan.schema.json`.

Validate them with:

```sh
dpf target-study validate ./run \
  --profile-json ./run/work/target-profile.json \
  --profile-markdown ./run/work/target-platform-profile.md \
  --api-table ./run/work/target-api-evidence.json \
  --analogous-trace ./run/work/analogous-driver-trace.json \
  --change-plan ./run/work/target-change-plan.json
```

The controller rechecks the KB and frozen Git baselines, exact target revision, selected artifact
mode, every original line/hash reference, all target-facing API definitions and call sites, and the
full ordered path from selection through cleanup and artifact/QEMU inclusion. A source-domain or
QEMU-domain citation cannot satisfy a target API field.

`VERIFIED` and `INFERRED` APIs need target definitions, call sites and analogous-driver evidence.
An `UNKNOWN` API must link to a concrete investigation or target-change ID. Any proposed change to
an existing target file needs the complete necessity record; a broad-impact change cannot pass
without explicit approval. A validation failure is retained as a distinct attempt and leaves the
stage open for evidence repair and a changed resubmission.
