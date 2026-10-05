# extstats-advisor-research

This repository is the reproducible research harness around the frozen
production `extstats-advisor` system. It owns benchmark provenance, the
simulated-production PostgreSQL ETL, run manifests, orchestration, artifact
extraction, and summaries. It does not reimplement candidate generation,
native statistics construction, planner sandboxing, singleton profiling,
ranking, screening, search, utility, Recommendation, or deployment.

The first benchmark is `arecel-census13`. Its audited source is read from
`EXTSTATS_RESEARCH_DATA_ROOT` (default example:
`/home/wqts/benchmark-data`) and is never downloaded or copied into Git.
The dataset is loaded into a disposable stock PostgreSQL 16 relation
`public.census13` before the frozen advisor sees it. Ground truth is acquired
by the advisor's production exact-cardinality path against that relation.

The two PostgreSQL roles are intentionally separate:

- `SIMULATED_PRODUCTION_DSN`: stock PostgreSQL 16, full data and exact truth;
- `ADVISOR_PATCHED_POSTGRES_DSN`: patched PostgreSQL 16.14, fixed advisor
  sample only, for native statistics and planner execution.

Canonical provenance binds the dataset, workload, research harness commit,
production advisor commit, patched PostgreSQL commit, and experiment
parameters. The research working tree must be clean; completed research units
must be committed and pushed to `origin/main` before canonical experiments run.

The previous execution advisor was
`bb4d58d46e734981a4542de4bcf59441d3effb98`; the current execution advisor is
`0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`. Census13's historical transfer
source remains `aa65af49fdbbf7443f8fa7295677724babfddfc5`, while historical
Forest10 and Power7 source runs remain bound to `bb4d58d46e734981a4542de4bcf59441d3effb98`.
The patched PostgreSQL SHA remains
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. Canonical runs reject drift.

The new advisor revision adds authoritative external exact-ground-truth
provenance only. Optimization, search, Recommendation, and deployment
semantics are unchanged. Existing Census13, Forest10, and Power7 evidence is
immutable and is not regenerated.

Install and inspect:

```sh
python -m pip install -e '.[dev]'
extstats-research dataset inspect arecel-census13
extstats-research run --help
```

Canonical smoke defaults are deliberately conservative and are run parameters,
not final paper settings:

```sh
extstats-research run arecel-census13 \
  --production-dsn "$SIMULATED_PRODUCTION_DSN" \
  --planner-dsn "$ADVISOR_PATCHED_POSTGRES_DSN" \
  --output-root runs/
```

No deployment command is part of this harness. Each run writes a deterministic
directory under `runs/<run-id>/`; completed run identities are never
overwritten. DSNs and passwords are never written to manifests or logs.

The optimizer objective belongs to the frozen advisor. When externally managed
production statistics coexist, the result describes the advisor membership
`M*`, not a guaranteed objective for `E_existing ∪ M*`.

The Forest10 K=8 deployment-transfer validation consumes the immutable
canonical source run and does not rerun search or planner sandboxing:

```sh
extstats-research validate full-data-transfer \
  runs/3a8737b6c3184ae2037100df \
  --production-dsn "$DISPOSABLE_STOCK_POSTGRES_DSN"
```

It measures fresh full-data P0, the committed add-only deployment P1, and a
transactional DROP/ROLLBACK counterfactual P2. The P2 operation is research-only
and never changes deployment policy or external statistics ownership.
