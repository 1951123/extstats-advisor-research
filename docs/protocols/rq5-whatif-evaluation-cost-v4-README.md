# RQ5 What-if Evaluation Cost v4 — execution notes

The v4 implementation is research-owned and lives in
`rq5_whatif_cost.py` and `rq5_whatif_cost_postgres.py`.  Use the v4 Analysis A
manifest and `InPlacePhysicalCostAdapter` for Physical; use the existing
Catalogless adapter with the same v4 manifest.

Before a run, provision one fresh database per Physical repetition from the
protected full-population laboratory template with
`provision_inplace_database()`.  The target name must begin with
`rq5wc_v4_`; the template must not be experiment-owned.  Provisioning is
outside the primary timing boundary.  Pass the target database in the
explicit stock DSN and construct the adapter with `owns_database=True` so
cleanup can drop only that registered target after residual-statistics checks.

The v4 Physical runner records no clone stages.  It records one baseline,
five stages per configuration, and final cleanup.  The Catalogless runner
retains runtime sample and Snapshot preparation and records the combined
repository-registration/sandbox-preparation stage.

No database is contacted by manifest generation or ordinary tests.  Live
correctness preflight and formal timing require explicit DSNs, pinned binary
identity records, the historical Advisor checkout, and a fresh output
directory.  Preflight artifacts are classified `integration-readiness-only`;
formal artifacts are classified `formal-rq5-v4-analysis-a`.  Analysis B is not
implemented or authorized by this amendment.
