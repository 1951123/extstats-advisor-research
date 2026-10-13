# Structural Analysis Report v1

Classification: `synthetic`; PostgreSQL execution status: `blocked`.

## Completed

The research-owned analyzer and synthetic tests cover the complete analysis surface:

- exact subset enumeration and canonical configuration identity;
- rejection of missing, duplicate, non-canonical, and failed observations;
- improvement transformation from lower-is-better objective values;
- monotonicity edge witnesses;
- unconditional and context-conditioned pairwise interactions;
- finite-universe submodularity checks;
- weak-singleton/positive-conditional-marginal complementarity witnesses;
- exact-`k`, at-most-`k`, and unrestricted-within-`C'` optima; and
- oracle-assisted Greedy ADD and deterministic Random Search budget traces.

All calculations operate on supplied objective values and do not call PostgreSQL.
The test fixture includes monotone, complementary, and interacting synthetic
functions. These are implementation evidence, not database findings.

## No empirical claims yet

There are no Forest10 objective observations in this package. Consequently the
following remain unresolved:

- monotonicity of `F` under the frozen native oracle;
- positive, negative, or context-dependent interactions;
- finite-universe gaps between singleton ranking, Greedy ADD, Random Search, and
  exact optima; and
- correlation between interactions and structural metadata.

The eight-candidate manifest is not a global search over the original 57 candidates.
Any future result must preserve the finite-universe and single-realization scope,
report failed observations, and separate oracle-assisted replay from online budget
claims.
