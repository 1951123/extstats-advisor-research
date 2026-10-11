# RQ5 What-if Cost v3 — Formal Analysis A

Classification: `formal-rq5-v3-analysis-a`

Status: **complete**. All six scheduled arm/repetition runs completed and passed
raw-event validation. The benchmark measured lifecycle cost only; it did not
measure estimator fidelity, q-error, Advisor search, or statistical significance.

## Frozen execution

- Producer: `eeaaff45cd363064895442f68ca1897851d2e064`
- Analysis A manifest semantic digest: `27c53072a8a2083a0a982ab04e86e063f030daea6d4a3ca32e1daedc8625a71e`
- Schedule: `P1 → C1 → C2 → P2 → P3 → C3`
- 20 singleton configurations, 128 queries/configuration, three repetitions/arm
- Stock PostgreSQL source: `0d1c00c624fa7367d4a895f44381887757289682`
- Patched PostgreSQL source: `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`
- Historical Advisor: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`

## Counts

| Arm | Complete runs | Method arms | EXPLAIN | Native ANALYZE | Valid observations |
|---|---:|---:|---:|---:|---:|
| Physical | 3 | 60 | 7,680 | 60 | 7,680 |
| Catalogless | 3 | 60 | 7,680 | 3 materialization + 3 sandbox preparation; 0/configuration | 7,680 |
| Total | 6 | 120 | 15,360 | 66 preparation/configuration operations as above | 15,360 |

## Complete-lifecycle checkpoints

Times are milliseconds. Each cell is `median [minimum, maximum]` across the
three complete repetitions. At `N=1,5,10`, the value is the observed cost
through that checkpoint and excludes final cleanup, which occurs after `N=20`.
At `N=20`, final `arm-cleanup` is included directly.

| N | Physical | Catalogless | Median Catalogless < Physical |
|---:|---:|---:|:---:|
| 1 | 4,576.938 [4,313.020, 13,453.025] | 7,328.490 [7,286.985, 7,723.202] | No |
| 5 | 14,880.227 [14,710.448, 24,906.283] | 7,521.727 [7,486.630, 7,920.243] | Yes |
| 10 | 29,374.576 [27,585.117, 38,961.073] | 7,757.771 [7,719.060, 8,155.536] | Yes |
| 20 | 57,738.596 [53,976.508, 66,949.390] | 8,244.810 [8,205.935, 8,638.384] | Yes |

The observed median crossover is `N=5`. Per repetition, the first observed
Catalogless-below-Physical checkpoint was `N=1` for Physical repetition 1's
paired comparison and `N=5` for repetitions 2 and 3. This is descriptive
evidence from three runs, not a significance claim or an extrapolated break-even
point.

## Stage medians

| Arm | Dominant measured stages (median ms) |
|---|---|
| Physical | baseline 1,712.137; clone-create 48,573.814; ANALYZE 5,485.629; EXPLAIN 643.859; clone-destroy 1,279.175 |
| Catalogless | sample acquisition 1,404.191; snapshot construction 36.628; serialization/validation 145.233; fixed-sample preparation 1,189.361; native materialization 2,571.214; repository/sandbox preparation 1,959.775; EXPLAIN 928.742 |

Prepared-state warm-start medians at `N=20` were 56,026.452 ms for Physical
and 952.431 ms for Catalogless. These exclude only the recorded mandatory
preparation stages listed in each run's `warm_start_excludes`; they are
secondary views, not the primary lifecycle result.

## Integrity and cleanup

- Every Physical run: 122/122 expected events, 20 ANALYZE calls, 2,560 EXPLAIN calls.
- Every Catalogless run: 67/67 expected events, one native materialization ANALYZE, one sandbox-preparation ANALYZE, zero per-configuration ANALYZE calls, 2,560 EXPLAIN calls.
- All sample acquisitions matched the frozen 30,000-row membership digest and all materialization audits recorded 57 candidates.
- Source relation audit after execution: 581,012 rows; no experiment-owned statistics remained.
- No `rq5wc_*` clone databases or patched sandbox relations remained.
- Both laboratory PostgreSQL instances were stopped after execution.

The append-only `integrity-audit-v1.json` contains a false-positive derived
candidate-count check against a schema that stores `payload_states` rather than
`candidate_count`. It did not alter raw evidence or execution. The corrected
`integrity-audit-v2.json` uses `len(payload_states)` and passes. Likewise,
`formal-summary-v2.json` is the corrected derived summary for the preserved
`formal-summary-v1.json` crossover predicate.

See `formal-summary-v2.json`, `checkpoint-summary-v2.json`,
`stage-summary-v1.json`, `integrity-audit-v2.json`, and
`cleanup-audit-v1.json` for machine-readable evidence.
