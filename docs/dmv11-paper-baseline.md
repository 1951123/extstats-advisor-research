# DMV11 PostgreSQL baseline

This is an independent reproduction of the stock PostgreSQL DMV11 result in
Table 4 of *Are We Ready For Learned Cardinality Estimation?*. It is a
dataset/schema/workload fidelity gate and must complete before any DMV11
advisor experiment.

The frozen source is the accepted AreCEL archive at
`EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/dmv11` and its audited canonical
workload under `arecel/audit-v1`. The adapter binds the benchmark ID, relation,
CSV, workload pickle, label pickle, canonical workload, shared archive,
upstream revision, and ordered schema to a schema-aware dataset identity. The
observed CSV has 11,591,877 rows, 11 columns, and no empty fields. These facts
are checked directly; no row count is inferred from a secondary reference.

The physical relation is `public.dmv11` with the upstream unquoted DDL
semantics: lowercase catalog names, ten `character varying(64)` columns, one
`double precision` column, and `attnotnull = false` for every column. The
baseline captures the actual native PostgreSQL collation for each varchar
column and rejects generic `text`, lost typmods, non-null columns, unexpected
extended statistics, or row-count mismatch. No indexes or extended statistics
are created.

Canonical `base:test` extraction preserves source order and creates stable IDs
`arecel_dmv11_test_000000` through `arecel_dmv11_test_009999`. The only SQL
projection change is `COUNT(*)` to `SELECT *`; the audited source relation is
adapted to `public.dmv11`, and only source identifiers are folded to catalog
names. Literals—including mixed case, spaces, punctuation, categorical codes,
and numeric values—remain byte-for-byte unchanged.

The live protocol loads a fresh stock PostgreSQL 16.14 database, performs
deterministic exact truth checks on a representative subset, executes
`SELECT setseed(1.0 / 123)`, sets statistics target 10000 on all columns, runs
exactly one `ANALYZE public.dmv11`, and evaluates all 10,000 adapted queries
using `EXPLAIN (FORMAT JSON)` and root `Plan Rows`. It uses production
`extstats_advisor.utility.QErrorLoss` with contract
`qerror-cardinality-floor-1-v1` and uniform query weights. It does not use
`EXPLAIN ANALYZE` for the workload evaluation.

The rounded PostgreSQL references are p50 1.19, p95 78, p99 3255, and max
100000. The rounded Naru values are retained only as external context. A
single-query-sensitive maximum is judged together with schema fidelity,
exact truth checks, workload mapping, and p50/p95/p99; it is not an acceptance
constant.

Evidence is written to `paper-baselines/arecel-dmv11/` as
`postgres-v1.json` and `postgres-per-query-v1.jsonl`. The artifact records
source hashes, physical schema/collations, truth sanity checks, PostgreSQL
metadata, statistics protocol, q-error summary, maximum-error query, and tail
contribution fractions. It contains no credentials. This baseline does not
create the later `authoritative-external-exact` production GroundTruthSet;
that belongs to the subsequent canonical DMV11 advisor run.
