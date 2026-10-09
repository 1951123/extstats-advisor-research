# RQ1b four-dataset evidence review

This document is an offline evidence review of the four published RQ1b
children. It is not a LaTeX manuscript revision and contains no new database,
Advisor, planner, or workload execution.

## Research question and protocol

RQ1b asks whether statistics selected from the audited `valid` workload remain
useful for cardinality estimation on the held-out `test` workload generated
under the same AreCEL contract. Selection used only valid-side evidence;
`S_valid` was sealed before test-side resolution. Evaluation used fresh stock
PostgreSQL deployment and one `EXPLAIN (FORMAT JSON)` observation per test
instance. The secondary strict-unseen analysis filters those same observations
using the preregistered exact-source-query membership. It is not a workload
drift or distribution-shift experiment.

The machine-readable synthesis is
`experiments/rq1-workload-generalization-cross-dataset-v1.json`. Its semantic
digest is recorded in the registry and is recomputed from the four immutable
children, their RQ1a baselines, source audit, and strict-unseen membership.

## Four-dataset primary results

Relative changes are `(S_valid mean - baseline mean) / baseline mean`; negative
values are lower q-error. Values below are rounded for reading; the JSON
artifact retains full precision.

| Dataset | k | PG16-default mean | PG16-target10000 mean | S_valid mean | Δ default | Δ target10000 | default improved/tied/worsened | strict-unseen n |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Census13 | 7 | 5.232280 | 5.246510 | 2.236480 | -57.26% | -57.37% | 4936 / 2909 / 2155 | 9386 |
| Forest10 | 7 | 7.143716 | 7.149641 | 6.676028 | -6.55% | -6.62% | 4322 / 2811 / 2867 | 10000 |
| Power7 | 7 | 127.503216 | 130.814929 | 64.363603 | -49.52% | -50.80% | 5719 / 575 / 3706 | 10000 |
| DMV11 | 5 | 276.867891 | 282.164385 | 40.310494 | -85.44% | -85.71% | 6518 / 1027 / 2455 | 8138 |

All four means are lower for `S_valid` than both immutable baseline arms. This
does not mean every query improved: all four datasets contain worsened paired
queries.

The exact p50/p95/p99/max pairs `(baseline, S_valid)` are:

| Dataset | Default p50 / p95 / p99 / max | S_valid p50 / p95 / p99 / max | Target10000 p50 / p95 / p99 / max |
|---|---|---|---|
| Census13 | 1.405687 / 18.502273 / 57 / 1634 | 1.190257 / 6 / 15.505 / 106 | 1.402518 / 18.551515 / 58 / 1635 |
| Forest10 | 1.216553 / 16.853347 / 71 / 9705 | 1.183878 / 15 / 64.505 / 9343 | 1.208397 / 17 / 71 / 9374 |
| Power7 | 1.071228 / 14.975266 / 235.02 / 153148 | 1.055720 / 13.579258 / 162.505 / 155314 | 1.064527 / 14.950405 / 235.064545 / 157746 |
| DMV11 | 1.200639 / 85.575 / 3263.855 / 112010 | 1.111581 / 39.016667 / 801.201176 / 25952.467 | 1.185814 / 78 / 3255.08 / 112010 |

The published design artifacts report `local-optimum` termination for all four
children. The selected sizes were 7, 7, 7, and 5 respectively. Candidate
universe cardinality and elapsed search time were not retained in the
published design artifact and are therefore reported as unavailable rather
than reconstructed or inferred.

## Full versus strict-unseen

| Dataset | Full n | Strict-unseen n | Default full → strict mean | S_valid full → strict mean | Strict Δ default |
|---|---:|---:|---:|---:|---:|
| Census13 | 10000 | 9386 | 5.232280 → 5.504350 | 2.236480 → 2.314713 | -57.95% |
| Forest10 | 10000 | 10000 | 7.143716 → 7.143716 | 6.676028 → 6.676028 | -6.55% |
| Power7 | 10000 | 10000 | 127.503216 → 127.503216 | 64.363603 → 64.363603 | -49.52% |
| DMV11 | 10000 | 8138 | 276.867891 → 317.045909 | 40.310494 → 49.097048 | -84.51% |

The positive mean reduction remains on the strict-unseen subsets for Census13
and DMV11. Forest10 and Power7 have strict-unseen equal to the complete test
population, so they provide no within-dataset population contrast.

For Census13, the strict subset has 614 seen-in-valid instances removed; for
DMV11 it has 1862 removed. These differences are exactly the preregistered
membership definition, not new splits or a claim of distribution shift.

## Paired regressions and tails

The default-arm worsened fractions are 21.55% (Census13), 28.67%
(Forest10), 37.06% (Power7), and 24.55% (DMV11). Thus the mean reduction is
not a uniform per-query effect. The p95 and p99 values fall for every dataset
against the default arm, while maximum q-error falls for Census13, Forest10,
and DMV11 but rises slightly for Power7. The artifact also records an offline
top-1%- and top-10%-of-q-error mass diagnostic. This is descriptive evidence
consistent with fewer severe errors, not a causal mechanism or an additional
acceptance criterion.

## Recommendation comparison

The identifier-level `S_valid`/historical `S_test` intersections are zero for
all four children. Counts are `(S_valid, S_test) = (7,7)` for Census13,
`(7,8)` for Forest10, `(7,7)` for Power7, and `(5,5)` for DMV11; the resulting
Jaccard values are all zero.

The synthesis labels this comparison **identifier-only**. Candidate IDs are
derived from independently constructed snapshot/candidate-universe instances,
and the immutable evidence does not provide enough normalized physical
definition metadata to prove that zero ID overlap means zero PostgreSQL
definition overlap. Historical `S_test` is an in-workload reference, not an
oracle, optimum, or upper bound.

## Census13 baseline caveat

Census13 preserves its audited historical compatibility exception: its RQ1a
baseline was truth-rebound from historical evidence, with the historical
workload representation distinguished from the current RQ1b extraction. The
historical `pg16-default` policy inherits PostgreSQL's default column target
(`-1`), while `pg16-target10000` explicitly uses 10000. The RQ1b selection
itself uses the frozen target 100. The synthesis retains these policies instead
of normalizing them away.

## Supported conclusions and limitations

Subject to the independent offline gates, the evidence supports the narrower
claim that valid-workload-selected native extended statistics reduced held-out
mean cardinality q-error relative to the historical PG16 baselines on these
four audited AreCEL datasets. The magnitude is heterogeneous, individual
regressions remain, and maximum-tail behavior is not uniformly improved.

It does not support claims about workload drift, distribution shift, arbitrary
SQL generators, execution time, global optimality, or a causal explanation for
the observed reductions. Four datasets are distinct experimental units and
their q-errors must not be pooled into one headline mean. Cross-dataset
interpretation should remain descriptive; paper-level wording and LaTeX edits
require separate review.
