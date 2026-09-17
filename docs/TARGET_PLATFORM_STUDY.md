# Target-platform study gate

This gate implements the mandatory target study from `knowledge-guided-driver-port`. Codex receives
the original English Skill, target-study reference, knowledge contract and target-profile template.
It writes task-local artifacts; deterministic code decides whether they satisfy the gate.

Required inputs are:

- the structured target profile, from which the controller renders the original Markdown template;
- the target API evidence table;
- the closest analogous-driver trace, containing the applicable Skill steps in Skill order;
- the integration/change plan.

Validate them with:

```sh
dpf target-study validate ./run \
  --profile-json ./run/work/target-profile.json \
  --api-table ./run/work/target-api-evidence.json \
  --analogous-trace ./run/work/analogous-driver-trace.json \
  --change-plan ./run/work/target-change-plan.json
```

The controller rechecks the KB and frozen Git baselines, exact target revision, selected artifact
mode, every original line/hash reference, all target-facing API definitions and call sites, and the
applicable analogous path in Skill order. A source-domain or QEMU-domain citation cannot satisfy a
target API field. A non-applicable analogous step is omitted rather than supplied with invented
evidence.

`VERIFIED` and `INFERRED` APIs need target definitions, call sites and analogous-driver evidence.
An `UNKNOWN` API must link to a concrete investigation or target-change ID. Any proposed change to
an existing target file needs the complete necessity record; a broad-impact change cannot pass
without explicit approval. A validation failure is retained as a distinct attempt and leaves the
stage open for evidence repair and a changed resubmission.
