# Foundations Interaction Landscape v1: Claims and Next Steps

## Claims ledger

### Proven by source audit

- The existing Advisor separates snapshot/candidate/truth models from planner
  evaluation and utility calculation.
- `PostgresPlannerSession` activates an ordered candidate configuration and obtains
  native planner estimates through `EXPLAIN (VERBOSE, FORMAT JSON)`.
- `WeightedWorkloadUtility` and `QErrorLoss` provide explicit, reusable objective
  contracts; the research package does not reimplement them.
- The existing planner/session boundary can represent the empty configuration and
  arbitrary supported memberships, subject to exact artifact and query-scope checks.

### Proven by synthetic tests only

- The offline analyzer enumerates finite subsets deterministically.
- It rejects missing, duplicate, failed, or non-canonical observations.
- Its monotonicity, interaction, submodularity, complementarity, finite-optimum,
  and budget-trace calculations run on synthetic set functions.

### Not yet observed

- Whether the Forest10 oracle is monotone.
- Whether positive or negative interactions occur in the selected eight candidates.
- Whether the finite optimum differs from Greedy ADD or singleton ranking.
- Whether structure predicts interaction strength.
- Any cross-DBMS or universal algorithmic conclusion.

## Ranked next steps

1. **Required before empirical execution:** load and validate the exact matching
   `AdvisorSnapshot`, `CandidateUniverse`, native repository, and `GroundTruthSet`;
   bind their semantic digests and exact per-candidate query incidence. This is a
   provenance blocker, not an algorithmic result.
2. **Required bounded smoke test:** evaluate the empty configuration plus two or
   three selected memberships in one reused planner session, verifying activation,
   planner-call counts, utility extraction, and cleanup. Keep it `smoke-test`.
3. **Exploratory finite landscape:** execute the 256 configurations once, with no
   per-configuration ANALYZE, preserving append-only observations and stopping the
   claims at the exact bound universe.
4. **Optional robustness:** repeat only after reviewing the first complete landscape
   and protocol integrity; do not use a second run to erase an unfavorable result.
5. **Future algorithm design:** only after the landscape analysis should a new
   interaction-aware selector be specified. It must be evaluated online under a
   distinct-evaluation budget, not replayed with information leaked from the full
   landscape.

The experiment is currently **NO-GO for PostgreSQL execution** because the exact
portable artifact binding is incomplete. The protocol, offline analyzer, and
synthetic correctness evidence are complete for this checkpoint.
