# Foundations 001: Black-Box Oracle Contract

Status: `design-audit`

This document records the narrowest reliable interface for the interaction-landscape
study. It is a contract audit, not an experimental result. The PostgreSQL execution
environment remains the empirical oracle; the set-function analysis below does not
assume PostgreSQL's selectivity formulas.

## Frozen implementation bindings

The production API audit was performed against `extstats-advisor` commit
`e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`. The existing Forest10 native repository
and snapshot bindings used by the planned oracle are the frozen historical bindings
recorded in the v3 RQ5 manifests:

- Advisor: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.
- Patched PostgreSQL: `1951123/postgresql-pgextadv`, commit
  `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, PostgreSQL 16.14.
- Snapshot semantic digest: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- Candidate-universe semantic digest: `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`.
- Frozen native repository semantic digest: `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.

The implementation and artifact bindings must be revalidated at execution time. A
manifest mismatch is a failed run, not a reason to substitute a nearby artifact.

## Contract components

### Snapshot and workload

`AdvisorSnapshot` is the frozen, typed input model in
`extstats_advisor/snapshot/model.py`. `load_snapshot()` in
`extstats_advisor/snapshot/bundle.py` validates and loads its serialized form. The
snapshot supplies the relation schema, workload SQL, query IDs, weights, sample
metadata, and population metadata. The study currently remains within the supported
single-table selection-query scope.

### Candidate structure

`CandidateUniverse` in `extstats_advisor/candidates/universe.py` is the authoritative
candidate/incidence model. `load_candidate_universe()` loads the serialized universe;
its semantic digest and ordered candidate records bind identity, columns, native
kind, and query incidence. Query profiles are used for scope validation, not as an
estimator formula.

### Native realization and configuration activation

`materialize_native_stats()` in
`extstats_advisor/dbms/postgres/native_stats.py` constructs the fixed native payload
repository once from the declared snapshot/sample. The persisted repository is
loaded and checked by `load_native_stats_repository()` and
`validate_native_stats_repository_compatibility()` in
`extstats_advisor/native_stats/repository.py`.

`PostgresPlannerSession` in `extstats_advisor/dbms/postgres/planner.py` is the
black-box planner interface. `open()` registers the repository in one backend-local
session; `activate()` changes the ordered active virtual OID list; `estimate_query()`
executes `EXPLAIN (VERBOSE, FORMAT JSON)` and returns the managed base-relation
`Plan Rows`. Empty membership is represented by an empty ordered configuration. The
session may be reused across all subset evaluations only when the run binds one
immutable repository, snapshot, planner-session contract, and reset/activation policy.

The existing `PostgresSearchEvaluator.__call__()` in
`extstats_advisor/dbms/postgres/search.py` is the shortest production-compatible
route for an arbitrary membership: it orders the requested set using an
`OptimizationPlan`, activates it, replays the selected workload, and returns a
`UtilityResult`. The exhaustive harness should call this evaluator (or the same
`PostgresPlannerSession` and utility boundary directly), not reproduce planner or
selection logic in the research repository. A separate bounded implementation
smoke test is required before any 256-configuration run.

### Truth and loss

`GroundTruthSet` in `extstats_advisor/ground_truth/model.py` and
`ArtifactGroundTruthProvider` in `ground_truth/provider.py` provide independent
truth labels. The utility contract is `weighted-workload-mean-v1`, implemented by
`WeightedWorkloadUtility` in `extstats_advisor/utility/provider.py`. The loss is
`qerror-cardinality-floor-1-v1`, implemented by `QErrorLoss` in
`extstats_advisor/utility/loss.py`. For a complete query estimate map, the oracle
objective is the returned `UtilityResult.objective` (weighted arithmetic mean
q-error); lower is better.

The foundations set function is therefore:

```text
J(M) = UtilityResult.objective for the activated ordered configuration Γ(M)
F(M) = J(∅) - J(M)
```

The fixed native repository and deterministic ordering make repeated membership
comparisons interpretable within one run. This does not make physical realization,
sampling, query distribution, or cross-DBMS behavior identical.

## What the oracle can and cannot establish

The oracle can evaluate the empty set and arbitrary subsets of a small frozen
universe, provided every candidate is present in the repository and every query is
supported by the planner-session contract. It records one configuration-oracle call
per requested membership and one planner call per positive-weight selected query.
Cached records are explicit and are not counted as new online oracle calls.

The oracle does not establish a formula for the estimator, query execution runtime,
or global optimality over the 57-candidate universe. Native payloads, planner state,
and workload/truth contracts are fixed inputs to the finite experiment. An empty or
failed activation must be represented as an explicit observation; it must never be
converted to an objective of zero, infinity, or a guessed baseline.

## Provenance boundary

The existing RQ4/RQ5 artifacts are historical system contracts, not new evidence
for the foundations paper. The planned manifest must bind the exact snapshot,
candidate-universe, native-repository, workload, truth set, source revisions, loss
and utility versions, and research producer commit. Any live PostgreSQL observation
will be labeled `exploratory` until a complete finite landscape and all protocol
integrity checks pass. Synthetic set-function tests are independent correctness
evidence and must never be reported as PostgreSQL findings.
