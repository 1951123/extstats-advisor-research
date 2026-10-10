# RQ5 Tiny Cost Fixture v1 — Catalog and Collation Correction

This append-only fixture corrects two live integration requirements discovered
by the preserved tiny-preflight attempts: the sealed relation catalog equals
the patched sandbox database, and PostgreSQL's default text collation is
retained in the sealed snapshot as `pg_catalog.default`.

The original `tiny-fixture-v1` and the first catalog-bound correction remain
unchanged. This fixture is still `integration-readiness-only`, not timing
evidence, and contains the same twelve rows, six candidate definitions, two
ordered configurations, and five queries.
