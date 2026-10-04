# Census numeric type-coercion diagnostic

This diagnostic tests one narrow mechanism behind the Census13 baseline gap.
It does not change `DatasetAdapter`, workload SQL, snapshots, candidate
generation, planner sandbox behavior, or advisor optimization.

The same source predicates can produce different PostgreSQL selectivity
expressions when the declared column type changes. With a decimal workload
literal, a `DOUBLE PRECISION` column can use a same-type comparison, while a
`BIGINT` column may be represented by PostgreSQL as a column-side cast to
`numeric`. That is not the same planner expression as a bare column comparison,
even though the query's logical row semantics are unchanged.

The live command creates identical full-data relations with controlled numeric
and categorical types, sets target 100, runs one `ANALYZE`, checks exact truth
for all 10,000 queries, and captures `EXPLAIN (VERBOSE, FORMAT JSON)` filters.
H1 uses `DOUBLE PRECISION` numerics plus `TEXT` categoricals; H2 uses `BIGINT`
numerics plus `VARCHAR(64)` categoricals. Ordinary `pg_stats` presence and
physical extended-statistics counts are recorded. The resulting decision is a
mechanism diagnostic only, not a broader claim about advisor quality.
