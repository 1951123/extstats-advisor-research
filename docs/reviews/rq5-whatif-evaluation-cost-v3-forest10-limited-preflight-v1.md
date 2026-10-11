# RQ5 What-if Cost v3 Forest10 Limited Live Preflight

Status: **PASS — integration-readiness-only**
Invocation: `forest10-v3-preflight-003`
Producer: `0c4c37602be95f3a482f310c28c5bdcb14f52e20`

This was a bounded correctness preflight, not a timing run. It used one
`A-singleton-01` configuration and the first five queries of the frozen v3
Analysis A query subset. No formal Analysis A/B configuration sequence,
amortization result, speedup, Advisor search, truth acquisition, or manuscript
result was produced.

## Frozen bindings

- Stock PostgreSQL: 16.14, source `0d1c00c624fa7367d4a895f44381887757289682`.
- Patched PostgreSQL: 16.14, source `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`.
- Historical Advisor: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.
- Forest10 CSV: SHA256 `3484f3037f890f6bae46ae51fb387ee883351119667139c5894654276789a73c`; 581,012 rows.
- Fixed sample: 30,000 rows, `sha256-row-rank-v1`, seed 42; index digest `d3ae114d5b18d71bd28d95199ff32989a3623d3a29ff7f8ef59ab82d79b99a0b`.
- Sealed sample digest: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- Candidate universe: 57 candidates; semantic digest `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`.
- Selected candidate: `cand_1537a6ae8ef67c379c8d0fa0` (MCV, `aspect`, `hillshade_9am`, target 100).
- Query IDs, in order: `arecel_forest10_test_000000`, `arecel_forest10_test_000078`, `arecel_forest10_test_000157`, `arecel_forest10_test_000236`, `arecel_forest10_test_000314`.

The formal v3 Analysis A and B manifests were validated and remained
unchanged. Their byte SHA256 values are respectively
`bbfd1e8d391fd8dfc35c4369911bf03a79a70e6bb64f21c8e3a7c48f87bb7671` and
`4977107506c07c464ae75cbd3c0ff4c503edc9224f0348dfbc1c97562efd55af`.

## Physical arm

The Stock lab used the explicit private socket/port `55432` and database
`extstats_stock`. The source relation had 581,012 rows, `reltuples=581012`,
and `relpages=7792`. The disposable clone was created from this full-data
source and was removed after the configuration.

The singleton created statistics object OID `16500` on relation OID `16496`.
Its observed order was the declared singleton order, kind `{m}`, target 100,
and native payload state `present` (2,480 bytes; SHA256
`99dff6d82fe60c0e4c0ce6fb4e71251257498ddedd1e849dfa972369802b5ca7`). The
ordinary-statistics fingerprint was
`1d369896938a2984cf8e1aaf74a0761415292b0a3624c69bbbce3a66bd8d8af9`.

There was one configuration ANALYZE and five attempted/successful ordinary
`EXPLAIN (FORMAT JSON)` calls. Root Plan Rows were:

`[5984, 3865, 143742, 17777, 39]`

The PostgreSQL source contract records the nominal extended-statistics sample
rule as `300 * max_statistics_target = 30,000` at target 100. The standard
catalog does not expose the actual sampled-row count, so the preflight does
not claim that the physical ANALYZE sampled exactly 30,000 rows.

## Catalogless arm

The patched lab used explicit private socket/port `55433` and database
`extstats_stock`. Native materialization processed all 57 candidates once,
with one materialization ANALYZE. The sandbox then performed one preparation
ANALYZE, established 30,000 sample rows and population metadata 581,012, and
verified 57 present payloads with zero absent payloads. The repository digest
was `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.

The backend-local activation of the singleton observed virtual OID
`3563580435` in the declared order. There were zero per-configuration
Catalogless ANALYZE calls and five attempted/successful EXPLAIN calls. Root
Plan Rows were:

`[6779, 3731, 142929, 17477, 39]`

The selected repository payload was present (2,248 bytes; SHA256
`db4ce0d5d802ed927b41b9f3f555f2828ceb40b9450e04657bf9b820ce85bc93`). Its
ordinary-statistics fingerprint was
`9c880f2bb54fff888b0afd1c9f30b4ea7f17ecaf56a74d87c50b97b85e6d0f55`.
Different physical/catalogless payloads and Plan Rows are permitted by v3
and are not treated as a correctness failure.

## Query, settings, and cleanup checks

Both arms replayed the same five query IDs and SQL digests in the same order;
all ten EXPLAIN calls returned parseable JSON with root Plan Rows. The checked
planner settings included `default_statistics_target=100`,
`random_page_cost=4`, `cpu_tuple_cost=0.01`, sequential/index/bitmap scans
enabled, and `jit=on`. Patched replay settings were restored to
`pg_extstats.frozen_sample_mode=off`, empty relation, and
`pg_extstats.frozen_totalrows=-1` after cleanup.

Adapter cleanup reported zero errors. Independent read-only checks found no
`rq5wc_*` clone databases, no patched sandbox relations, and no experiment
statistics objects left in the Stock source. The two `extstats_stock`
databases created for this preflight were dropped; `extstats_lab` was
preserved. Both lab clusters were stopped and ports 55432/55433 were no
longer listening.

## Timing readiness decision

The preflight establishes execution correctness for the declared limited
paths, but it does not establish formal timing readiness. Physical clone,
statistics DDL, ANALYZE, EXPLAIN, DROP, and clone destruction map to the v3
stage model. Catalogless materialization, sandbox preparation, registration,
activation, EXPLAIN, deactivation, and cleanup also executed as expected.

The remaining blocker is the primary lifecycle boundary: deterministic
full-population-to-sealed-30,000-row sample extraction and snapshot
preparation occur before the timed adapter path. The current `prepare_sample`
stage validates the sealed snapshot but does not time that mandatory
acquisition/preparation work. It must be incorporated into the declared
primary timing boundary, or explicitly resolved by a new versioned protocol
decision, before formal Analysis A timing.

Therefore:

- Limited v3 correctness preflight: **PASS**.
- Formal RQ5 v3 Analysis A timing under this invocation: **NO-GO**.
- No estimator-equivalence or speedup conclusion is authorized.

## Append-only attempt history

- `forest10-v3-preflight-001`: static binding-generation failure; no database write.
- `forest10-v3-preflight-002`: preflight scope validation failure before database access; no SQL operation.
- `forest10-v3-preflight-003`: successful live bounded preflight reported here.

The initial lab startup also exposed and corrected a non-scientific launcher
configuration mismatch: the registered data directories defaulted to port
5432 despite identity records requiring 55432/55433. The registered clusters
were stopped and restarted with explicit private sockets and ports before any
database initialization.

## Evidence

Primary machine-readable summary:
`experiments/rq5-whatif-evaluation-cost-v3/preflight-invocations/forest10-v3-preflight-003/summary-v1.json`
(SHA256 `29f9474b45be5b7e0731eae91bf823f34d7dd86bbe67d60bf6f9606a68b61003`).

Cleanup record:
`experiments/rq5-whatif-evaluation-cost-v3/preflight-invocations/forest10-v3-preflight-003/cleanup-v1.json`
(SHA256 `8dc3fa75343ddfa8961735d0b021836ad5637ff90837d92e3bf34da6eb085e03`).

The summary records SHA256 values for the binding, limited manifest, raw
physical/catalogless audits, repository manifest, and result. All artifacts
are classified `integration-readiness-only`; none is formal timing evidence.
