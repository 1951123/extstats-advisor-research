# VLDB experiment plan

This document specifies the experiments behind the manuscript. It is a plan,
not a claim that every row is complete. Historical Census13, Forest10, Power7,
and DMV11 artifacts remain `pilot` or `preliminary`; the new Census13,
Forest10, Power7, and DMV11 matched-comparison artifacts are dataset-level
canonical results under the current confirmatory truth policy. The four-dataset
RQ1 matched campaign is complete only within its declared three-arm,
in-workload scope; RQ4 heuristic ablations, held-out generalization, RQ3, and
RQ5 remain separate incomplete work. The formal four-child RQ2 transfer
campaign is complete under the frozen v2 protocol; historical transfer
directories remain pilot evidence.

The immutable `rq1-cross-dataset-summary-v1.json` remains historical output.
The current paper pointer is the versioned `rq1-cross-dataset-summary-v2.json`,
which preserves the same estimates and metrics while making observations,
snapshot-bound `GroundTruthSet`, historical production-exact truth, and the
Census13 equivalence-bound external truth distinct provenance fields.

The RQ4 v2 fixed-k child results are summarized by
`experiments/rq4-fixed-k-cross-dataset-summary-v1.json` (semantic digest
`4db25845552c38f1b3b48b5cf3ec9b7637617eb162701e9444a2a2b8b189f50f`).

## Shared protocol

- **Current frozen SUT:** `paper/system-freeze-v2.json` freezes advisor
  `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`, patched PostgreSQL
  `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, and stock PostgreSQL
  `0d1c00c624fa7367d4a895f44381887757289682` at PostgreSQL 16.14.
  `system-freeze-v1.json` remains unchanged and is the provenance of historical
  RQ1 and Forest10 RQ4 artifacts; those artifacts are not silently reassigned
  to v2. A future semantic SUT change requires a new freeze.
- **Local build provenance:** when the reproducible local lab is used, record
  the role's `.runtime/postgres-lab/{stock,patched}/identity.json` as the
  build identity source. Runtime paths are not semantic identity fields.
- **Research harness:** this repository; an exact committed `research_commit_sha`
  and clean working tree are required in each canonical manifest/run. This is
  per-experiment provenance, not a permanent SUT pin.
- **Seed policy:** the experiment seed identifier is integer `123`; the exact
  PostgreSQL statement is `SELECT setseed(1.0 / 123)`. The floating-point
  expression is part of the protocol; integer-division spelling is forbidden.
- **Datasets:** AreCELearnedYet lineage currently represented by Census13,
  Forest10, Power7, and DMV11. Dataset adapter hashes, workload identity, and
  exact-truth provenance are part of the RunManifest.
- **Utility contract:** `qerror-cardinality-floor-1-v1` with
  `Q(e,t)=max(max(e,1)/max(t,1),max(t,1)/max(e,1))`; lower is better. The
  optimization objective is the `weighted-workload-mean-v1` weighted mean over
  positive-weight queries. Reporting additionally includes unweighted
  arithmetic mean, p50, p95, p99, maximum, and improved/unchanged/worsened
  query counts. With all weights one these means coincide numerically, but the
  contracts remain distinct. Cost metrics are reported in
  seconds, bytes, configuration evaluations, PostgreSQL planner query calls,
  and native catalog/storage units
  as appropriate.
- **Truth input:** `AdvisorSnapshot` and `GroundTruthSet` are separate inputs.
  Ground truth never constructs the sample/native payloads or changes the
  PostgreSQL estimator. Current source kinds are
  `production-exact-execution` and `authoritative-external-exact`.
- **AreCEL benchmark truth policy:** confirmatory RQ1/RQ2/RQ4/RQ5 runs use
  `authoritative-external-exact from audited AreCEL labels`, gated per dataset
  by `paper/benchmark-truth-policy-v1.json`. The DBMS-neutral observations are
  imported afresh into a `GroundTruthSet` for every fresh snapshot; a
  `GroundTruthSet` from another snapshot is never reused. The existing
  Census13 production-exact canary remains one-time equivalence evidence, not
  the default source for repeated benchmark runs. Production exact execution
  remains an independent validation mechanism and a measured production
  truth-acquisition cost path.
- **Label provenance:** each dataset audit binds the AreCEL upstream commit,
  source workload and `base-original-label.pkl` hashes, canonical workload
  hash, exact source-index/source-query-ID/research-query-ID mapping, dataset
  identity, split, and 10,000-query coverage. The observations wire file
  contains only `format_version`, `workload_id`, and `truths`; provenance is
  stored separately.
- **Artifact rule:** raw artifacts remain referenced by RunID and digest;
  derived artifacts must retain source manifest and commit identities.
- **Design versus realization:** RQ4a is deterministic after the sealed
  snapshot/sample, native repository, workload, truth, planner settings, and
  canonical ordering are fixed. RQ4b uses stock `CREATE STATISTICS` plus
  native `ANALYZE`, whose sampling is a stochastic realization. The exact
  seed statement does not control `ANALYZE` replayability or imply bit-identical
  statistics across fresh runs.

### System Freeze v2 scope

The v2 readiness review is recorded in
`paper/system-freeze-v2-readiness-review-v1.json`. Its singleton equivalence
claim is deliberately scoped to the confirmatory Power7 and Forest10 evidence;
it is not exhaustive cross-dataset validation. Census13 and DMV11 singleton
equivalence are explicitly `not-executed` under the v2 validation policy, with
their earlier preflight limitations retained as notes. The live incremental
Greedy evidence is a small correctness validation, not a formal 10,000-query
performance evaluation or a four-dataset speedup claim.

The v2 manifest separates semantics-preserving execution optimizations
(incidence-indexed profiling and Greedy ADD, estimate caching, and bounded
proposal caching) from the new algorithmic control represented by the maximum
selected-definition count `B` and its budget termination semantics. `B` is not
a physical maintenance-cost budget. The canonical v2 parameters are
`sample_rows=10000`, `sample_seed=42`, `statistics_target=100`, `K_s=8`,
`B=8`, and `T=300` seconds.

## Research execution order (not paper section order)

1. **Priority 0:** protocol/source freeze and the RQ3 mechanism-fidelity
   harness.
2. **Priority 1:** RQ1 matched baselines and RQ4 inexpensive heuristic
   baselines.
3. **Priority 2:** RQ2 confirmatory transfer and sample/full utility analysis.
4. **Priority 3:** RQ5 cost accounting and sensitivity analyses.
5. **Stretch:** drift/recommendation stability and additional workloads.

This is the research execution order only; it does not reorder the manuscript
sections. No item is confirmatory-complete merely because a pilot artifact
already exists.

## Experiment status ledger

The controlled vocabulary is `pilot`, `planned`, `implementation-needed`,
`ready-to-run`, `complete`, and `superseded`. Existing historical outputs stay
`pilot`/`preliminary`; dataset-level canonical artifacts may be `complete`, but
the global RQ1 campaign is not complete while any dataset remains planned.

| Experiment ID | RQ | Status | Evidence or blocker |
| --- | --- | --- | --- |
| `rq1-existing-baselines` | RQ1 | `pilot` | Existing artifacts require confirmatory provenance audit. |
| `rq1-confirmatory-matched-baselines` | RQ1 | `complete` | The four-dataset, three-arm, in-workload matched comparison is complete and summarized by `experiments/rq1-cross-dataset-summary-v2.json`; v1 remains immutable historical output and RQ4 heuristic configurations remain separate work. |
| `rq2a-existing-transfer` | RQ2a | `pilot` | Existing DMV11/related transfer artifacts are preliminary. |
| `rq2a-confirmatory-transfer` | RQ2a | `complete` | Four validated `rq2-transfer-v1` children under frozen v2 and authoritative-external-exact truth; cross-dataset summary digest `dbf6fe6f734039e6de7040406d24aa05baec4b81d1fec1ecc5280b8998a5dfc7`. |
| `rq2b-sample-full-utility` | RQ2b | `complete` | The same four children contain paired sample/full utility, 3x3 direction tables, and descriptive Spearman transfer diagnostics; summary digest `dbf6fe6f734039e6de7040406d24aa05baec4b81d1fec1ecc5280b8998a5dfc7`. |
| `rq3-primary-mechanism-fidelity` | RQ3 | `complete` | Formal artifact `experiments/rq3/rq3-primary-mechanism-fidelity-v1.json` (digest `e3304129...`) records the frozen producer SHA, v2 freeze, 3 configurations, 9 exact Plan Rows pairs, zero mismatches, payload/fingerprint controls, and cleanup. |
| `rq3-secondary-build-sanity` | RQ3 | `complete` | Formal artifact `experiments/rq3/rq3-secondary-build-sanity-v1.json` (digest `2678bb4e...`) records 3 configurations, 9 exact stock/patched physical pairs, inactive overlay, payload/fingerprint controls, and cleanup. |
| `rq4-existing-calibration` | RQ4 | `pilot` | Existing k-budget calibration is supporting diagnostic evidence only. |
| `rq4-real-backend-integration-smoke` | RQ4 | `complete` | Three-query Census13 smoke passed through the frozen patched planner, catalogless activation, frozen utility/loss, and a separate one-MCV frozen stock deployment-contract probe. Immutable evidence: `experiments/rq4/integration-smoke/rq4-real-backend-smoke-v1.json` (digest `f088ddce...`). This is integration-readiness evidence, not a formal ablation. |
| `rq4-incremental-greedy-hardening` | RQ4 | `complete` | The live v2 hardening fixture passed at `K_s=3` with `B=3,2,1`, exact bounded-reference/incremental proposal traces, empty-incidence ADD, nonincident Plan Rows audit, local-optimum, maximum-count, deadline-incomplete-round, and explicit v1 reference coverage. Evidence: `experiments/rq4/integration-smoke/advisor-greedy-incremental-hardening-v2.json` (digest `3322523889dd1b7e5f148734e0112bf31d71fcc0d1fdc7d1edbfb45a545c5743`). This is implementation-readiness evidence, not a formal RQ4 ablation. |
| `rq4-fixed-k` | RQ4 | `complete` | The historical audit remains recorded as blocked, but the definition gate is resolved by `experiments/rq4-dependency-baseline-definition-resolution-v1.json`. Census13, Power7, and DMV11 each have complete `rq4-fixed-k-v2` RQ4a/RQ4b children under the frozen v2 SUT; Forest10 remains complete historical v1 evidence and was not rerun. The global RQ4 program remains incomplete because fixed-evaluation-budget and native-ANALYZE stability are separate protocols. |
| `rq4-ks-sensitivity` | RQ4a | `complete` | `paper/top-k-screening-protocol-v2.json` (semantic digest `55c29212eabbd59dcc3539d9b4f538390305431a35181126866c383f1f15faec`) has validated formal children for Census13, Power7, and DMV11 under `K_s={4,8,16,32,all}`, `B=4`, and 300 seconds. The child artifacts are `experiments/arecel-power7/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json` (digest `85b832eae...`), `experiments/arecel-census13/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json` (digest `119dc4e886a61ee023b4045f74f43ed102c3fadc060fdb4fbd16c739188b1ee8`), and `experiments/arecel-dmv11/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json` (digest `569994babd3b4bc5d07d82a3b6bfad03ca2b13a596d8533a16d68b7ee2a74109`). The Census13 and DMV11 `all` points are reused 300-second-bounded k=2 incumbents, not optimum references. The offline aggregation `experiments/rq4-ks-sensitivity-cross-dataset-summary-v1.json` is complete; global RQ4 remains incomplete. |
| `rq4-fixed-evaluation-budget` | RQ4 | `implementation-needed` | The 2,000 configuration-objective/300-second contract is frozen, but the common budget-comparison allocator is not yet implemented. The current harness rejects this mode rather than presenting fixed-k execution as a budget comparison. |
| `rq5-cost-accounting` | RQ5 | `planned` | Requires stage timing and truth-acquisition cost artifacts. |
| `stretch-drift-stability` | Stretch | `planned` | Out of the first confirmatory execution sequence. |
| `stretch-additional-workloads` | Stretch | `planned` | Out of the first confirmatory execution sequence. |

## RQ1 — Effectiveness

**Question:** How much cardinality-estimation error can workload-optimized
native extended statistics eliminate?

**Hypothesis:** The planner-in-the-loop recommendation improves at least some
workload queries and lowers aggregate error relative to stock PostgreSQL and
simple selection baselines, but does not improve every query.

**Systems/configurations:**

1. stock PostgreSQL 16 baseline;
2. strong conventional stock PostgreSQL baseline: no extended-statistics
   objects, `SELECT setseed(1.0 / 123)`, one `ANALYZE`, and target 10000 on every dataset
   column where the paper-baseline contract defines that setting. The current
   four manifests record target 10000; Census13 records it at baseline level,
   while the newer manifests also expose more column-level verification;
3. `ExtStats Advisor` recommendation;
4. workload-frequency top-k, native-payload-size top-k,
   singleton-utility top-k, random-k, and exhaustive tiny-universe baselines
   are RQ4 ablations, not completed RQ1 three-arm configurations;
5. learned-CE results from Are We Ready, labeled literature/contextual data
   and never merged into the matched-system result table.

The current RQ1 effectiveness comparison is explicitly in-workload: the
evaluation workload is the workload used by the advisor objective. It does not
establish held-out query generalization. The three-arm matched-comparison
artifacts therefore do not imply that the RQ4 heuristic baselines have been
completed.

**Datasets:** Census13, Forest10, Power7, DMV11, subject to available exact
ground truth and an explicit dataset inclusion table.

**Independent variables:** system/configuration, dataset, candidate limit,
statistics target, and (where planned) recommendation size.

**Controlled variables:** exact frozen stock PostgreSQL source/version/build,
relation contents, workload query texts/order, ordinary statistics policy,
truth definition, hardware/environment, and timeout policy. Dataset-specific
paper-baseline differences must be recorded rather than normalized away.

**Census13 canonical result (`rq1-matched-comparison-v1`):** The historical
canary remains the immutable planner-execution source, not the final
truth-policy result. Its 10,000 production-exact labels were proven fully
equivalent to the audited AreCEL observations (10,000 matched, zero mismatch,
missing, or extra). The derived artifact
`experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json`
therefore rebinds truth/evaluation fields offline, recomputes q-error with the
frozen utility helper, and reuses historical planner estimates; no PostgreSQL
workload, `ANALYZE`, planner, or exact-count rerun was performed. Its semantic
digest is `647d09ecb0bdedce426f71c2527a5f30c3c413da4d4c670d4c5e22710b906fec`.

**Forest10 canonical result:**
`experiments/arecel-forest10/rq1-confirmatory/rq1-matched-comparison-v1.json`
uses the same three-arm schema and audited external truth contract. Frozen
parameters are sample rows 10000, sample seed 42, statistics target 100,
candidate limit 8, and a 300-second search budget; the parameter basis is the
pre-existing Forest10 canonical K=8 protocol. The result's semantic digest is
`3dbc024c6d182011f3eab3c5c333a7013a445f4f951e6cc8725086bfff3b0436`.
`PG16-default` and `PG16-target10000` are independent fresh stock states with
no physical extended statistics. `PG16-advisor` uses the frozen patched
planner only at design time and reports headline estimates after fresh stock
full-data deployment plus one native `ANALYZE`; its sandbox objective and
termination are separate fields. The result direction is empirical and is not
an acceptance target.

**Power7 canonical result:**
`experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json`
was run with the same frozen K=8 protocol: sample rows 10000, sample seed 42,
statistics target 100, candidate limit 8, a 300-second search budget, and
experiment seed identifier 123 with `SELECT setseed(1.0 / 123)`. Its
authoritative AreCEL truth is `validated-provenance` (not
`validated-full-equivalence`) and includes a ten-query live exact sanity check
whose results are not the GroundTruthSet source. The three arms used
independent `postgres-lab reinit` states; the advisor recommendation was
regenerated with the frozen advisor, and headline estimates came from the
fresh stock full-data deployment. The artifact digest is
`14a809c8677db1007fa602ead1b2b5ab362c119f8cb197724b5c5de4a2959124`.
Its sandbox objective decreased from 167.64553643195066 to
99.8095133469062 and terminated at `local-optimum`; this sandbox value is
reported separately from the stock deployment headline. The result is
diagnostic evidence for this frozen protocol, not an acceptance target based
on the historical Power7 pilot.

The committed lineage record
`experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-lineage-v1.json`
audits a metadata-only revision of this artifact. It records the historical
digest `fc823a63f2f8075882d63875a868a97129c60aacdf40edd338238c907e693750`,
the revised digest above, and the sole changed field:
`provenance.parameter_selection_basis` changed from the pre-existing Forest10
label to the pre-existing Power7 label. It verifies that estimates, truth,
q-errors, aggregate metrics, recommendation, search, and deployment evidence
were unchanged; no planner, database workload, or exact count was rerun.

**DMV11 canonical result:**
`experiments/arecel-dmv11/rq1-confirmatory/rq1-matched-comparison-v1.json`
uses the same three-arm schema, frozen SUT, K=8 parameter policy, and
authoritative external truth contract. The parameters are sample rows 10000,
sample seed 42, statistics target 100, candidate limit 8, a 300-second search
budget, experiment seed identifier 123 with `SELECT setseed(1.0 / 123)`, and
the 10,000-query `arecel_dmv11_test_v1` workload. Its truth policy is
`validated-provenance`; the existing 15-query exact check is sanity evidence
only and is not the GroundTruthSet source. The three arms started from
independent fresh stock states, the advisor recommendation was regenerated
with the frozen advisor, and headline estimates came from fresh stock full-data
deployment. The artifact digest is
`9015b7b824107c9b99e34dadcc1e50e5c1a4a8d03c2b616a5bfab04450cb82dd`.
The sandbox objective decreased from 345.7140108148528 to 72.6202657788967
and terminated at `local-optimum`; it is reported separately from the
deployment headline. The five selected objects are MCV recommendations, and
their membership is immutable in the artifact.

**Unified RQ1 result:**
`experiments/rq1-cross-dataset-summary-v2.json` is the current generated,
content-addressed four-dataset summary with semantic digest
`84ce7a91fc94ad137f1b8dfd901429d41e3ab89a84c1cece30901233f42da90c`.
It contains per-arm metrics, paired classifications, recommendation and
sandbox fields, source artifact digests, per-query regression summaries, tail
error concentration, mean/p50 trends, and explicit truth provenance roles.
Its scope is exactly
`four-dataset, three-arm, in-workload matched comparison`; it excludes RQ4
heuristics and makes no held-out query generalization claim.

The v2 truth record keeps the observations file SHA and semantic digest
separate from the snapshot-bound `GroundTruthSet` digest. For Census13 it also
records the historical production-exact `GroundTruthSet` digest, the
equivalence-bound external `GroundTruthSet` digest, the equivalence-bound
snapshot digest, and the equivalence artifact digest. The Power7 and DMV11
records retain their snapshot-bound external truth digest without inventing
equivalence evidence. The v1 summary is not overwritten and remains a
reproducible historical revision.

For every new dataset, confirmatory arms start from `postgres-lab reinit` and
import audited AreCEL observations into a fresh snapshot-bound
`GroundTruthSet`; there is no fallback to `production-exact-execution`.
`PG16-default` uses stock PostgreSQL with no extended statistics and the
audited default ordinary-statistics policy. `PG16-target10000` uses no
extended statistics, target 10000 on every dataset column, the exact
`SELECT setseed(1.0 / 123)`, and exactly one `ANALYZE`.

The unified artifact records the three arm identities, actual targets and
ANALYZE protocol, physical extended-statistics inventory, recommendation and
sandbox objective where applicable, per-query estimates/truth/q-errors, both
paired comparisons, cleanup, and semantic digests. Census13, Forest10, Power7,
and DMV11 are now `complete` at dataset level, and the generated four-dataset
matched campaign is `complete` within its declared scope. This does not
complete the RQ4 heuristic baselines.

**Metrics:** per-query q-error and the shared aggregate metrics; optional gap
closure only when the learned-CE comparison is demonstrably comparable.

**Raw artifact:** per-query planner estimate/truth rows, workload identity,
system logs, and RunManifest.

**Derived artifact:** compact baseline comparison table, q-error distribution,
and improved/unchanged/worsened classification.

**Intended paper output:** RQ1 distribution figure and summary table.

**Status:** historical benchmark runs remain `pilot`/`preliminary`; all four
dataset-level canonical artifacts and the unified three-arm comparison are
`complete` within the in-workload scope. RQ4 heuristic baselines remain
`planned`, and the global research program is not complete.

## RQ2 — Sample-to-full-data transfer

**Question:** Do designs optimized on a fixed sample retain their benefits when
native statistics are independently recomputed on full data, and how well does
the sample-sandbox utility predict that full-data utility?

**Hypothesis:** The selected membership transfers with measurable but
non-zero uncertainty; P1 improves over P0 on at least part of the workload,
and P2 isolates the contribution of the advisor-managed objects while holding
ordinary statistics fixed.

**Systems/configurations:** stock PostgreSQL P0, add-only deployment plus final
`ANALYZE` P1, and research-only removal without `ANALYZE` followed by rollback
P2. P2 is not production reconciliation.

**Datasets:** four independent formal children: Census13, Forest10, Power7,
and DMV11. The historical `full-data-transfer-k8` directories remain pilot
evidence only; each formal child must produce a new `rq2-transfer-v1` artifact
bound to the current system-freeze-v2 and producer research SHA.

**Independent variables:** P0/P1/P2 state, dataset, recommendation, and
full-data versus fixed-sample payload construction.

**Controlled variables:** population, workload, stock PostgreSQL build,
ordinary-statistics settings, query order, transaction isolation, and object
identity.

**Metrics:** paired per-query q-error, P0-to-P1 and P2-to-P1 deltas, ordinary
statistics fingerprints, extstats payload verification, deployment/rollback
cost, query outcome counts, and the relationship between sample utility and
full-data utility. Report two labels separately: **RQ2a** deployment benefit
retention and **RQ2b** sandbox-utility/full-data-utility relationship.

RQ2a reports the operational effect
`Delta_operational = J_D(P0) - J_D(P1)` and the controlled full-data
extended-statistics effect `Delta_controlled = J_D(P2) - J_D(P1)`. P2 is a
research-only transaction-local counterfactual: it drops exactly the
advisor-managed objects, performs no `ANALYZE`, and rolls back. The P1/P2
ordinary-statistics fingerprints must be identical. A regression remains a
valid completed observation; it is not a reason to change the Recommendation.

RQ2b evaluates the same final membership on the sample and full-data states.
It records `J_S(empty)`, `J_S(M)`, `J_D(P2)`, and `J_D(P1)`, the two relative
improvements, a complete 3x3 direction contingency, same/opposite/unchanged-
involved counts, and descriptive per-query Spearman correlation between
`G_S(q)=log(Q_S(empty)/Q_S(M))` and
`G_D(q)=log(Q_D(P2)/Q_D(P1))`. No p-value is claimed. A single recorded
full-data native `ANALYZE` realization is used per formal child.

The fixed-sample objective is $J_S(M)$. A full-data result is $J_D(M;A)$ for
one native `ANALYZE` realization $A$; transfer analysis must not conflate the
two with mechanism fidelity or treat native realization noise as a planner
overlay error.

**Raw artifact:** one `rq2-transfer-v1` child artifact plus its per-query
JSONL, fresh v2 source-run digests, new snapshot-bound external GroundTruthSet
provenance, Recommendation/SearchResult/profile digests, DeploymentResult,
object/catalog verification, ordinary-stat fingerprints, stage timings, and
cleanup evidence. External truth import/validation is reported separately from
planner evaluation and exact-count truth acquisition; formal RQ2 uses no
10,000-query exact recount.

**Derived artifact:** P0/P1/P2 summary table, paired scatter/violin plots, and
transfer validity report.

**Intended paper output:** RQ2a transfer table and RQ2b utility-transfer figure.

**Status:** the four formal children are complete and passed the offline RQ2
validator. Their historical `full-data-transfer-k8` counterparts remain
`pilot`/`preliminary`. The versioned cross-dataset derived artifact is
`experiments/rq2-cross-dataset-transfer-summary-v1.json` with semantic digest
`dbf6fe6f734039e6de7040406d24aa05baec4b81d1fec1ecc5280b8998a5dfc7`.

The formal children produced these actual search outcomes: Census13 selected
7 objects and terminated at `local-optimum`; Forest10 selected 8 and reached
`all-screened-candidates-selected`; Power7 selected 7 and terminated at
`local-optimum`; DMV11 selected 5 and terminated at `local-optimum`. No child
was forced to select eight objects. All four children use fresh
snapshot-bound authoritative external truth, keep external labels out of
statistics construction, and record P1/P2 ordinary-statistics fingerprint
equality. No 10,000-query exact truth recount was performed.

## RQ3 — Fidelity

**Question:** How faithfully does catalogless hypothetical evaluation reproduce
physical PostgreSQL statistics behavior when data, payload semantics, ordinary
statistics state, and design are controlled without a PostgreSQL binary/build
confound?

**Primary comparison:** physical native statistics versus catalogless
hypothetical statistics on the same patched PostgreSQL binary. The only main
factor is physical catalog realization versus hypothetical overlay realization.

**Controls:** identical PostgreSQL binary, relation contents, schema, workload,
ordinary-statistics state/fingerprint, statistics target, equivalent design,
equivalent native payload semantics, identical payload bytes where technically
possible, and planner/session settings.

**Primary metrics:** exact root `Plan Rows` agreement, mismatch count, and
mismatch classification. No near-exact threshold is pre-registered until an
empirical reason exists to tolerate non-equality. Objective difference is
derived from the paired estimates; q-error correlation is a secondary
diagnostic rather than the primary fidelity metric.

**Secondary sanity check:** patched PostgreSQL with physical statistics versus
stock PostgreSQL with physical statistics and the overlay inactive. This
checks ordinary physical-statistics semantics across builds but is not the
primary hypothetical-fidelity comparison. If the current build setup cannot
run both primary realizations on one patched binary, record the concrete
implementation/build blocker and do not change DBMS semantics to fit the plan.
Sample-to-full-data utility transfer is excluded from this RQ and reported in
RQ2b.

**Systems/configurations:** the same patched PostgreSQL binary in three
independent primary configurations—MCV only, dependencies only, and MCV plus
dependencies. Each configuration runs physical catalog realization and
catalogless overlay realization from the same extracted native payload. Stock
physical mode is used only for the secondary sanity check.

**Dataset:** the Priority-0 validation fixture
`rq3-synthetic-mcv-fd-v1`, rebuilt independently for each of the three
configurations. A valid P1 transfer is not a prerequisite for the primary
mechanism comparison.

**Independent variables:** physical/overlay realization and the three
independently rebuilt candidate designs (MCV only, dependencies only, and their
combination).

**Controlled variables:** all controls above, plus relation/schema identity and
active ordering. The patched-versus-stock build identity is recorded only for
the secondary sanity check, never substituted into the primary comparison.

**Metrics:** exact root `Plan Rows` agreement, mismatch count and
classification, and derived objective difference. Payload non-emptiness and
supported clause-form evidence are required for each candidate kind; observed
physical-versus-no-extstats estimate changes are recorded but are not asserted
as a universal `used=true` signal. Per-query q-error/rank correlation is
retained only as a secondary diagnostic.

**Formal artifact:** `experiments/rq3/rq3-primary-mechanism-fidelity-v1.json`
with `formal_experiment=true`, producer research SHA, and the
`system-freeze-v2` semantic digest. The earlier readiness artifact is retained
separately and is not renamed or promoted.

**Raw artifact:** paired EXPLAIN output, active design records, physical
catalog/payload verification, ordinary-statistics fingerprints, binary/build
identities, and transaction/session logs for both primary and secondary paths.

**Derived artifact:** fidelity table, plan-difference classification, and
hypothetical-versus-physical estimate/error agreement plots.

**Intended paper output:** RQ3 fidelity figure and limitations table.

**Artifact/status semantics:** the runner writes an artifact even when paired
Plan Rows mismatch and returns `status=artifact-created` with
`fidelity_gate=pass|fail`. `pass` means mismatch count is exactly zero. The
small live gate passed for all three configurations on the pinned patched
build, and the formal artifact now records `execution_status=complete` and
`fidelity_gate=pass` separately. The primary result contains 9 paired queries,
9 exact Plan Rows matches, and zero mismatches. The secondary
patched-versus-stock build sanity artifact likewise completed independently
with 9/9 exact pairs and an inactive overlay. Both results are limited to the
declared synthetic mechanism fixture and do not establish universal PostgreSQL
equivalence.

## RQ4 — Advisor necessity / ablation

**Question:** How much does planner-in-the-loop configuration search contribute
beyond simple heuristics?

**Hypothesis:** Greedy planner-in-the-loop search selects useful combinations
that are not fully recovered by frequency or singleton-only rules, while the
gap to exhaustive search on tiny universes quantifies search suboptimality.

**Systems/configurations:** the primary fixed-k methods are random-k,
workload-frequency top-k, native-payload-size top-k, singleton-utility
top-k, and greedy ADD. Exhaustive search is a separately reported
tiny-universe optimality diagnostic, never a default AreCEL method.

**Fairness contract:** the primary eligible universe is frozen before any
utility or outcome is observed. Eligibility uses only supported native
statistics kind, valid relation/column schema, sample-built native payload
availability, and predeclared PostgreSQL compatibility. It records exclusion
reasons and forbids singleton q-error, `SearchResult`, final recommendation,
and full-data outcome signals. A screened universe based on utility or outcome
is a separate conditional diagnostic and is never silently substituted for the
primary universe. Every method receives the identical primary universe,
relation, candidate kinds, attribute keys, and sample-built
`NativeStatsRepository`; no method may add candidates or change native
statistics targets. Random-k uses pre-registered seeds and samples only from
that universe. Workload-frequency uses workload predicate incidence but no
ground truth. Native-payload-size top-k ranks each eligible candidate by the
byte size of its sample-built native PostgreSQL extended-statistics payload;
it is a truth-free, planner-utility-free sample-side structural heuristic, not
a statistical correlation estimate. Singleton-utility top-k, greedy ADD, and
exhaustive search use the same bound `GroundTruthSet` and weighted utility/loss
contract.
All selected configurations are evaluated against the same truth after
selection.

**Comparison modes:** report two distinct comparisons. (A) The primary fixed-k
quality comparison uses pre-registered `k=4`, independently of the final
advisor recommendation; genuinely infeasible universes, at-most-k local
optima, and budget-censored searches are reported explicitly. A result using
the advisor's selected k is not an unbiased unknown-k comparison. (B) The
fixed-evaluation-budget comparison uses a common pre-registered cap of 2,000
configuration-objective evaluations and 300 seconds. A configuration
evaluation may contain many PostgreSQL per-query planner calls. Selection
preprocessing, sandbox configuration evaluations, independent final sandbox
evaluation, and stock full-data deployment/evaluation are reported as separate
cost stages; the independent stock full-data evaluation is never charged to
the selection budget. Every method reports both configuration evaluations and
actual planner query calls, wall time, and censoring reason.

The historical Forest10 v1 artifact stores this method under the identifier
`dependency-correlation-top-k`; code audit established that its actual score
was native payload byte size. Derived reports use the canonical label
`native-payload-size-top-k` without modifying the original artifact, traces,
memberships, or digests. The alias is recorded in
`experiments/rq4-dependency-baseline-definition-resolution-v1.json`.

### Generic RQ4 v2 formal campaign

The formal AreCEL children for Census13, Power7, and DMV11 use the generic
`rq4-fixed-k-v2` harness.  The frozen primary methods are five random
replicates (`random-k-seed-1` through `random-k-seed-5`),
`workload-frequency-top-k`, `native-payload-size-top-k`,
`singleton-utility-top-k`, and `greedy-ADD`; the fixed primary size is
`k=4`.  Forest10's historical v1 execution is not rerun or used as a runtime
comparison target.

The primary universe is built once, before singleton utility, GroundTruthSet,
or Greedy search is consulted.  It contains only supported native kinds,
valid relation/column metadata, sample-built payloads, and predeclared
PostgreSQL-compatible candidates.  The RQ2 v2 snapshot, authoritative
GroundTruthSet, native repository, candidate universe, and singleton profile
may be reused only when their digests and frozen SUT identities match; source
profiling cost is recorded and new selection calls are zero.

Greedy uses the frozen Advisor incidence-incremental evaluator: the incumbent
stores a complete estimate map, each ADD re-plans only incident queries, the
changed estimates are merged, and the original weighted workload utility is
evaluated on that complete map.  The first round uses cached singleton
objectives, then has at most `3N-6` live proposal configuration evaluations
for an eligible universe of size `N`.  It stops at a strict local optimum or
the fixed `300` second deadline; it never fills to four with a non-improving
ADD.  Selection accounting is separate from each unbudgeted (but individually
bounded) final sandbox evaluation and from stock physical deployment.

Each child writes `rq4-ablation-v2.json`,
`rq4-design-evaluation-v2.json`, a gzip-compressed deterministic replay, a
shared stock realization, and nine physical method children under
`physical/`.  RQ4b creates the union of all memberships once, performs exactly
one stock `ANALYZE`, then evaluates no-ANALYZE clones.  The primary truth is
the same snapshot-bound `authoritative-external-exact` vector for every
method; no new 10,000-query exact COUNT is permitted.

The three new children are complete at dataset scope: Census13 digest
`ed9031d8573f0048cbc852e5b8a236c7a29cc224b6012fd88b252700bbebba73`, Power7
digest `c092541aa381a3e4eb8660e44f870f61ad1ea81eafc2d5fe94a87b79d8e853a7`,
and DMV11 digest
`15182b79f7691890cbfa5121d7eda65bbbb02737fb5129646dcc25341330b693`.
Each has nine physical method children and one shared stock ANALYZE. Forest10's
historical v1 artifact was not rerun.

RQ1 and RQ4 remain separate questions: the RQ1 matched comparison evaluates
the advisor objective workload and therefore is an in-workload effectiveness
result, not held-out-query generalization.  The three new RQ4 v2 children pass
their design replay and physical realization gates and are marked `complete`;
the global RQ4 program remains incomplete because fixed-evaluation-budget and
native-ANALYZE stability are separate protocols.  A child method may still be
`budget-censored` internally when Greedy reaches the fixed wall-clock deadline;
that execution state is retained rather than relabeled as local optimum.

RQ4a is the primary deterministic patched-sandbox design comparison. RQ4b is
the secondary stock physical consequence comparison. The fixed-k quality
harness is executable for both evidence layers on a small fixture; the tracked
smokes are readiness evidence, not formal AreCEL results. A shared physical
realization builds the union of method memberships, performs one stock
`ANALYZE`, and evaluates no-ANALYZE clones after dropping unrelated objects.
This controlled comparison is distinct from the planned
`native-analyze-stability-v1` protocol with five independent native
realizations. The fixed-evaluation-budget mode remains
`implementation-needed` until a common budget-comparison allocator is
implemented; the harness fails closed for that mode rather than silently
reusing fixed-k selection logic.

**Evaluation accounting:** fixed-k runs must publish a declared maximum
configuration-objective budget and actual counts for every method, even when
the primary comparison does not equalize those counts. Each backend evaluation
records its PostgreSQL per-query planner calls and measured backend time;
singleton profiling records its own observed configuration/query counts and
preprocessing time. Fixed-evaluation-budget runs use the same caps for every
primary method. Tiny exhaustive diagnostics have their own declared universe
and budget and are not silently substituted for a full-universe run.

**Determinism:** random seeds are recorded per replicate; formal random-k
replicates use pre-registered seeds `[1, 2, 3, 4, 5]`. All non-random ties are
broken by candidate ID after the declared score and static precedence keys.
The same ordered configuration convention is used for planner evaluation. A
small replay smoke requires exact membership, evaluation order, planner
estimates, objectives, and configuration trace outside runtime measurements.

**Native realization stability:** `native-analyze-stability-v1` is planned,
not run. It requires five independent stock `CREATE STATISTICS` plus
`ANALYZE` realizations, preferably on Forest10 and Census13/DMV11, and reports
payload and Plan Rows variability. `setseed` is recorded as an experiment
statement but is not treated as control of native `ANALYZE` sampling.

**Datasets:** a representative subset for full ablation and synthetic/tiny
candidate universes for exhaustive comparison; dataset choice must be recorded.
The checked-in `experiments/rq4/synthetic/rq4-ablation-v1.json` is only the
implementation gate. It is not an AreCEL result and does not make RQ4
complete.

**Independent variables:** search method, candidate-universe size, k/budget,
and workload.

**Controlled variables:** candidate universe, sample/payloads, planner build,
truth/loss contract, statistics target, workload, and either fixed k or the
pre-registered evaluation budget according to the comparison mode.

**Metrics:** RQ4a objective value, membership/object count, q-error only as a
secondary sandbox diagnostic, selected membership overlap, configuration
evaluations, PostgreSQL planner calls, preprocessing and selection wall-clock
time, termination status, and exhaustive optimality gap. RQ4b separately
reports stock physical Plan Rows/q-error and native realization/evaluation
costs; neither metric layer substitutes for the other.

**Raw artifact:** method-specific SearchResult or baseline selection, move
trace, planner evaluation log, and manifest.

**Derived artifact:** ablation table, membership overlap matrix, and budget vs.
objective curve.

**Intended paper output:** RQ4 ablation table and search-budget figure.

**Status:** `ready-to-run` for formal AreCEL execution; the synthetic
implementation gate passed on a six-candidate universe (`6 choose 3 = 20`
exhaustive subsets), and a three-query real Census13 integration smoke passed
through the frozen patched PostgreSQL planner and utility backend. The smoke
is integration-readiness evidence only. Existing k-budget calibration
artifacts remain supporting diagnostics, and no formal AreCEL ablation is
complete.

### Singleton profiling and screening-width cost protocol

The advisor now has an incidence-incremental singleton profiling path, with
the previous full-workload implementation retained as a reference evaluator
for audit. The invariant is fail-closed: a full-workload audit must show that
nonincident query `Plan Rows` values are unchanged, and the incremental merged
estimate map must produce the same utility result, candidate improvements,
ties, frozen order, and semantic digest as the reference.

The pre-pruning screening width is `K_s`; it is distinct from the downstream
selected-object count `k`. All eligible candidates are profiled before the
frozen singleton order is truncated to a `K_s` prefix. Thus `K_s` is a
semantics-affecting search hyperparameter, while incidence-incremental
profiling reduces the profiling cost independently of the prefix width. The
machine-readable protocol and proposed, not-yet-run grid are in
`paper/top-k-screening-protocol-v1.json`. No formal K sweep is complete.

The v1 protocol remains immutable historical design documentation. The
follow-up `paper/top-k-screening-protocol-v2.json` preregisters the distinct
`rq4-ks-sensitivity-v1` quality--cost experiment for Census13, Power7, and
DMV11, excluding Forest10. It holds the selected-statistics budget at `B=4`
and compares `K_s={4,8,16,32,all}` under an independent 300-second search
cap and a maximum of 2,000 configuration-objective evaluations per point.
`K_s=8` is only the canonical screening-width reference under fixed `B=4`;
it is not a new canonical Advisor configuration. Every finite point uses the
first prefix of the already frozen singleton order, while `all` is reused
from a fixed-k-v2 child only after an exact digest and semantic gate. Formal
Power7, Census13, and DMV11 children have now completed their finite points
and reused their gated `all` points. Census13's and DMV11's `all` points are
300-second-bounded full-universe incumbents at `k=2`, not optimum references.
Their artifacts are
`experiments/arecel-power7/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json`,
`experiments/arecel-census13/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json`,
and `experiments/arecel-dmv11/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json`.
The dataset scope and the offline cross-dataset aggregation are complete;
global RQ4 remains incomplete. The aggregation is descriptive only and does
not retune the production configuration. Across the three formal datasets, the
width at which the best observed finite-point objective first appears is
workload-dependent: `K_s=8` for Census13, `K_s=16` for Power7, and `K_s=32`
for DMV11. Larger screening widths consistently increase search work, while
objective improvements show diminishing or workload-dependent returns. Power7
provides a completed full-universe Greedy reference; Census13 and DMV11
provide 300-second bounded full-universe incumbents. A separate non-formal frozen-v2 live smoke has
passed at `K_s=4` and `K_s=all` on a bounded Census13 fixture; its artifact
remains readiness evidence only.

All eligible candidates are profiled before any prefix is applied. The
sensitivity protocol therefore records zero new singleton-profiling work but
retains the source profile's measured accounting; it does not treat profile
reuse as zero algorithmic cost. Each future finite point reports selection
configuration evaluations, incremental planner calls, committed membership,
termination/censoring state, and one independent final sandbox evaluation.
Stock physical RQ4b evaluation is not repeated per screening width.

The immutable preflight predicts that Forest10 and Power7 are within the
300-second reference-plus-incremental validation gate; DMV11 is not, and
Census13 lacks an immutable historical wall-clock measurement. Forest10 now
has a current-Advisor historical-oracle gate with exact semantic output,
candidate-profile, frozen-order, baseline-objective, call-count, and
nonincident-audit equality in
`experiments/arecel-forest10/singleton-equivalence/advisor-singleton-incremental-historical-equivalence-v2.json`
(digest `c523c8617d2169e1886874d8fd31359320f0c8cec00817030b4afac0f60b0e41`).
Power7's existing v1 equivalence artifact remains complete evidence for the
previous incremental implementation. Census13 remains blocked because no
immutable historical reference wall-clock is available; DMV11 remains blocked
because the projected validation total is 427.91 seconds. No blocked or
unmeasured dataset is promoted to complete. The scoped v2 freeze now records
these two datasets as out-of-scope rather than as failed equivalence gates.

### Incremental Greedy ADD and maximum-statistics-count compatibility protocol

The production Advisor now has an explicit v2 search path described by
`paper/incremental-greedy-add-protocol-v1.json`. The singleton screening width
`K_s` controls the candidate prefix; the independent selected-definition budget
`B` constrains the selected count `k` with `k <= B`. `B` counts candidate
definitions, not physical catalog objects, and physical object count is reported
separately.

The v2 path retains the v1 full-workload Greedy ADD implementation as a
reference. For an incumbent membership `M` and proposal `M+c`, it activates
the proposal, replans only the positive-weight workload queries incident on
`c`, merges those estimates into the immutable incumbent estimate map, and
evaluates the complete utility. The first winner is materialized with `|W|`
baseline calls plus only `|I(c*)|` winner calls, rather than a second `|W|`
workload pass; the materialized utility must exactly equal the cached singleton
objective. A proposal cache stores only affected-query patches and objectives,
and is committed only after a whole round selects its winner, so a rejected or
deadline-incomplete round cannot mutate the incumbent. The exact invariant is
audited with full workload estimates: nonincident queries must retain identical
`Plan Rows`.

The compatibility gate compares the reference and incremental paths on the
same small live patched-PostgreSQL fixture: each proposal objective, each
round winner, accepted sequence, final membership/objective, and termination
reason must agree. The v2 hardening artifact covers `B=3`, `B=2`, and `B=1`,
empty-incidence ADD, nonincident audits, local optimum, maximum-count
termination, deadline-incomplete rounds, default v2 plans, and explicit v1
reference mode. Runtime accounting separates baseline/winner materialization,
proposal and audit planner calls, actual versus reference selection calls,
proposal-only versus end-to-end reductions, the 300-second budget, and measured
elapsed time; singleton profiling and independent final evaluation remain
separate. This is readiness and semantic-equivalence evidence only; it does not
constitute a formal RQ4 AreCEL experiment.

## RQ5 — Practicality

**Question:** What operational cost is paid for the obtained accuracy
improvement?

**Hypothesis:** Most cost is offline capture, truth acquisition, native
construction, profiling, and planner search; deployment adds native DDL,
`ANALYZE`, and storage/refresh cost. No separate learned-model
training/inference path is added to production query processing, but this is
not a zero-overhead claim.

**Systems/configurations:** stock baseline, advisor design-time stages, and
stock deployment/refresh.

**Datasets:** all completed datasets, with size-stratified reporting if the
benchmark populations permit it.

**Independent variables:** dataset size, sample size, statistics target,
candidate limit, search budget, recommendation size, and refresh interval.

**Controlled variables:** hardware, PostgreSQL builds, workload, timeout
policy, concurrency, and measurement protocol.

**Metrics:** snapshot time/size, sample rows, native build time, profiling and
search time, planner evaluation count, DDL and `ANALYZE` time, catalog/storage
size, post-deployment planning time, refresh cost, and truth acquisition cost.
For benchmark evaluation, measure authoritative label import, provenance
validation, and snapshot binding separately. For a real production truth path,
measure exact-count collection separately; do not use cheap imported AreCEL
labels as a proxy for production database work. The system adds no learned
model training/inference path to production query processing, but it can still
pay for sample capture, exact or authoritative truth acquisition, native
construction, planner search, and deployment `ANALYZE`.

**Raw artifact:** stage timings, sizes, planner counters, deployment logs,
catalog measurements, and manifest.

**Derived artifact:** cost breakdown table, accuracy-cost plot, and refresh
trend.

**Intended paper output:** RQ5 cost table and figure.

**Status:** `planned`.

## Provenance and completion criteria

An experiment can be marked `complete` only when its raw and derived artifacts
are versioned or content-addressed, the RunManifest binds all system and input
identities, the working tree was clean before the canonical run, and the
derived output can be regenerated from the declared command. A result that is
superseded remains referenced with status `superseded`; it is not silently
rewritten.

## Claim-to-evidence matrix

| Paper claim | RQ | Required evidence | Status |
| --- | --- | --- | --- |
| Native statistics can improve some workload estimates without replacing the estimator | RQ1 | Matched stock/strong-conventional/advisor per-query results and immutable manifests for all four completed datasets; cross-dataset summary v2 digest `84ce7a91fc94ad137f1b8dfd901429d41e3ab89a84c1cece30901233f42da90c` | complete for the four-dataset, three-arm, in-workload claim; RQ4 heuristics and held-out generalization remain out of scope |
| A sample-selected design can transfer to full-data native payloads | RQ2a | Four valid P0/P1/P2 transfer children, payload/object verification, paired q-error analysis, and cross-dataset summary `dbf6fe6f734039e6de7040406d24aa05baec4b81d1fec1ecc5280b8998a5dfc7` | complete for the four declared AreCEL datasets and frozen v2 protocol |
| Sample-sandbox utility predicts full-data utility to a measured degree | RQ2b | Four paired sample/full utility artifacts with 3x3 direction tables, same/opposite/unchanged-involved counts, and descriptive Spearman correlations under the same truth contract | complete descriptively; no inferential p-value |
| Catalogless hypothetical evaluation reproduces physical behavior under controlled equivalent realization | RQ3 | Same-patched-binary primary comparison with direct `Plan Rows` agreement and mismatch classification; patched-vs-stock physical sanity check is secondary | complete for the declared synthetic fixture; limited scope |
| Planner-in-the-loop search adds value beyond inexpensive heuristics | RQ4a/RQ4b | `rq4-ablation-v1`, deterministic replay artifact, shared stock union/drop artifact, then formal AreCEL fixed-k stock evaluations; RQ4b stability requires `native-analyze-stability-v1` | ready-to-run; readiness smokes only |
| Operational trade-offs are measurable and include truth acquisition | RQ5 | Stage timing/size/cost records separating external import from exact counting | planned |
| Deployment is stock-compatible and DBA-controlled, not a production-readiness claim | all / contract audit | Recommendation SQL, add-only ownership checks, collision fail-closed tests, deployment verification | contract established; empirical scope remains bounded |
