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

Frozen identities are enforced for `extstats-advisor` SHA
`524a17d4a9dea1a436bb4e30aadcc77e2bda4edd` and patched PostgreSQL SHA
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. Canonical runs reject drift.

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
