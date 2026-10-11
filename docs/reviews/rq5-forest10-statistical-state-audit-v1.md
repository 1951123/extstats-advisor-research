# RQ5 Forest10 Statistical-State Comparability Audit v1

**Status:** `CONDITIONAL GO`

**Scope:** Offline audit of the persisted Forest10 limited preflight
`forest10-preflight-004`. This review did not start PostgreSQL, execute
`ANALYZE`, execute `EXPLAIN`, execute `COUNT`, acquire truth, or run timing
measurements.

## 1. Decision summary

The preflight verified the same five SQL queries and the intended singleton
definition in both arms, but it did not establish the same planner statistical
state. The strongest explanation for the large Plan Rows difference is a
relation-cardinality mismatch:

* the fixed sample contains 10,000 rows;
* the catalogless patched sandbox explicitly replays the snapshot population
  estimate of 581,012 rows as `pg_class.reltuples`; and
* the first four catalogless/physical Plan Rows ratios are 57.69--58.15,
  centered on `581012 / 10000 = 58.1012`.

This is not proof that relation scale caused every estimate difference. The
physical preflight artifact did not retain `pg_class.reltuples`, `relpages`, or
the individual ordinary-statistics rows, so the physical side of the scale
comparison cannot now be directly reconstructed. The evidence is nevertheless
strong enough to identify an uncontrolled statistical-state difference that
must be corrected or explicitly equalized before formal timing.

**Recommendation: CONDITIONAL GO.** The RQ5 mechanism-cost question remains
meaningful without requiring identical Plan Rows or q-error. Formal timing
should proceed only after a versioned harness/preflight correction records and
aligns the relation-scale boundary (or deliberately uses the same sample-scale
state in both arms), and records the relevant GUCs and relation metadata. No
Advisor or PostgreSQL source change is required by this audit. No new CE study
is required.

## 2. Audited identities and immutable inputs

The research repository was at commit
`1a8640d803a3ad8a9ac1283d24165ea43891151e` on `main`, equal to
`origin/main`, with a clean worktree at audit start. The source worktrees were
also clean: Advisor `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`, stock
PostgreSQL `0d1c00c624fa7367d4a895f44381887757289682`, and patched PostgreSQL
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. The persisted preflight was bound
to historical Advisor `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` and PostgreSQL
16.14.

| Input | Path | SHA256 or semantic identity |
|---|---|---|
| Protocol | `docs/protocols/rq5-whatif-evaluation-cost-v2.md` | `56175f66c6e05066de91ef7219984c7f5889a6484173c4d4a6766ce8aeaf5192` |
| Analysis A manifest | `experiments/rq5-whatif-evaluation-cost-v2/manifests/analysis-a-manifest-v1.json` | byte `d0c4d8a991b74581d9bbaaa69881b8b1dae66911d55fabc85940fce78186c919`; semantic `c58f6abed18a4a3998c1bb49cf125cc760fc27de793e36588e17bbf84d8d8b81` |
| Analysis B manifest | `experiments/rq5-whatif-evaluation-cost-v2/manifests/analysis-b-manifest-v1.json` | byte `e71710605ec8295b3063916aa8c90b2ec9a2dde1dbf3071ec72f067f846b28cd`; semantic `28c795e1f9c9e16b6de83b08a4ef12bc77e9a5ad37f0e1bfd1c33a95f02dc472` |
| Limited manifest | `.../forest10-preflight-004/limited-manifest-v1.json` | byte `aabe4fda9d8dcae80b2abdc2eda5a159da34aa03c2a72a369d67ad463c603ebf` |
| Preflight summary | `.../forest10-preflight-004/summary-v1.json` | byte `24a8886be8ab9c5f4f66fa6f8295102add2c018484746d765a4c294cf5b58220` |
| Snapshot manifest | `.runtime/.../advisor-snapshot/manifest.json` | semantic `65cc525eee74175abbe53dd128b45a09ffa5ebad878f3d4c90f4a57660ef3a2a`; byte `7d05f86bde463e6b4c1090261306784ec8c7960b28baaccdddc27b56499f31ed` |
| Population metadata | `.runtime/.../advisor-snapshot/population.json` | byte `b7f0da6e423fe6fb0eb7ca3815c7c1ed87c03496eaf2371f4f507026bbea00ec` |
| Candidate derivation | `.../forest10-preflight-004/candidate-universe-derivation-v1.json` | byte `0c17d12d86f22dfd367329bec620adc91940cb9b86047bc0540a2651ac574256` |
| Preflight result | `.../forest10-preflight-004/result-v1.json` | byte `be453f3e79ec2a1183aa8b5a46e214553f5b0f5726a9e8939d0c9ca85c08c5a6` |

The summary records the preflight as `PASS` with classification
`integration-readiness-only`; it explicitly records that cross-arm Plan Rows
and ordinary fingerprints are not equal and are not an accuracy comparison.

## 3. Exact Plan Rows comparison

The persisted physical and catalogless explain audit files contain the same
five query IDs, in the same order, with successful counts 5/5.

| Query ID | Physical | Catalogless | Catalogless / physical |
|---|---:|---:|---:|
| `arecel_forest10_test_000000` | 110 | 6,396 | 58.145455 |
| `arecel_forest10_test_000078` | 65 | 3,750 | 57.692308 |
| `arecel_forest10_test_000157` | 2,469 | 143,452 | 58.101256 |
| `arecel_forest10_test_000236` | 396 | 23,020 | 58.131313 |
| `arecel_forest10_test_000314` | 1 | 34 | 34.000000 |

The first four ratios are within approximately 0.70% of the scale factor
58.1012. Their mean ratio is 58.0176. The last ratio is not comparable as a
scale diagnostic because the physical estimate is rounded to one row; its
unrounded estimator value is not persisted. This pattern is highly
consistent with a shared selectivity calculation being applied to different
base-relation cardinalities, while not by itself proving that mechanism.

## 4. Relation cardinality and scaling

The sealed snapshot population record states:

```json
{"row_count": 581012.0, "row_count_quality": "estimate",
 "row_count_source": "postgresql.pg_class.reltuples"}
```

The snapshot sample and both preflight bindings state 10,000 sample rows.
The catalogless sandbox audit records `target_sample_row_count: 10000`,
`reltuples: 581012.0`, and population quality `estimate`.

The historical Advisor sandbox implementation makes this state intentional:

* `sandbox.py:_set_replay_config` sets
  `pg_extstats.frozen_sample_mode = replay`, the frozen sample relation, and
  `pg_extstats.frozen_totalrows = population` (lines 299--311).
* `prepare_postgres_planner_sandbox` copies the 10,000-row sample, enables the
  replay configuration, runs `ANALYZE`, and requires the observed
  `pg_class.reltuples` to equal the snapshot population (lines 587--610).
* `verify_postgres_planner_sandbox` independently checks the target row count
  is 10,000 while checking `reltuples` against the population (lines
  447--463).
* Patched PostgreSQL `analyze.c:1494--1503` assigns the replay population to
  `*totalrows`, while reading rows from the auxiliary frozen sample relation.

By contrast, the stock physical adapter creates a clone of the source
database, runs ordinary `ANALYZE public.forest10`, and captures relation and
statistics-object identity plus ordinary-statistics fingerprint. In
`rq5_whatif_cost_postgres.py`, `StockPhysicalCostAdapter.analyze` is lines
675--684 and `_capture_state` is lines 609--673. `_capture_state` does not
persist `pg_class.reltuples` or `pg_class.relpages`. The physical source and
clone contain the 10,000-row Arrow sample; no persisted artifact establishes
that stock ANALYZE assigned the population estimate 581,012 to the clone.

Stock PostgreSQL's planner uses relation metadata in its base-relation size
calculation: `src/backend/optimizer/util/plancat.c:1037--1144` consumes
`relpages` and `reltuples`, and `table_relation_estimate_size` derives the
planner tuple estimate from that relation state. Thus the scale difference is
planner-relevant, not merely bookkeeping.

**Finding:** relation scale is the most likely primary cause of the common
factor in the first four query estimates. It is a strong source-and-artifact
explanation, not a directly observed causal intervention, because the stock
clone's relation metadata was not retained.

## 5. Ordinary-statistics fingerprints

The physical arm records:

* stock server 16.14, source database `extstats_stock`, relation OID 16490;
* ordinary fingerprint
  `17f97a6695240dd0abfd9ceead83e504355358601234a3e89f6ebd940c40bb54`;
* one stock `ANALYZE` before the five EXPLAIN calls.

The catalogless arm records:

* patched target relation OID 16742;
* ordinary fingerprint
  `c305028a2ce4860299b90ff41a428318f9d6eaa7cd04a93d1924fddba8b893bb`;
* one native materialization ANALYZE and one sandbox-preparation ANALYZE;
* the sandbox verifier checks that the ordinary fingerprint equals the native
  repository fingerprint.

The mismatch proves different ordinary-statistics content under the project’s
fingerprint contract. It does not identify which `pg_statistic` fields differ,
and it does not prove that ordinary statistics, rather than relation scale,
caused the approximately 58x pattern. Ordinary-statistics variation is a
second uncontrolled estimator input and must be reported/audited in any
formal timing run. The current protocol permits different native payload bytes
between stock and catalogless arms, but it does not make this ordinary-state
mismatch irrelevant to interpretation of Plan Rows.

## 6. Native candidate payload

The selected candidate is identical at the definition level in both arms:

* candidate `cand_1537a6ae8ef67c379c8d0fa0`;
* relation `rel_96c2e4f82f8ed10b05330552`;
* columns `aspect`, `hillshade_9am`, ordinals `[2, 7]`;
* kind `postgresql.mcv`; target 100.

Physical catalog evidence records one statistics object, OID 16494, kind
`{m}`, payload `present`, 2,966 bytes, SHA256
`58c2f68167630df6079f9e859d0e1ef50bdd6cb34fa78117a556a73d15e2af09`.

Catalogless repository evidence records the corresponding virtual OID
3968454259, payload `present`, 160 bytes, SHA256
`049adae92c1bba44ba38999be733fd1817f071217be5879624038d24b17eca6b`.
All 57 catalogless candidate payload states were `present` in this preflight.

The payload bytes and sizes differ, as expected from stock physical ANALYZE
versus patched fixed-sample materialization. The persisted evidence does not
show a missing selected object, a kind mismatch, or a candidate-definition
mapping error. It also does not establish semantic equality of the two MCV
realizations. Protocol v2 explicitly treats stock-versus-catalogless payload
byte equality as unnecessary for the mechanism-cost question.

## 7. Workload, relation identity, planner settings, and parsing

The two explain audit files contain the same query IDs and the limited
manifest binds the same five SQL digests. The relation schema and candidate
definition contract passed in the preflight summary. Physical parsing uses
`EXPLAIN (FORMAT JSON)` and the research `_explain_rows` root `Plan Rows`
extractor (`rq5_whatif_cost_postgres.py:686--718`). Catalogless parsing uses
the historical `PostgresPlannerSession.estimate_query` path, which issues
`EXPLAIN (VERBOSE, FORMAT JSON)` and extracts `Plan Rows` from the one managed
base-relation node (`planner.py:304--339`). Both therefore target base-relation
cardinality, although the option sets are not byte-identical (`VERBOSE` is
present only in the catalogless call).

The persisted preflight establishes server version and binary source identity,
but it does not persist a complete `SHOW` snapshot for both sessions. The
historical planner session starts a repeatable-read, read-only transaction and
sets its search path (`planner.py:169--185`); the physical adapter does not
record an equivalent complete GUC inventory. No evidence demonstrates a
planner-setting mismatch, but exact GUC equality is not independently
auditable from the retained files.

The distinct relation OIDs (16490 versus 16742) are expected for separate
databases and are not themselves a defect. The semantic relation and column
contract match.

## 8. Verified explanations versus unresolved hypotheses

### Verified from source or persisted artifacts

1. The workload sequence, query IDs, and SQL digests are aligned for all five
   queries.
2. The selected candidate definition, kind, target, and membership are aligned.
3. Physical execution used one stock `ANALYZE`; catalogless preparation used
   one materialization and one sandbox preparation `ANALYZE`, with zero
   per-configuration ANALYZE calls.
4. The catalogless target contains 10,000 rows but intentionally reports
   `reltuples = 581012`.
5. Ordinary-statistics fingerprints differ.
6. Native MCV payloads are present but differ in size and bytes.
7. PostgreSQL planner relation sizing consumes relation metadata, including
   `reltuples`/`relpages`.

### Strongly supported interpretation

The common approximately 58x ratio in four queries is consistent with the
catalogless full-population replay state versus the physical sample-scale
state. This is more specific than attributing the difference to generic
ANALYZE randomness.

### Not established by retained evidence

1. The exact stock clone values of `reltuples`, `relpages`, and each ordinary
   statistic row.
2. The counterfactual Plan Rows that either arm would produce after aligning
   relation scale or ordinary statistics.
3. A quantitative decomposition of the Plan Rows difference into relation
   scale, ordinary statistics, native MCV payload, and stock-versus-patched
   planner behavior.
4. Strict equivalence of physical and hypothetical effective statistics order.
5. Complete equality of session GUCs.

No retained evidence supports calling the discrepancy a payload-reader bug or
a candidate identity defect.

## 9. Impact on RQ5

1. **Timing comparability:** A timing comparison can remain valid as a
   mechanism-cost comparison without identical cardinality estimates. It
   cannot treat the arms as identical estimator states while this mismatch is
   unmeasured.
2. **Workload equivalence:** The SQL workload and intended definitions are
   equivalent at the manifest level. The planner statistical state is not
   equivalent, so the future paper must not describe the arms as producing
   equivalent plans or estimates.
3. **Runtime interpretation:** The current five-query Plan Rows difference
   does not invalidate measuring client-side cumulative cost, but any claim
   about planner behavior, plan complexity, or estimator fidelity would be
   confounded until relation metadata and GUCs are audited.
4. **Scientific claim boundary:** RQ5 may claim measured mechanism lifecycle
   costs under explicitly recorded stock/patched statistical realizations. It
   may not claim estimator equivalence, q-error equivalence, or that a timing
   difference is caused solely by catalogless activation rather than the full
   arm implementations.

## 10. Required follow-up before formal timing

The smallest justified follow-up is research-harness instrumentation and a
fresh limited correctness preflight, not a new accuracy experiment:

1. Persist `pg_class.reltuples`, `relpages`, and a per-column ordinary
   statistics inventory for both arms immediately before EXPLAIN.
2. Persist a complete `SHOW`/planner-session settings inventory, including the
   patched replay GUCs.
3. Make the relation-scale boundary explicit in the benchmark. The preferred
   mechanism-level comparison is to use the same declared sample-scale
   relation metadata in both arms, unless a versioned protocol decision
   intentionally compares physical sample realization with catalogless
   full-population replay.
4. Keep the existing payload and ordinary fingerprints; do not manufacture
   byte equality or alter frozen candidate definitions.
5. Preserve the existing five-query preflight and use a new invocation ID for
   the corrected run.

No correction should be applied to historical evidence, frozen manifests,
Advisor source, or PostgreSQL source. If the formal benchmark intentionally
retains the population replay state, it must label the result as an operational
cost comparison across those two explicitly different statistical states.

## 11. Direct answers

| Question | Answer |
|---|---|
| 1. Same SQL workload and intended definitions? | **Yes**, for the five persisted query IDs and the selected MCV definition. |
| 2. Are distinct payload realizations permitted? | **Yes**, Protocol v2 permits stock and catalogless payload bytes to differ; it does not permit silently ignoring relation-scale or ordinary-state differences. |
| 3. Are estimate differences sufficiently understood for planning-time interpretation? | **No, not yet**. Relation-scale replay is strongly implicated, but the physical catalog state and complete GUC state were not retained. |
| 4. Is complete-lifecycle cost comparison defensible without estimator equivalence? | **Yes, conditionally**, if the state boundary is recorded and claims stay at mechanism-cost scope. |
| 5. Is correction necessary before formal timing? | **Yes**, a narrow instrumentation/state-boundary correction and fresh limited preflight are required; no scientific semantic change is required. |

## 12. Final recommendation

**CONDITIONAL GO:** The observed discrepancy is not evidence that the
catalogless adapter failed to load the intended candidate, and it does not
invalidate the timing question by itself. However, the catalogless sandbox's
intentional `581012` relation replay versus the physical arm's unrecorded
sample-scale relation state is a material comparability gap. Formal Forest10
timing should not begin until that gap is either equalized or explicitly
registered as part of the cost boundary, with relation metadata and GUC
inventories persisted. The old preflight remains valid as
`integration-readiness-only` evidence and must not be promoted to an accuracy
result.
