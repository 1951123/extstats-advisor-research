# RQ5 v3 Analysis A — Clone-Cost Attribution

Status: **reconciliation PASS**. Classification:
`derived-accounting-sensitivity-not-measured-baseline`.

This report is an offline analysis of the six formal raw event streams. It does
not add a database run or create a measured no-clone Physical baseline.

## Evidence validation

- 6 complete runs and 567 events were revalidated.
- Physical: 3 × 122 events, 60 ANALYZE calls, 7,680 EXPLAIN calls.
- Catalogless: 3 × 67 events, 3 materialization ANALYZE calls, 3 sandbox-preparation ANALYZE calls, 0 per-configuration ANALYZE calls, 7,680 EXPLAIN calls.
- All 24 checkpoint values (six runs × four checkpoints) exactly reconcile to `checkpoint-summary-v2.json`.
- Original event files and formal summaries were not changed.

## Full lifecycle and clone-excluded sensitivity

Values are milliseconds, summarized as median `[minimum, maximum]` over three
runs. Full lifecycle is the published observed accounting view. The
clone-excluded column removes only the actual Physical clone-create and
clone-destroy events from each run before aggregation.

| N | Physical full | Physical clone-excluded | Catalogless full | Catalogless below clone-excluded? |
|---:|---:|---:|---:|:---:|
| 1 | 4,576.938 [4,313.020, 13,453.025] | 2,008.680 [1,907.045, 2,853.817] | 7,328.490 [7,286.985, 7,723.202] | No |
| 5 | 14,880.227 [14,710.448, 24,906.283] | 3,217.998 [3,050.647, 4,273.821] | 7,521.727 [7,486.630, 7,920.243] | No |
| 10 | 29,374.576 [27,585.117, 38,961.073] | 4,712.434 [4,597.864, 5,905.970] | 7,757.771 [7,719.060, 8,155.536] | No |
| 20 | 57,738.596 [53,976.508, 66,949.390] | 7,920.572 [7,885.607, 9,192.033] | 8,244.810 [8,205.935, 8,638.384] | No |

There is therefore no derived accounting crossover through the measured
checkpoints. The observed full-lifecycle crossover remains N=5, but it is
sensitive to including the measured Physical isolation stages.

## N=20 clone contribution

| Physical repetition | Clone create (ms) | Clone destroy (ms) | Combined clone (ms) | Clone share of full Physical |
|---:|---:|---:|---:|---:|
| 1 | 56,394.512 | 1,362.845 | 57,757.357 | 86.27% |
| 2 | 44,835.191 | 1,220.744 | 46,055.936 | 85.32% |
| 3 | 48,573.814 | 1,279.175 | 49,852.990 | 86.34% |

Descriptive N=20 medians:

- Physical full lifecycle: **57,738.596 ms**.
- Catalogless full lifecycle: **8,244.810 ms**.
- Physical/Catalogless full-lifecycle ratio: **7.003×**.
- Physical clone-excluded accounting: **7,920.572 ms**.
- Physical clone-excluded/Catalogless ratio: **0.961×**.
- Median clone contribution: **86.27%** of observed Physical lifecycle.

Thus, almost all of the observed full-workflow Physical disadvantage is associated
with the recorded clone stages. After subtracting those stages, Catalogless is
not favorable at any checkpoint; the derived Physical accounting view is slightly
lower at N=20. This does not show that a real no-clone Physical implementation
would beat Catalogless, because the subtraction does not measure the alternative
workflow's caching, reset, ANALYZE, or DDL behavior.

## Intrinsic stage decomposition

Median full-run stage times, in milliseconds:

| Arm | Stage | Median |
|---|---|---:|
| Physical | baseline initialization | 1,712.137 |
| Physical | clone create | 48,573.814 |
| Physical | CREATE/ALTER STATISTICS | 132.818 |
| Physical | ANALYZE | 5,485.629 |
| Physical | EXPLAIN | 643.859 |
| Physical | DROP STATISTICS | 12.953 |
| Physical | clone destroy | 1,279.175 |
| Physical | cleanup | 0.006 |
| Catalogless | sample acquisition | 1,404.191 |
| Catalogless | snapshot construction | 36.628 |
| Catalogless | serialization/validation | 145.233 |
| Catalogless | fixed-sample preparation | 1,189.361 |
| Catalogless | native payload materialization | 2,571.214 |
| Catalogless | repository/sandbox preparation | 1,959.775 |
| Catalogless | activation | 12.816 |
| Catalogless | EXPLAIN | 928.742 |
| Catalogless | deactivation | 10.330 |
| Catalogless | cleanup | 0.014 |

Without the clone stages, the largest measured Physical components are ANALYZE
and baseline preparation. The largest early Catalogless components are native
payload materialization, repository/sandbox preparation, sample acquisition, and
fixed-sample preparation.

## Claim boundary

The defensible mechanism-level claim is narrow: under the declared isolated
database lifecycle, repeated Physical evaluation incurred substantial measured
clone overhead, and Catalogless preparation plus activation produced lower
observed cumulative cost from the N=5 checkpoint onward. A raw-event accounting
sensitivity indicates that this advantage is largely attributable to the Physical
clone policy; it does not establish a no-clone Physical baseline, end-to-end
Advisor speedup, or a universal PostgreSQL performance result.

See `methodology.md`, `per-run-checkpoints.json`, `stage-attribution.json`,
`clone-sensitivity.json`, and `integrity-reconciliation.json` for reproducible
derived evidence.
