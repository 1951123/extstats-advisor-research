# VLDB experiment plan

This document specifies the experiments behind the manuscript. It is a plan,
not a claim that every row is complete. Existing Census13, Forest10, Power7,
and DMV11 artifacts are marked `pilot` or `preliminary` until the confirmatory
protocol, provenance audit, and derived-artifact checks are complete.

## Shared protocol

- **Advisor:** `1951123/extstats-advisor`, revision pinned in each manifest.
- **Patched planner:** `1951123/postgresql-pgextadv`, revision pinned in each
  manifest; design-time only.
- **Stock planner:** PostgreSQL 16.14 or an explicitly recorded stock commit;
  deployment and baseline target.
- **Research harness:** this repository, with a clean committed tree required
  before a canonical run.
- **Datasets:** AreCELearnedYet lineage currently represented by Census13,
  Forest10, Power7, and DMV11. Dataset adapter hashes, workload identity, and
  exact-truth provenance are part of the RunManifest.
- **Primary metrics:** arithmetic mean q-error, p50, p95, p99, maximum, and
  improved/unchanged/worsened query counts. Cost metrics are reported in
  seconds, bytes, rows, planner evaluations, and native catalog/storage units
  as appropriate.
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
2. stock PostgreSQL with a high statistics target;
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

**Controlled variables:** stock PostgreSQL version, relation contents,
workload query texts/order, ordinary statistics policy, truth definition,
hardware/environment, and timeout policy.

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
native statistics are independently recomputed on full data?

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
cost, and query outcome counts.

**Raw artifact:** deployment result, per-query transfer JSONL, object/catalog
verification, ordinary-stat fingerprints, and source digests.

**Derived artifact:** P0/P1/P2 summary table, paired scatter/violin plots, and
transfer validity report.

**Intended paper output:** RQ2 transfer table and figure.

**Status:** `pilot`/`preliminary` for current DMV11 and related artifacts;
`planned` for final cross-dataset reporting.

## RQ3 — Fidelity

**Question:** Can the isolated sandbox faithfully predict deployable physical
statistics designs?

**Hypothesis:** Hypothetical and physical planner behavior is sufficiently
aligned for the tested supported kinds and workload, but mismatches reveal
where sample transfer, ordinary-stat drift, or unsupported semantics matter.

**Systems/configurations:** active catalogless design in patched PostgreSQL,
physical native objects in stock PostgreSQL, and stock baseline without the
recommendation.

**Datasets:** all datasets with an immutable SearchResult and a valid P1
transfer.

**Independent variables:** hypothetical/physical state, sample/full-data
payload source, candidate kind, and recommendation membership.

**Controlled variables:** query text, relation contents, ordinary stats,
statistics target, planner version, and active ordering.

**Metrics:** plan-row estimate agreement, per-query q-error correlation and
rank correlation, membership-level objective difference, and explicit mismatch
categories.

**Raw artifact:** paired EXPLAIN output, active design records, physical
catalog/payload verification, and transaction logs.

**Derived artifact:** fidelity table, plan-difference classification, and
sample-utility/full-utility scatter plot.

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

**Datasets:** a representative subset for full ablation and synthetic/tiny
candidate universes for exhaustive comparison; dataset choice must be recorded.

**Independent variables:** search method, candidate-universe size, k/budget,
and workload.

**Controlled variables:** sample, payloads, planner build, objective,
statistics target, and evaluation budget where comparable.

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

**Hypothesis:** Most cost is offline capture, native construction, profiling,
and planner search; deployment adds native DDL/`ANALYZE` and storage/refresh
cost. No separate learned-model inference path is added to production query
processing, but this is not a zero-overhead claim.

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
size, post-deployment planning time, and refresh cost.

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
