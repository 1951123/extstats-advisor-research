# AreCELearnedYet Forest10 PostgreSQL baseline

The Forest10 paper PostgreSQL reproduction precedes any advisor experiment. This
adapter does not capture an AdvisorSnapshot, derive candidates, materialize
native statistics, profile singletons, search, build a Recommendation, or deploy
anything.

## Frozen source

Forest10 is bound to `sfu-db/AreCELearnedYet` commit
`aa52da7768023270bad884232972e0b77ec6534a` and the audited shared archive SHA256
`5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79`. The local
audit under `EXTSTATS_RESEARCH_DATA_ROOT/arecel` freezes the CSV, workload
pickle, label pickle, and canonical workload hashes. The label pickle, together
with the canonical source-index mapping, is authoritative truth for the full
10,000-query test workload; this avoids 10,000 full-table truth scans in future
cross-dataset experiments.

The reproduction still performs deterministic live exact-count spot checks.
They cover beginning/middle/end source order, predicate arities, smallest and
largest labeled cardinalities, and the observed predicate operators. Any
mismatch fails the reproduction rather than changing labels.

## Physical contract

The upstream `forest2postgres` DDL leaves identifiers unquoted. PostgreSQL
therefore stores all ten columns as lowercase catalog identifiers, all
`DOUBLE PRECISION`, and nullable. The research relation is `public.forest10`;
no quoted mixed-case columns are created.

The adapter safely replaces only the audited source relation identity and the
aggregate projection. It converts `COUNT(*)` to `SELECT *` for planner
estimation, while preserving predicate order, operators, and numeric literals.

## Reproduction protocol

```text
extstats-research dataset inspect arecel-forest10
extstats-research paper-baseline postgres arecel-forest10 \
    --dsn "$DISPOSABLE_STOCK_POSTGRES_DSN"
```

The live baseline loads all 581,012 rows into stock PostgreSQL 16.14, verifies
the physical schema and zero extended statistics, executes `setseed(1.0 / 123)`,
sets every column's statistics target to 10,000, runs exactly one `ANALYZE`,
and executes 10,000 `EXPLAIN (FORMAT JSON)` statements. It never uses
`EXPLAIN ANALYZE` or an advisor pipeline.

Q-error is supplied by the frozen advisor public `QErrorLoss` under the
`qerror-cardinality-floor-1-v1` contract. Percentiles use the research
linear-interpolation `(n - 1)` method; paper reference values are recorded as
external comparison references, not optimization targets.
