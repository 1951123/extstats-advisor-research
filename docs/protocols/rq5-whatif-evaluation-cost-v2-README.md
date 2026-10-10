# RQ5 What-if Cost v2 Harness

This is a research-only, offline-first implementation of
`rq5-whatif-evaluation-cost-v2`. It does not run PostgreSQL by default and does
not contain measured timing observations.

## Offline manifest generation

From the research repository, using the existing workload source:

```text
.venv/bin/rq5-whatif-cost manifest \
  --research-root /path/to/extstats-advisor-research \
  --workload runs/_forest10_workload.json \
  --output-dir experiments/rq5-whatif-evaluation-cost-v2/manifests
```

This writes separate Analysis A and Analysis B manifests. The command checks the
tracked Forest10 candidate artifacts, records the 128 deterministic SQL
digests, and binds each manifest to the producer commit and protocol bytes.
Existing manifest paths are never overwritten.

Validate without any database connection:

```text
.venv/bin/rq5-whatif-cost validate-manifest \
  experiments/rq5-whatif-evaluation-cost-v2/manifests/analysis-a-manifest-v1.json \
  --research-root /path/to/extstats-advisor-research \
  --verify-sources
```

## Integration-preflight plan

The implementation can produce a non-executing scope plan:

```text
.venv/bin/rq5-whatif-cost integration-preflight-plan \
  experiments/rq5-whatif-evaluation-cost-v2/manifests/analysis-a-manifest-v1.json \
  --output /tmp/rq5-whatif-integration-preflight-plan.json
```

This plan deliberately reports `not-executed` and zero live connections. A
future live preflight requires explicit stock/patched DSNs, pinned binary
verification, isolated databases, and concrete adapters. It is not invoked by
offline tests or manifest commands.

## Live adapter implementation

The research-owned adapters are in
`src/extstats_advisor_research/rq5_whatif_cost_postgres.py`:

- `StockPhysicalCostAdapter` clones an explicitly named stock template for
  each configuration, creates the declared statistics in order, runs one
  `ANALYZE`, verifies catalog definitions/OIDs/payload states and ordinary
  statistics, replays `EXPLAIN (FORMAT JSON)`, then drops only its owned clone.
- `CataloglessWhatIfCostAdapter` loads the sealed snapshot and candidate
  universe, calls Advisor's `materialize_native_stats` once, registers the
  resulting repository through `PostgresPlannerSession`, activates the exact
  declared order, and replays the same query IDs without per-configuration
  `ANALYZE`.

Both adapters write non-overwriting audit records below the invocation output
directory. Query audits distinguish attempted, successful, and failed planner
calls. Native payload state is recorded as `present` or `native-null`; a NULL
payload is not silently treated as a missing object.

Live execution requires all of the following:

- explicit host, port, and database in stock, stock-admin, and patched DSNs;
- a clean binary identity JSON for stock SHA
  `0d1c00c624fa7367d4a895f44381887757289682` and patched SHA
  `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`;
- the historical Advisor checkout at
  `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`;
- a sealed snapshot, matching candidate-universe artifact, and workload
  source whose SQL digests match the manifest;
- a source/template database that is not named with the adapter-owned
  `rq5wc_` prefix; and
- an output directory that has not been used by an earlier invocation.

The adapter never falls back to a local socket or default database. It refuses
to drop the source database and only drops database names that it generated,
revalidated, and confirmed to be owned by the current role.

## Tiny live correctness preflight

The preflight command is disabled unless the explicit opt-in flag is supplied:

```text
.venv/bin/rq5-whatif-cost integration-preflight \
  /path/to/tiny-fixture-manifest.json \
  --advisor-root /path/to/extstats-advisor \
  --snapshot /path/to/tiny-fixture-snapshot \
  --candidate-universe /path/to/tiny-fixture-candidates.json \
  --workload /path/to/tiny-fixture-workload.json \
  --stock-dsn 'host=127.0.0.1 port=55432 dbname=fixture_template' \
  --stock-admin-dsn 'host=127.0.0.1 port=55432 dbname=postgres' \
  --patched-dsn 'host=127.0.0.1 port=55433 dbname=fixture_template' \
  --stock-identity /path/to/stock-identity.json \
  --patched-identity /path/to/patched-identity.json \
  --output-dir /tmp/rq5-whatif-preflight \
  --output /tmp/rq5-whatif-preflight/result.json \
  --run-id tiny-fixture-001 \
  --enable-live-preflight
```

The fixture manifest must contain at most eight candidates, two configurations,
and eight queries. The two explicitly supplied databases must already contain
the same tiny relation; the command does not create or mutate a source/template
database. It checks catalog identity, physical OIDs and payload states,
hypothetical activation order, EXPLAIN JSON shape, and cleanup. Its result is
`integration-readiness-only`, never a timing observation.

This command is not invoked by pytest or CI. Formal Analysis A/B execution
remains separately unauthorized until the live preflight has passed and a
formal timing invocation is approved.

## Adapter boundary

`rq5_whatif_cost.py` owns the manifest, event, failure, and cost contracts.
`PhysicalCostAdapter` and `CataloglessCostAdapter` are explicit dependency
injection boundaries. A later live task may implement them using the existing
research `rq4_physical` helpers and Advisor `materialize_native_stats` /
`PostgresPlannerSession` interfaces. No changes to `extstats-advisor` or
PostgreSQL are required or permitted.
