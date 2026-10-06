# VLDB experiment plan

This document specifies the experiments behind the manuscript. It is a plan,
not a claim that every row is complete. Existing Census13, Forest10, Power7,
and DMV11 artifacts are marked `pilot` or `preliminary` until the confirmatory
protocol, provenance audit, and derived-artifact checks are complete.

## Shared protocol

- **Advisor:** `1951123/extstats-advisor`, exact SHA required in each manifest.
- **Patched planner:** `1951123/postgresql-pgextadv`, exact SHA required in
  each manifest; design-time only.
- **Stock planner:** exact source/version/build identity required in each
  manifest, including server version and build provenance; do not use the
  phrase "PostgreSQL 16.14 or an explicitly recorded commit" in a final paper
  result.
- **Local build provenance:** when the reproducible local lab is used, record
  the role's `.runtime/postgres-lab/{stock,patched}/identity.json` as the
  build identity source. This is a provenance mechanism, not a resolution of
  the `TODO(PAPER-FREEZE)` source-identity gate.
- **Research harness:** this repository; an exact committed SHA and clean
  working tree are required in each canonical manifest/run.
- **Freeze gate:** `TODO(PAPER-FREEZE)`: select final confirmatory research,
  advisor, patched PostgreSQL, and stock PostgreSQL source/version/build SHAs.
  Do not silently substitute the current manuscript commit.
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
- **Artifact rule:** raw artifacts remain referenced by RunID and digest;
  derived artifacts must retain source manifest and commit identities.

## Research execution order (not paper section order)

1. **Priority 0:** protocol/source freeze, then the RQ3 mechanism-fidelity
   harness.
2. **Priority 1:** RQ1 matched baselines, then RQ4 inexpensive heuristic
   baselines.
3. **Priority 2:** RQ2 confirmatory transfer and the sample/full utility
   analysis.
4. **Priority 3:** RQ5 cost accounting and sensitivity analyses.
5. **Stretch:** drift/recommendation stability and additional workloads.

This is the research execution order only; it does not reorder the manuscript
sections. No item is confirmatory-complete merely because a pilot artifact
already exists.

## Experiment status ledger

The controlled vocabulary is `pilot`, `planned`, `implementation-needed`,
`ready-to-run`, `complete`, and `superseded`. Existing historical outputs stay
`pilot`/`preliminary`; no current entry is promoted to `complete` in this
specification.

| Experiment ID | RQ | Status | Evidence or blocker |
| --- | --- | --- | --- |
| `rq1-existing-baselines` | RQ1 | `pilot` | Existing artifacts require confirmatory provenance audit. |
| `rq1-confirmatory-matched-baselines` | RQ1 | `planned` | Awaiting source-identity freeze and matched comparison run. |
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
3. workload-frequency top-k;
4. dependency/correlation top-k;
5. singleton-utility top-k;
6. `ExtStats Advisor` recommendation;
7. learned-CE results from Are We Ready, labeled literature/contextual data
   and never merged into the matched-system result table.

**Datasets:** Census13, Forest10, Power7, DMV11, subject to available exact
ground truth and an explicit dataset inclusion table.

**Independent variables:** system/configuration, dataset, candidate limit,
statistics target, and (where planned) recommendation size.

**Controlled variables:** exact frozen stock PostgreSQL source/version/build,
relation contents, workload query texts/order, ordinary statistics policy,
truth definition, hardware/environment, and timeout policy. Dataset-specific
paper-baseline differences must be recorded rather than normalized away.

**Current DMV11 protocol to reproduce (do not run in this unit):** 11 columns
of `public.dmv11`, all column targets 10000, experiment seed identifier 123,
`SELECT setseed(1.0 / 123)`,
one `ANALYZE public.dmv11`, zero physical extended-statistics objects, stock
PostgreSQL, the AreCELearnedYet base:test workload with 10,000 test queries,
and `authoritative-external-exact` labels bound to the audited workload and
dataset identity.

**Metrics:** per-query q-error and the shared aggregate metrics; optional gap
closure only when the learned-CE comparison is demonstrably comparable.

**Raw artifact:** per-query planner estimate/truth rows, workload identity,
system logs, and RunManifest.

**Derived artifact:** compact baseline comparison table, q-error distribution,
and improved/unchanged/worsened classification.

**Intended paper output:** RQ1 distribution figure and summary table.

**Status:** `pilot` for existing benchmark runs; `planned` for the unified
confirmatory comparison.

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
Report authoritative external truth import/validation separately from
production exact truth collection/counting; do not use the former's low import
cost as a proxy for the latter's database work.

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
| Native statistics can improve some workload estimates without replacing the estimator | RQ1 | Matched stock/strong-conventional/heuristic/advisor per-query results and immutable manifests | planned; existing artifacts are pilot |
| A sample-selected design can transfer to full-data native payloads | RQ2a | Valid P0/P1/P2 transfer, payload/object verification, paired q-error analysis | pilot/preliminary |
| Sample-sandbox utility predicts full-data utility to a measured degree | RQ2b | Paired sample/full utility and correlation analysis under the same truth contract | planned |
| Catalogless hypothetical evaluation reproduces physical behavior under controlled equivalent realization | RQ3 | Same-patched-binary primary comparison with direct `Plan Rows` agreement and mismatch classification; patched-vs-stock physical sanity check is secondary | ready-to-run |
| Planner-in-the-loop search adds value beyond inexpensive heuristics | RQ4 | Fair fixed-k and/or fixed-evaluation-budget ablations with declared seeds and tie-breaking | planned |
| Operational trade-offs are measurable and include truth acquisition | RQ5 | Stage timing/size/cost records separating external import from exact counting | planned |
| Deployment is stock-compatible and DBA-controlled, not a production-readiness claim | all / contract audit | Recommendation SQL, add-only ownership checks, collision fail-closed tests, deployment verification | contract established; empirical scope remains bounded |
