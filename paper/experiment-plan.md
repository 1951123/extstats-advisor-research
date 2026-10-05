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
- **Research harness:** exact committed SHA required in each manifest.
- **Freeze gate:** `TODO(PAPER-FREEZE)`: select final confirmatory research,
  advisor, patched PostgreSQL, and stock PostgreSQL source/version/build SHAs.
  Do not silently substitute the current manuscript commit.
- **Research harness:** this repository, with a clean committed tree required
  before a canonical run.
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

## RQ1 — Effectiveness

**Question:** How much cardinality-estimation error can workload-optimized
native extended statistics eliminate?

**Hypothesis:** The planner-in-the-loop recommendation improves at least some
workload queries and lowers aggregate error relative to stock PostgreSQL and
simple selection baselines, but does not improve every query.

**Systems/configurations:**

1. stock PostgreSQL 16 baseline;
2. strong conventional stock PostgreSQL baseline: no extended-statistics
   objects, `setseed(1/123)`, one `ANALYZE`, and target 10000 on every dataset
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
of `public.dmv11`, all column targets 10000, `setseed(1/123)` with seed 123,
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
statistics state, planner build, and design are controlled?

**Hypothesis:** For equivalent native realizations under controlled state,
hypothetical and physical planner estimates agree closely for supported kinds;
remaining mismatches identify mechanism or integration limitations. Sample to
full-data utility transfer is excluded from this RQ and reported in RQ2b.

**Systems/configurations:** active catalogless design in patched PostgreSQL,
physical native objects in stock PostgreSQL, and stock baseline without the
recommendation.

**Datasets:** all datasets with an immutable SearchResult and a valid P1
transfer.

**Independent variables:** hypothetical/physical state, candidate kind, and
equivalent design membership.

**Controlled variables:** identical relation contents, workload query text and
order, equivalent native payload semantics (and payload bytes when the
representation is directly comparable), ordinary-statistics fingerprint,
statistics target, session settings, active ordering, and relation/schema
identity. Planner version/build identities are recorded explicitly; the
patched-versus-stock implementation difference is the mechanism under test,
not an uncontrolled environment change.

**Metrics:** plan-row estimate agreement, per-query q-error correlation and
rank correlation, membership-level objective difference, and explicit mismatch
categories.

**Raw artifact:** paired EXPLAIN output, active design records, physical
catalog/payload verification, and transaction logs.

**Derived artifact:** fidelity table, plan-difference classification, and
hypothetical-versus-physical estimate/error agreement plots.

**Intended paper output:** RQ3 fidelity figure and limitations table.

**Status:** `planned`.

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
| Catalogless hypothetical evaluation reproduces physical behavior under controlled equivalent realization | RQ3 | Same-data, same-design, same-payload-semantics, same-ordinary-stats hypothetical/physical comparison | planned |
| Planner-in-the-loop search adds value beyond inexpensive heuristics | RQ4 | Fair fixed-k and/or fixed-evaluation-budget ablations with declared seeds and tie-breaking | planned |
| Operational trade-offs are measurable and include truth acquisition | RQ5 | Stage timing/size/cost records separating external import from exact counting | planned |
| Deployment is stock-compatible and DBA-controlled, not a production-readiness claim | all / contract audit | Recommendation SQL, add-only ownership checks, collision fail-closed tests, deployment verification | contract established; empirical scope remains bounded |
