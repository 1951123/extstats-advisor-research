# AreCEL Power7 PostgreSQL paper baseline

This is the independent PostgreSQL reproduction for the third AreCEL real-world
dataset in the cross-dataset study. It must pass before any Power7 advisor
experimentation begins.

The reproduction uses the audited `base:test` workload and labels, a fresh
stock PostgreSQL 16.14 instance, `SELECT setseed(1.0 / 123)`, statistics target
`10000` on all seven columns, and exactly one `ANALYZE public.power7`. It does
not run the advisor pipeline, patched PostgreSQL, candidate generation,
sampling, or deployment.

Run it with:

```sh
extstats-research dataset inspect arecel-power7
extstats-research paper-baseline postgres arecel-power7 \
  --dsn "$DISPOSABLE_STOCK_POSTGRES_DSN"
```

The paper PostgreSQL reference is recorded as `p50 / p95 / p99 / max`:
`1.06 / 15.0 / 235 / 200000`. These are reproduction references, not
optimization targets. The compact artifacts are written under
`paper-baselines/arecel-power7/`.

The single canonical Power7 advisor experiment uses sample rows `10000`,
sample seed `42`, statistics target `100`, candidate limit `8`, and a `300`
second search cap. It requires both stock and patched PostgreSQL DSNs and
writes ignored bulk run state under `runs/`; only compact evidence under
`experiments/arecel-power7/canonical-k8/` is tracked. It does not deploy, run
K12/K16, or onboard another dataset.

```sh
extstats-research run arecel-power7 \
  --production-dsn "$DISPOSABLE_STOCK_POSTGRES_DSN" \
  --planner-dsn "$DISPOSABLE_PATCHED_POSTGRES_DSN" \
  --advisor-root /home/wqts/projects/extstats-advisor \
  --patched-postgres-root /home/wqts/projects/postgresql-src-pgextadv
```
