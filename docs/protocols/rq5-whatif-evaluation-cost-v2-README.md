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

## Adapter boundary

`rq5_whatif_cost.py` owns the manifest, event, failure, and cost contracts.
`PhysicalCostAdapter` and `CataloglessCostAdapter` are explicit dependency
injection boundaries. A later live task may implement them using the existing
research `rq4_physical` helpers and Advisor `materialize_native_stats` /
`PostgresPlannerSession` interfaces. No changes to `extstats-advisor` or
PostgreSQL are required or permitted.
