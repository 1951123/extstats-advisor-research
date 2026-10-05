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
