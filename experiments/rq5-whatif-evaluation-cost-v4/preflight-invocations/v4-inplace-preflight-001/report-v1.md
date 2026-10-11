# RQ5 v4 limited live correctness preflight

- Classification: `integration-readiness-only`
- Status: **PASS**
- Invocation: `v4-inplace-preflight-001`

The preflight used the pinned stock and patched PostgreSQL 16.14 laboratory instances and historical Advisor checkout. It used the first two frozen Analysis A singleton configurations and first five frozen queries, and did not execute the formal 20-configuration campaign.

## Physical

One experiment-owned `rq5wc_v4_preflight_001` database was provisioned from the protected full-data `extstats_stock` template before the primary lifecycle. The adapter verified 581,012 rows and no unexpected extended-statistics objects. Both configurations used the same relation OID (16501) in the same database. Each completed CREATE/ALTER, one native ANALYZE, five JSON EXPLAIN calls, DROP, and post-drop absence verification. Both EXPLAIN groups were 5/5 successful. No clone was created between configurations. Ordinary-statistics fingerprints were recorded and allowed to evolve.

## Catalogless

Runtime sample acquisition and snapshot validation established the frozen 30,000-row sample. Native materialization processed all 57 candidates with one ANALYZE; sandbox preparation used one ANALYZE; per-configuration ANALYZE was zero. Both singleton activations matched their declared candidate order and virtual OIDs. The two configurations completed 10/10 planner evaluations.

## Cleanup

The v4 database was absent after adapter cleanup. The protected `extstats_stock` relation remained at 581,012 rows with zero experiment-named statistics. Patched internal and managed sandbox namespaces had zero residual relations. Preflight timing values are not scientific cost evidence.

This establishes readiness for the separately authorized formal v4 Analysis A; it does not establish speedup, amortization, or estimator equivalence.
