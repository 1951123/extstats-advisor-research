# RQ5 Tiny Fixture — Advisor Compatibility Review v1

Status: `offline-reviewed`; no PostgreSQL connection was opened.

The Forest10 RQ5 artifacts are bound to Advisor commit
`0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.  This review inspected that exact
commit, rather than inferring compatibility from the current checkout.  The
tiny fixture uses the same historical implementation identity and is therefore
not silently promoted to the newer Advisor v2 stratum.

| API / contract | Historical commit `0865c5a` | Current research adapter expectation | Compatible? |
|---|---|---|---|
| `materialize_native_stats()` | Present in `dbms/postgres/native_stats.py`; accepts `(dsn, snapshot, universe, *, statistics_target=100, batch_size=256)` and returns `NativeStatsMaterialization`. | Loads the sealed snapshot and candidate universe, then materializes one fixed-sample native repository in the patched session. | Yes |
| `PostgresPlannerSession` | Present in `dbms/postgres/planner.py`; constructor accepts `(dsn, snapshot, candidate_universe, native_repository)`, with `open`, `activate`, `estimate_query`, and `close`. | Registers a backend-local repository, activates the manifest order, and runs JSON `EXPLAIN`. | Yes |
| `load_snapshot()` / sealed snapshot | Present in `snapshot/bundle.py`; validates the sealed directory, Arrow payload digest, component digests, and semantic digest. | Loads the fixture snapshot before any live operation. | Yes |
| `load_candidate_universe()` | Present in `candidates/universe.py`; validates the source snapshot digest and candidate definitions. | Requires the manifest definitions to match the loaded universe exactly. | Yes |
| `load_native_stats_repository()` | Present in `native_stats/repository.py`; loads payload bytes and `ABSENT_NATIVE` states from the immutable repository. | Loads the repository produced by `materialize_native_stats`. | Yes |
| `validate_native_stats_repository_compatibility()` | Present in `native_stats/repository.py`; checks snapshot digest, universe digest, and exact candidate inventory. | Used as a closed-world compatibility gate before planner evaluation. | Yes |
| Native source / patched backend contract | Historical materialization and planner code require the patched backend contract returned by `probe_patched_postgres`; the reference commit is recorded in the materialization result. | Adapter requires the frozen patched PostgreSQL SHA and validates the returned contract and version. | Yes, conditional on the pinned patched build |

## Identity boundary

The checked-out Advisor tree at review time may be on a different branch or
revision.  The fixture generator accepts an explicit Advisor root and verifies
that its Git `HEAD` is the historical pin before using its serializers.  The
committed fixture's `source_bindings.advisor_sha` is the historical pin.  The
actual stock and patched binaries remain runtime inputs and must be verified
against the identity records immediately before a future preflight.

This review establishes API compatibility only.  It does not establish that a
database exists, that the binaries are installed, or that the live adapter has
successfully run.
