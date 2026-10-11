# RQ5 v4 Formal Analysis A — in-place Physical baseline

- Classification: `formal-rq5-v4-analysis-a`
- Status: **COMPLETE; eligible for analysis**
- Manifest semantic digest: `813870b1bbdefdce9450f70a6dacb42abcd8b159a1d00c0c96fa8c015b604c77`

## Execution

The frozen schedule was P1 → C1 → C2 → P2 → P3 → C3. Each arm had three complete repetitions, 20 singleton configurations per repetition, and 128 EXPLAIN calls per configuration. No Analysis B execution occurred.

| N | Physical median ms (range) | Catalogless median ms (range) | Catalogless median lower |
|---:|---:|---:|:---:|
| 1 | 1871.070 (1824.921–2061.024) | 7045.934 (6877.821–7133.001) | no |
| 5 | 2847.376 (2780.526–3068.396) | 7237.321 (7071.159–7379.188) | no |
| 10 | 4135.709 (4011.388–4271.506) | 7470.068 (7310.698–7650.435) | no |
| 20 | 6751.457 (6656.795–6844.546) | 7979.233 (7799.565–8154.504) | no |

At N=20 the descriptive median ratio Physical/Catalogless is **0.846×**. This is a measured lifecycle comparison for the declared workflows, not an end-to-end Advisor speedup or estimator-fidelity result.

## Stage decomposition

### Physical

| Stage | Median ms | Range ms |
|---|---:|---:|
| `analyze` | 4354.873 | 4283.934–4396.591 |
| `arm-cleanup` | 60.840 | 60.545–65.222 |
| `baseline-initialization` | 1602.023 | 1579.518–1797.663 |
| `create-alter-statistics` | 14.659 | 14.282–15.382 |
| `drop-statistics` | 9.165 | 8.977–9.394 |
| `explain` | 617.054 | 589.901–629.381 |
| `post-drop-verification` | 47.953 | 47.308–48.134 |

### Catalogless

| Stage | Median ms | Range ms |
|---|---:|---:|
| `activate` | 12.393 | 12.242–14.133 |
| `arm-cleanup` | 14.814 | 14.572–15.374 |
| `deactivate` | 10.750 | 10.414–12.139 |
| `explain` | 947.700 | 932.253–1042.751 |
| `fixed-sample-preparation` | 1140.758 | 1110.683–1184.159 |
| `native-payload-materialization` | 2508.111 | 2479.332–2622.614 |
| `repository-registration-and-sandbox-preparation` | 1884.179 | 1861.996–1909.905 |
| `sample-acquisition` | 1228.585 | 1205.449–1241.176 |
| `snapshot-construction` | 40.356 | 33.474–48.595 |
| `snapshot-serialization-validation` | 131.466 | 129.583–133.345 |

## Accounting and controls

- Final `arm-cleanup` is included only at N=20; N=1/5/10 are observed through-checkpoint costs without invented cleanup allocation.
- Physical has no clone-create or clone-destroy stage. Each repetition used one fresh experiment-owned full-population database, then reused that database sequentially.
- Catalogless preparation was independently performed in each repetition; materialization and sandbox preparation each recorded one ANALYZE, with zero configuration-level ANALYZE.
- All six raw event streams passed the v4 validator. Physical post-drop inventories were empty for every configuration. Formal databases, source statistics, and patched sandbox resources were verified absent/clean after the run.
- Native payload bytes, Plan Rows, ordinary-statistics fingerprints, and q-error were not equality gates for this cost experiment.

## Interpretation limits

Three repetitions provide descriptive variability only. The result concerns the measured in-place stock lifecycle versus the catalogless lifecycle under the frozen Forest10 workload and source pins. It does not compare Advisor search algorithms, establish cardinality-estimation superiority, or justify a universal speedup claim. The earlier v3 clone-based result remains separate historical evidence.
