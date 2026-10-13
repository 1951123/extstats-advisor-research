# Foundations Oracle Integration Protocol v1

Status: `NO-GO pending exact GroundTruthSet rebinding`

## Purpose

This protocol defines the smallest research-owned integration boundary for evaluating a
specified membership of the eight-candidate Foundations subset. It does not implement a
search algorithm and does not change Greedy ADD. The PostgreSQL planner remains a black-box
oracle to the Foundations analysis.

## Bound experiment

An experiment unit binds the current Forest10 30,000-row `AdvisorSnapshot`, the current
57-candidate `CandidateUniverse`, a compatible native repository, the workload and its
weights, a `GroundTruthProvider`, the frozen source revisions, and the exact ordered
candidate subset. The current candidate subset has eight candidates and therefore 256
canonical memberships, including the empty membership. Each uncached membership requires
one configuration activation and one planner estimate for every positive-weight supported
workload query. With the current 10,000-query workload this is 2,560,000 planner-query
calls for 256 uncached configurations, not 256 SQL statements.

The experiment remains blocked until a GroundTruthSet whose source snapshot digest is
`ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5` is identified or
produced as a separately named experimental realization. The old GroundTruthSet bound to
`65cc525...` is not silently reused.

## Oracle contract

`evaluate(configuration)` accepts a membership and canonicalizes it in the declared
CandidateUniverse order. It activates the ordered `PostgresStatisticsConfiguration`, calls
the existing `PostgresPlannerSession.estimate_query()` for each positive-weight workload
query, and passes the resulting estimates to the existing `WeightedWorkloadUtility`.
The returned objective is the existing `UtilityResult.objective`, using the existing
`QErrorLoss` and GroundTruthProvider. The adapter does not inspect PostgreSQL formulas.

The empty membership is valid. A configuration identity is the SHA-256 identity of its
canonical ordered member list and is independent of Python set/dict iteration order.
Failures are returned as explicit failed observations with an error and partial query
counts; they are never converted to a numeric objective.

The adapter may cache an exact completed membership. Cache hits report zero new oracle and
planner-query calls. Cache accounting is separate from distinct configuration evaluations,
planner-query evaluations, preprocessing, and session setup/cleanup. A landscape summary
must reject failed, missing, duplicate, or mixed-provenance observations.

## Source-level reuse

The implementation reuses `PostgresPlannerSession`,
`PostgresStatisticsConfiguration`, `WeightedWorkloadUtility`, `UtilityResult`, `QErrorLoss`,
and the Advisor GroundTruthProvider contract. It intentionally does not instantiate
`PostgresSearchEvaluator`, because that production evaluator requires an
`OptimizationPlan`; no exact current-snapshot plan artifact is available. This is a
membership-evaluation adapter, not a replacement optimizer.

The one planner session owns backend-local registration and activation state. The adapter
checks the observed active configuration after activation and closes the session only at
the caller's explicit lifecycle boundary. No database connection is made on module import.

## Non-goals and safety

This protocol does not run ANALYZE, acquire new truth, run Greedy ADD, enumerate the full
57-candidate universe, or claim accuracy or algorithmic generality. A live smoke test is
permitted only after snapshot, candidate, native repository, and exact GroundTruthSet
compatibility gates pass. Explicit DSNs and the patched PostgreSQL identity remain required.
