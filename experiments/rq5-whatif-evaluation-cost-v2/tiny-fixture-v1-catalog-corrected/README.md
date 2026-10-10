# RQ5 Tiny Cost Fixture v1 — Catalog-Bound Correction

This is an append-only correction of the original `tiny-fixture-v1`.  The
original fixture and all failed preflight invocations remain immutable.  The
correction was required because the historical patched planner sandbox contract
requires the sealed relation catalog to equal `current_database()`.

The snapshot in this directory therefore binds its relation catalog to
`rq5_tiny_patched_template`, the explicitly isolated patched lab database.  It
retains the same 12 rows, relation schema, six candidate definitions, two
ordered configurations, and five query shapes.  Because the catalog is part of
the sealed snapshot identity, this correction has distinct snapshot and
candidate-universe digests and must be treated as a new fixture binding.

Classification: `integration-readiness-only`; not timing evidence.

Use this directory for the next correctness-only preflight.  Do not replace
the original fixture or reinterpret its failed invocation records.
