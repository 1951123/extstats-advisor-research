# Research run registry

## `197e9b890ac58bc4fbcfb218`

- Status: `superseded-for-arecel-main-evaluation`
- Reason: the research adapter loaded the numeric AreCEL columns as `BIGINT`
  while the audited workload contains decimal numeric literals generated for
  `DOUBLE PRECISION` columns. PostgreSQL consequently applied column-side
  numeric coercion and distorted ordinary-statistics selectivity.
- Evidence: baseline-gap semantic digest
  `7a589232a3210f69b2cb24f2fe54047d241f58d7f90cf4c293e1338d4480be5d`;
  type-coercion semantic digest
  `cb1398c3a3c48b9ee43693a95f9f2ddf18267f3334db2fc66a61a67771d9f8b6`.

This run remains valid evidence for discovering the adapter bug. Its manifest
and artifacts are immutable and are not rewritten.
