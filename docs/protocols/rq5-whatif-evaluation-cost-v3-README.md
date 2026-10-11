# RQ5 What-if Cost v3 Offline Preparation

The v3 harness is research-owned and offline-first. It does not start
PostgreSQL, run `ANALYZE`, execute `EXPLAIN`, or perform timing measurements.

## Artifact preparation

The full Forest10 source is external to Git and must pass the audited dataset
contract. Prepare a sealed 30,000-row snapshot with the installed historical
Advisor serializer:

```text
.venv/bin/python -c 'from pathlib import Path; from extstats_advisor_research.rq5_whatif_cost_v3 import prepare_v3_snapshot; print(prepare_v3_snapshot(Path("/home/wqts/benchmark-data"), Path("runs/_forest10_workload.json"), Path(".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/advisor-snapshot")))'
```

Then derive the matching Advisor candidate universe:

```text
.venv/bin/python -c 'from pathlib import Path; from extstats_advisor_research.rq5_whatif_cost_v3 import prepare_v3_candidate_universe; print(prepare_v3_candidate_universe(Path(".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/advisor-snapshot"), Path(".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/candidate-universe.json"), Path("experiments/rq5-whatif-evaluation-cost-v2/manifests/analysis-a-manifest-v1.json")))'
```

The generator uses the fixed SHA-256 row-rank recipe from Protocol v3, not
PostgreSQL sampling. The sample is sealed with `extstats_advisor`'s
`AdvisorSnapshot` contract. The generated runtime directory is intentionally
not committed as a database dump or private data bundle.

## Primary lifecycle timing boundary

The complete-lifecycle Catalogless run must reconstruct the fixed sample from
the verified full Forest10 source. A prebuilt `AdvisorSnapshot` is an input
for offline preparation and prepared-state diagnostics only; it is not a
zero-cost substitute for the primary lifecycle.

The current research-owned implementation is CSV-backed: it reads the selected
rows from the audited Forest10 source representation, rather than extracting
them from a PostgreSQL relation. That source read and all subsequent
Snapshot construction/serialization are therefore recorded as Catalogless
method-specific preparation. The common exclusion remains limited to the
one-time full-population provisioning shared by the arms; CSV access is not
silently treated as free.

For a v3 Catalogless run, the research harness records these run-level stages
in this order:

1. `sample-acquisition` — validate the full source, read the selected 30,000
   rows, and verify the frozen row-membership digest.
2. `snapshot-construction` — construct the AdvisorSnapshot object from those
   rows and the frozen workload.
3. `snapshot-serialization-validation` — serialize to the invocation output
   namespace, reload it through the Advisor bundle reader, and verify the
   frozen semantic digest and sample size.
4. `fixed-sample-preparation` — load the runtime-produced Snapshot and frozen
   candidate universe/workload bindings for the live adapter.
5. `native-payload-materialization` — construct the fixed 57-candidate native
   repository once. Any native materialization ANALYZE belongs to this stage.
6. `repository-registration-and-sandbox-preparation` — prepare and verify the
   patched planner sandbox, including its required preparation ANALYZE, then
   register the backend-local repository.

The per-configuration stages remain activation, the declared EXPLAIN replay,
and deactivation. `arm-cleanup` is recorded last and is not silently folded
into another stage. Events are append-only and complete-result validation
rejects missing, duplicated, failed, or out-of-order mandatory stages.

The secondary prepared-state view is valid only when a separately authorized
measurement explicitly identifies which recorded preparation stages were
excluded. The ordinary v3 complete-lifecycle path does not authorize loading
the checked-in Snapshot as an unmeasured preparation shortcut. Event writes
are emitted after each timed operation; the monotonic interval covers the
adapter operation and its declared research-owned work, not later event-stream
serialization.

## Manifests and validation

The checked-in v3 Analysis A/B manifests bind the audited full source,
candidate universe, query sequence, sample recipe, and source pins. They retain
the v2 candidate definitions and 128 query selection exactly while changing
only the population/sample execution boundary. Validate them with
`validate_v3_manifest`; strict readiness additionally requires the sealed
snapshot and matching semantic digest.

The manifest generator is `build_v3_manifest` in
`src/extstats_advisor_research/rq5_whatif_cost_v3.py`. The existing
`run_physical_arm` and `run_catalogless_arm` consume the same event and cost
contracts; v2 keeps its historical stage names, while v3 uses the explicit
preparation stages above. The event protocol field remains bound to the
manifest version.

## Live authorization boundary

This revision does not authorize a live correctness preflight or formal timing.
Future live execution requires explicit stock/patched DSNs, binary identity
records, the historical Advisor checkout, the audited Forest10 data root, the
sealed v3 snapshot and candidate universe, and a fresh append-only namespace.
The arms are a mechanism-cost comparison, not an estimator-fidelity test.
