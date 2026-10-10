# RQ5 Tiny Cost Fixture v1 — Catalog and Collation Correction v3

This append-only fixture records the exact PostgreSQL collation spelling used
by the historical Advisor resolver: `pg_catalog."default"`. It also binds the
sealed relation catalog to the patched sandbox database.

The original fixture and earlier corrections remain unchanged. This artifact
is `integration-readiness-only`, not timing evidence, with the same twelve
rows, six candidates, two ordered configurations, and five queries.
