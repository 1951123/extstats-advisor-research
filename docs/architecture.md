# Architecture and ownership

The canonical data flow is:

```text
audited benchmark source
  -> dataset adapter and ETL
  -> simulated stock PostgreSQL public.census13
  -> frozen advisor Snapshot + exact GroundTruthSet
  -> frozen advisor CandidateUniverse
  -> frozen advisor fixed-sample NativeStatsRepository
  -> patched PostgreSQL sandbox
  -> frozen advisor profiling, plan, search, Recommendation
  -> research artifact validation and summary
```

The dataset adapter validates source hashes, creates the typed SQL schema,
loads CSV data, validates row/content metadata, and extracts the audited
workload. It never creates `AdvisorSnapshot`, candidate objects, truth
objects, native statistics, or search state. CSV/DataFrame values are never
passed directly to the optimizer.

The production advisor is the system under test and is invoked only through
its public CLI and public APIs. The research repository owns orchestration and
analysis, not advisor semantics. The patched PostgreSQL tree is a pinned
planner dependency; the full benchmark is not loaded there.

Before loading, the harness checks the target relation. Arbitrary existing
extended statistics are not reconciled or silently dropped. An explicit
`--reset-disposable` is required for destructive reset of a disposable target
database; canonical runs should instead use a fresh database.

The first unit does not deploy a Recommendation. Deployment, reconciliation,
garbage collection, and additional datasets are outside scope.
