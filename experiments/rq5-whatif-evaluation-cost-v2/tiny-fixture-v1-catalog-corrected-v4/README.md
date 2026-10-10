# RQ5 Tiny Cost Fixture v1 — Exact Collation Correction v4

This append-only fixture uses the complete collation identifier returned by
PostgreSQL and required by the historical Advisor resolver:
`pg_catalog."default"`.

The original fixture and earlier corrections remain unchanged. This artifact
is `integration-readiness-only`, not timing evidence, with the same twelve
rows, six candidates, two ordered configurations, and five queries.
