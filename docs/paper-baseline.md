# AreCELearnedYet PostgreSQL baseline

`extstats-research paper-baseline postgres arecel-census13` independently
reproduces the PostgreSQL baseline from the frozen AreCELearnedYet revision
`aa52da7768023270bad884232972e0b77ec6534a`. It is a research-only reference
and does not invoke advisor search, candidate generation, native statistics,
planner sandboxing, or deployment.

The reproduction uses the complete 48,842-row Census13 CSV in a fresh
`public.arecel_paper_census13` relation. The relation schema follows the
upstream `census2postgres` contract: numeric columns are `DOUBLE PRECISION`
and categorical columns are `VARCHAR(64)`. Every column receives statistics
target 10000. The command executes `SELECT setseed(1.0 / 123)`, then the
per-column targets, then exactly one `ANALYZE`.

The workload is the frozen AreCELearnedYet `base:test` workload (10,000
queries). The source query identity and predicates are retained; the upstream
`aggregate=False` form is rendered as `SELECT * FROM
public.arecel_paper_census13 WHERE ...` for `EXPLAIN (FORMAT JSON)`. Before any
q-error is calculated, every query is executed as an exact full-table count and
must match the frozen canonical GroundTruthSet. A mismatch stops the run and
reports the query ID and SQL.

Artifacts are written under `paper-baselines/arecel-census13/` and contain no
DSN or credentials. Their names distinguish `paper_pg_reproduction` from the
existing `advisor_sandbox_baseline` and `advisor_final`. The published paper
references are external comparison points only: p50 1.40, p95 18.6, p99 58.0,
and maximum 1635. Percentiles use linear interpolation over `n - 1`, matching
the upstream NumPy default.

The optimizer evaluated advisor membership `M*`, not necessarily
`E_existing ∪ M*`. Therefore the reproduced paper objective is not a guarantee
for a combined production state when externally managed extended statistics
coexist. This baseline does not reconcile or remove such objects.
