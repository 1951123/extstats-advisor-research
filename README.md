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
`public.census13` before the frozen advisor sees it. The canonical benchmark
path imports audited AreCEL exact observations as an external, snapshot-bound
truth artifact. The advisor's production exact-cardinality path remains an
explicit independent validation and cost path; it is not repeated for every
future canonical run.

The two PostgreSQL roles are intentionally separate:

- `SIMULATED_PRODUCTION_DSN`: stock PostgreSQL 16 and full data; canonical
  benchmark truth is imported from the audited external label artifact;
  production exact truth is used only when explicitly requested;
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

## DMV11 stock PostgreSQL baseline

AreCELearnedYet DMV11 is onboarded as `arecel-dmv11` from the audited shared
data root. The adapter preserves the upstream eleven-column order, ten
`VARCHAR(64)` typmods, nullable columns, `DOUBLE PRECISION` `reg_valid_date`,
and catalog-observed collations. It safely renders only the audited relation,
projection, and source identifiers; string and numeric literals are not
rewritten.

The paper PostgreSQL reproduction must precede DMV11 advisor experimentation:

```sh
extstats-research dataset inspect arecel-dmv11
extstats-research paper-baseline postgres arecel-dmv11 \
  --dsn "$DISPOSABLE_STOCK_POSTGRES_DSN"
```

The baseline uses the audited AreCEL label file as truth, checks a deterministic
representative subset exactly, sets seed `1.0 / 123`, sets all eleven targets
to `10000`, runs exactly one `ANALYZE`, and evaluates all 10,000 queries with
`EXPLAIN (FORMAT JSON)`. It does not create an advisor snapshot, an external
`GroundTruthSet`, candidates, extended statistics, or a patched PostgreSQL
server. The later canonical DMV11 advisor run is expected to use
`authoritative-external-exact` rather than recapturing all exact cardinalities.

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

The DMV11 K=8 transfer uses the same shared P0/P1/P2 engine:

```sh
extstats-research validate full-data-transfer \
  runs/82f277385bf5381a43b0c268 \
  --production-dsn "$DISPOSABLE_STOCK_POSTGRES_DSN"
```

Its immutable `GroundTruthSet` is `authoritative-external-exact` truth from
`sfu-db/AreCELearnedYet`. Validation binds all 10,000 cardinalities to the
audited labels in memory and performs no exact-truth recapture. The transfer
deploys only the frozen five-object Recommendation, then measures P0 (fresh
ordinary statistics), P1 (committed Recommendation), and P2 (the same
post-deployment ordinary statistics after transactional DROP and ROLLBACK).
The patched PostgreSQL planner is not started for this validation.
