# EXP-002 GO/NO-GO Review

Decision: **NO-GO for PostgreSQL smoke execution and EXP-003 full landscape execution**.

Classification: offline provenance and integration-readiness review; no database connection,
ANALYZE, truth acquisition, exhaustive enumeration, or Greedy search was executed.

## What passed

The current local runtime artifacts were validated with the Advisor loaders and validators:

- AdvisorSnapshot: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- CandidateUniverse: `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`, with 57 candidates,
  10,000 profiles, and 210,640 incidence rows.
- NativeStatsRepository: `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`,
  compatible with the current snapshot/universe and 57/57 present payloads.
- Workload: `arecel_forest10_test_v1`, 10,000 positive-weight supported queries, bound by
  the existing Forest10 workload identity.

The eight EXP-001 candidate IDs were checked against the current universe. Their current
static precedence ranks, native presence states, and per-candidate incidence counts and
digests are recorded in `002-artifact-binding-audit.md`. The selected IDs were not changed
based on any landscape outcome.

The pinned historical Advisor commit `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` contains
the required `materialize_native_stats`, `PostgresPlannerSession`, snapshot/candidate
loaders, native repository compatibility validator, and ordered `activate`/`estimate_query`
path. `PostgresPlannerSession.estimate_query` uses the existing `EXPLAIN (VERBOSE, FORMAT
JSON)` contract. The research adapter reuses this boundary and the existing
`WeightedWorkloadUtility`/`UtilityResult`/`QErrorLoss` contracts; it does not reimplement
estimation or selection.

## Blocking result

The only available Forest10 GroundTruthSet has semantic digest
`f2ea981e2e11bc600fe7a21f2d27a041c82ed0313c1021a069929c04762a12be`, but its declared
source snapshot digest is `65cc525...`. Advisor's compatibility validator rejects it
against the current snapshot with `GroundTruthValidationError: ground-truth snapshot digest
mismatch`. The associated historical SingletonProfile and OptimizationPlan have the same
old snapshot/universe binding. They are valid historical artifacts, not a current oracle
binding.

The current `BlackBoxMembershipOracle` is therefore tested only with synthetic planner,
workload, and utility doubles. It can make the empty configuration, canonical ordering,
cache accounting, planner-call accounting, partial failures, repository membership, and
digest mismatch explicit, but it must not produce Forest10 objectives until a compatible
truth artifact is bound.

## Cost and feasibility assessment

The current workload has 10,000 positive-weight supported queries. A complete eight-candidate
landscape has 256 memberships, including the empty membership. An uncached run therefore
requires 256 configuration oracle calls and 2,560,000 planner-query evaluations. This is a
count derived from the artifact contracts, not a runtime measurement. It includes neither
session setup nor preprocessing, and no precise wall-clock extrapolation is made.

Cache hits are counted separately and do not count as new planner calls. A future complete
run must preserve failed observations and reject incomplete coverage. The one-session policy
is implementable through the historical planner session, but it is not live-validated in
EXP-002 because truth compatibility is a prerequisite, not because the missing truth can be
ignored for an objective-only smoke test.

## Required next step

Locate or construct a GroundTruthSet whose source snapshot semantic digest is exactly
`ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`. If construction uses
the committed authoritative observation source, it must receive a new artifact and
experiment identity and must not be called the frozen old RQ4 realization. Re-run the
Advisor truth validator, update the binding record, and only then reconsider the bounded
smoke test. No source pin, historical artifact, or mathematical objective should be
changed to make the old truth pass.

## Final answer to the scientific question

The infrastructure currently provides a deterministic, testable black-box configuration
evaluation boundary: candidate membership is canonicalized, native activation and planner
estimation are delegated to Advisor, and utility/loss/truth are delegated to existing
contracts. It is **not yet a reproducible Forest10 objective oracle**, because the exact
current-snapshot GroundTruthSet is unresolved. The verified answer is therefore **NO-GO**
for claiming a ready empirical oracle and **NO-GO** for any EXP-003 landscape result.
