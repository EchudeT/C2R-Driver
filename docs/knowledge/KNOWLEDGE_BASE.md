# Local knowledge base

The immutable materials manifest from `evidence_closure` owns corpus provenance. The knowledge
layer indexes those controlled originals; workers cannot register arbitrary files or edit the ledger.
Indexes live under `knowledge/indexes/<corpus-sha256>/`. Reopening a concrete evidence gap produces
new validated material identities and downstream invalidation. There is no separate source-closure
stage, mandatory compiler export or automatic shared-header corpus expansion; see SOURCE_DESIGN.md.

## Infrastructure and target knowledge

Bootstrap establishes index infrastructure, query contracts and the project knowledge Skill. It
records `INDEX_INFRASTRUCTURE_ONLY` and `PENDING_TARGET_STUDY_PROBE_REPLAY`, not semantic quality.
The generator uses the packaged `data/project-kb-skill.md`, binding its digest and corpus identity.

The same analysis worker supplies one Markdown report and an adjacent `report.probes.json`.
New prompts use `{"mode":"focused","probes":[...]}`: only actual gap-driven queries are recorded.
The seven target topic names remain a vocabulary, not a mandatory survey. An empty focused list
records `NO_RETRIEVAL_REQUESTED`, never retrieval success or semantic coverage. Direct inspection
of pinned definitions is valid report evidence; a search miss alone does not trigger corpus repair.
Legacy specifications containing only `probes` retain the original all-seven validation.

Submission freezes the specification; target-study acceptance replays each supplied query and
verifies selected original ranges, then binds `target_knowledge_quality` to the report and corpus.
The stage bundle validator repeats this check. Forged queries, stale originals and stale reports
still fail. This is provenance accounting, not another analysis report or an independent probe agent.

`RETRIEVAL_VALIDATED` proves query results and original-byte identity. It does not prove the worker
read the passages, relevance, complete API coverage, or semantic entailment. The worker self-checks
its explanations against originals and the source task, whether optional reviewers are enabled or not.
Acceptance cases establish only their actual observed scenarios. Optional analysis review can inspect
the same observations but is not needed to execute the mechanical knowledge gate.

```sh
dpf knowledge status RUN
dpf knowledge inventory RUN --domain target
dpf knowledge search RUN --query "actual target symbols" --domain target --limit 20
dpf knowledge show RUN --chunk-id CHUNK_ID
dpf knowledge check-probes RUN --report /path/to/report.md
dpf knowledge rag RUN --query "Which API waits for callbacks?" --domain target
dpf knowledge rebuild RUN
```

Probe replay uses the deterministic BM25 `search` interface. Optional mixed semantic retrieval uses
`rag`; citations selected for replay must appear in the specified BM25 query results. No per-iteration
catalog prefetch or fixed query quota is introduced. Changed premises require checking relevant original evidence; retrieval is optional when its
location is already known. Final worker self-check does not mandate another search.

Weak retrieval requires direct inspection of the pinned target repository. Name missing paths in the
same report and request `--decision rework --repair-stage evidence_closure`. After controlled corpus
repair and rebuilding, repeat the affected query. Empty search results never prove API absence.
Changed originals, vectors or model identities fail rather than silently degrading retrieval.

See [RAG configuration and measured probe](RAG.zh-CN.md) and [alignment/compatibility](../history/SKILL_ALIGNMENT_2026-10-02.zh-CN.md).
