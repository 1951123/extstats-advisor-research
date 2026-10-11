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
contracts; the event protocol field is now bound to the manifest version.

## Live authorization boundary

This revision does not authorize a live correctness preflight or formal timing.
Future live execution requires explicit stock/patched DSNs, binary identity
records, the historical Advisor checkout, the audited Forest10 data root, the
sealed v3 snapshot and candidate universe, and a fresh append-only namespace.
The arms are a mechanism-cost comparison, not an estimator-fidelity test.
