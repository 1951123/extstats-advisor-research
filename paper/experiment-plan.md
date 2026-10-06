# VLDB experiment plan

This document specifies the experiments behind the manuscript. It is a plan,
not a claim that every row is complete. Historical Census13, Forest10, Power7,
and DMV11 artifacts remain `pilot` or `preliminary`; the new Census13,
Forest10, and Power7 matched-comparison artifacts are dataset-level canonical
results under the current confirmatory truth policy. The four-dataset RQ1
campaign remains incomplete because DMV11 is still planned.

## Shared protocol

- **Frozen SUT:** `paper/system-freeze-v1.json` freezes advisor
  `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`, patched PostgreSQL
  `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, and stock PostgreSQL
  `0d1c00c624fa7367d4a895f44381887757289682` at PostgreSQL 16.14.
  A semantic SUT change requires `system-freeze-v2`; v1 is not silently
  overwritten.
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
  seconds, bytes, rows, planner evaluations, and native catalog/storage units
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

## Research execution order (not paper section order)

1. **Priority 0:** protocol/source freeze, then the RQ3 mechanism-fidelity
   harness.
2. **Priority 1:** RQ1 matched baselines, then RQ4 inexpensive heuristic
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
| `rq1-confirmatory-matched-baselines` | RQ1 | `planned` | Census13, Forest10, and Power7 are complete as dataset-level canonical artifacts; DMV11 remains planned, so the global campaign is incomplete. The heuristic configurations remain RQ4 work. |
| `rq2a-existing-transfer` | RQ2a | `pilot` | Existing DMV11/related transfer artifacts are preliminary. |
| `rq2a-confirmatory-transfer` | RQ2a | `planned` | Requires frozen Recommendation, stock build, and P0/P1/P2 evidence. |
| `rq2b-sample-full-utility` | RQ2b | `planned` | Requires paired utility artifacts under one truth contract. |
| `rq3-primary-mechanism-fidelity` | RQ3 | `ready-to-run` | Live synthetic gate passed on the pinned patched build: three configurations, 9 exact Plan Rows pairs, zero mismatches, cleanup verified; this is readiness evidence, not a completed paper experiment. |
| `rq3-secondary-build-sanity` | RQ3 | `ready-to-run` | Small synthetic artifact passed: 3 configurations, 9 exact Plan Rows pairs, payload/ordinary-stat correspondence, overlay inactive, and cleanup verified; readiness evidence only. |
| `rq4-existing-calibration` | RQ4 | `pilot` | Existing k-budget calibration is supporting diagnostic evidence only. |
| `rq4-fixed-k` | RQ4 | `planned` | Requires pre-registered k, seeds, tie-breaking, and common candidate universe. |
| `rq4-fixed-evaluation-budget` | RQ4 | `planned` | Requires common planner-evaluation and wall-clock caps. |
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
4. workload-frequency top-k, dependency/correlation top-k,
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
`fc823a63f2f8075882d63875a868a97129c60aacdf40edd338238c907e693750`.
Its sandbox objective decreased from 167.64553643195066 to
99.8095133469062 and terminated at `local-optimum`; this sandbox value is
reported separately from the stock deployment headline. The result is
diagnostic evidence for this frozen protocol, not an acceptance target based
on the historical Power7 pilot.

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
paired comparisons, cleanup, and semantic digests. Census13, Forest10, and
Power7 are now `complete` at dataset level; DMV11 remains `planned`, so the
global RQ1 status stays `planned` until all four artifacts are complete.

**Current DMV11 protocol to reproduce (do not run in this unit):** 11 columns
of `public.dmv11`, all column targets 10000, experiment seed identifier 123,
`SELECT setseed(1.0 / 123)`,
one `ANALYZE public.dmv11`, zero physical extended-statistics objects, stock
PostgreSQL, the AreCELearnedYet base:test workload with 10,000 test queries,
and `authoritative-external-exact` labels bound to the audited workload,
dataset identity, source revision, and current snapshot. It is a protocol
description, not a claim that a DMV11 RQ1 run was executed in this unit.

**Metrics:** per-query q-error and the shared aggregate metrics; optional gap
closure only when the learned-CE comparison is demonstrably comparable.

**Raw artifact:** per-query planner estimate/truth rows, workload identity,
system logs, and RunManifest.

**Derived artifact:** compact baseline comparison table, q-error distribution,
and improved/unchanged/worsened classification.

**Intended paper output:** RQ1 distribution figure and summary table.

**Status:** historical benchmark runs remain `pilot`/`preliminary`; Census13,
Forest10, and Power7 are `complete` dataset-level canonical artifacts, while
DMV11 and the unified four-dataset comparison remain `planned`.

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

**Datasets:** each benchmark with an immutable recommendation and a full-data
transfer path; DMV11 currently has an explicit full-data-transfer artifact.

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

**Raw artifact:** deployment result, per-query transfer JSONL, object/catalog
verification, ordinary-stat fingerprints, and source digests.

**Derived artifact:** P0/P1/P2 summary table, paired scatter/violin plots, and
transfer validity report.

**Intended paper output:** RQ2a transfer table and RQ2b utility-transfer figure.

**Status:** `pilot`/`preliminary` for current DMV11 and related artifacts;
`planned` for final cross-dataset reporting.

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
build, so the primary protocol is now `ready-to-run`; it is not an official
benchmark result and is not `complete`. The secondary patched-versus-stock
build sanity path now has `ready-to-run` readiness evidence in the independent
`rq3-build-sanity-v1` artifact. Its passing synthetic gate is not a completed
confirmatory benchmark result.

## RQ4 — Advisor necessity / ablation

**Question:** How much does planner-in-the-loop configuration search contribute
beyond simple heuristics?

**Hypothesis:** Greedy planner-in-the-loop search selects useful combinations
that are not fully recovered by frequency or singleton-only rules, while the
gap to exhaustive search on tiny universes quantifies search suboptimality.

**Systems/configurations:** random-k, workload-frequency top-k,
correlation/dependency top-k, singleton-utility top-k, greedy ADD, and
exhaustive search on small candidate universes.

**Fairness contract:** every method receives the identical frozen candidate
universe, relation, candidate kinds, attribute keys, and sample-built
`NativeStatsRepository`. No method may add candidates or change native
statistics targets. Random-k uses pre-registered seeds and samples only from
that universe. Workload-frequency uses workload predicate frequency but no
ground truth. Correlation/dependency top-k uses the declared sample-side
candidate/profile signal but no ground truth. Singleton-utility top-k,
greedy ADD, and exhaustive search use the same bound `GroundTruthSet` and the
same weighted utility/loss contracts. All methods are evaluated against the
same truth after selection.

**Comparison modes:** report two distinct comparisons. (A) In fixed-k quality
comparison, pre-register k values independently of the final advisor
recommendation; a result using the advisor's selected k is labeled explicitly
as fixed-k and is not presented as an unbiased unknown-k comparison. (B) In
fixed-evaluation-budget comparison, pre-register a common planner-evaluation
and wall-clock budget; selection methods may stop early, but unused budget is
not silently converted into extra information. Every method reports its actual
planner evaluations and wall time.

**Planner evaluation budget:** fixed-k runs must publish a declared maximum
evaluation budget and actual evaluation count for every method, even when the
primary comparison does not equalize those counts. Fixed-evaluation-budget
runs must use the same pre-registered planner-evaluation and wall-clock caps
for every method, including random and exhaustive variants where feasible;
infeasible variants are reported as censored rather than silently granted a
larger budget.

**Determinism:** random seeds are recorded per replicate; all non-random ties
are broken by candidate ID after the declared score and static precedence keys.
The same ordered configuration convention is used for planner evaluation.

**Datasets:** a representative subset for full ablation and synthetic/tiny
candidate universes for exhaustive comparison; dataset choice must be recorded.

**Independent variables:** search method, candidate-universe size, k/budget,
and workload.

**Controlled variables:** candidate universe, sample/payloads, planner build,
truth/loss contract, statistics target, workload, and either fixed k or the
pre-registered evaluation budget according to the comparison mode.

**Metrics:** objective value, q-error distribution, selected membership overlap,
planner evaluations, wall-clock time, and exhaustive optimality gap.

**Raw artifact:** method-specific SearchResult or baseline selection, move
trace, planner evaluation log, and manifest.

**Derived artifact:** ablation table, membership overlap matrix, and budget vs.
objective curve.

**Intended paper output:** RQ4 ablation table and search-budget figure.

**Status:** `planned`; existing k-budget calibration artifacts are supporting
diagnostics, not a completed ablation.

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
| Native statistics can improve some workload estimates without replacing the estimator | RQ1 | Matched stock/strong-conventional/advisor per-query results and immutable manifests for each completed dataset; heuristic arms and remaining datasets still required | partially evidenced by complete Census13/Forest10 artifacts; global claim planned |
| A sample-selected design can transfer to full-data native payloads | RQ2a | Valid P0/P1/P2 transfer, payload/object verification, paired q-error analysis | pilot/preliminary |
| Sample-sandbox utility predicts full-data utility to a measured degree | RQ2b | Paired sample/full utility and correlation analysis under the same truth contract | planned |
| Catalogless hypothetical evaluation reproduces physical behavior under controlled equivalent realization | RQ3 | Same-patched-binary primary comparison with direct `Plan Rows` agreement and mismatch classification; patched-vs-stock physical sanity check is secondary | ready-to-run |
| Planner-in-the-loop search adds value beyond inexpensive heuristics | RQ4 | Fair fixed-k and/or fixed-evaluation-budget ablations with declared seeds and tie-breaking | planned |
| Operational trade-offs are measurable and include truth acquisition | RQ5 | Stage timing/size/cost records separating external import from exact counting | planned |
| Deployment is stock-compatible and DBA-controlled, not a production-readiness claim | all / contract audit | Recommendation SQL, add-only ownership checks, collision fail-closed tests, deployment verification | contract established; empirical scope remains bounded |
