# RQ5 What-if Evaluation Cost v3

**Status:** research-only protocol and offline harness; formal timing is not
authorized by this protocol revision.

**Protocol identity:** `rq5-whatif-evaluation-cost-v3`

**Predecessor:** [`rq5-whatif-evaluation-cost-v2.md`](rq5-whatif-evaluation-cost-v2.md),
which remains immutable historical evidence.

## 1. Question and claim boundary

For a fixed Forest10 population, eligible native extended-statistics candidate
universe, frozen configuration sequence, and SQL workload, how does repeated
physical configuration evaluation on stock PostgreSQL 16.14 compare in
lifecycle cost with catalogless evaluation on patched PostgreSQL 16.14 using a
fixed 30,000-row native-statistics sample?

The primary outcomes are complete-lifecycle cost, cumulative cost at common
configuration-count checkpoints, observed amortization, stage-level cost, and
configuration-size sensitivity. This is a mechanism-cost experiment. It does
not measure Advisor search, singleton profiling, q-error, estimator fidelity,
or a universal speedup.

The arms intentionally have different native-statistics realizations. Stock
`ANALYZE` samples the full relation using PostgreSQL's native rules; the
catalogless arm materializes payloads from one sealed 30,000-row sample and
reuses them. Payload bytes, Plan Rows, and q-error need not match.

## 2. Frozen identities and population

| Binding | Required identity |
|---|---|
| PostgreSQL | 16.14 |
| Stock PostgreSQL | `0d1c00c624fa7367d4a895f44381887757289682` |
| Patched PostgreSQL | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` |
| Historical Advisor | `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` |
| Dataset | audited `arecel-forest10`, 581,012 rows |
| Candidate universe | 57 frozen eligible candidates |
| Statistics target | 100 |
| Workload | `arecel_forest10_test_v1`, original 128-query subset |

The full source must pass the existing Forest10 audit, including CSV digest,
schema, null counts, upstream commit, and exact row count. No replacement or
synthetic Forest10 data is permitted.

## 3. Population and sample contracts

The stock arm operates on the complete 581,012-row relation. Each physical
configuration starts from an experiment-owned clean clone, creates its
statistics in the declared order, runs one native `ANALYZE`, replays the 128
queries, and cleans up.

The catalogless arm uses a sealed AdvisorSnapshot containing exactly 30,000
rows sampled from the same audited source. The v3 offline preparation recipe
is `sha256-row-rank-v1`: hash `"<seed>:<zero-based-source-row-index>"` with
SHA-256, select the 30,000 smallest hashes for seed 42, and emit those rows in
ascending source-row order. The recipe, source identity, sample size, and
sample semantic digest are recorded in each v3 manifest.

The snapshot population metadata remains 581,012 rows. The sample size is not
treated as a proxy for population size, and the existing v2 10,000-row
snapshot is not a valid v3 input.

## 4. Analyses

### Analysis A: fixed-size amortization

All 20 configurations are distinct singletons (`K=1`) in the canonical frozen
candidate order. Report cumulative totals at `N=1,5,10,20`. The complete
20-entry sequence is fixed before timing and is identical in both arms.

### Analysis B: configuration-size sensitivity

Use deterministic candidate-prefix configurations with `K=0,1,5,10,20,50`.
Analysis B is reported separately and cannot supply an amortization claim for
Analysis A.

Both analyses use three independent complete repetitions per arm. Three runs
support descriptive variability only; no significance or population
probability claim is made.

## 5. Cost boundaries

The primary boundary is **evaluation-ready population**. Common one-time
provisioning of the full Forest10 population is excluded, but all
method-specific work after that boundary is included.

Physical stages are clone/reset, statistics DDL, native `ANALYZE`, EXPLAIN,
statistics removal, clone destruction, and final cleanup. Catalogless stages
are deterministic sample acquisition/sealed-snapshot preparation, repository
materialization, sandbox preparation (including its required ANALYZE),
registration, ordered activation, EXPLAIN, deactivation, and final cleanup.

For completed configuration count `N`:

\[
T_P(N)=P_P+\sum_{i=1}^{N}C_{P,i},\qquad
T_H(N)=P_H+\sum_{i=1}^{N}C_{H,i}.
\]

`P` includes mandatory arm preparation; `C` includes configuration-specific
work and cleanup. EXPLAIN and ANALYZE operation counts are reported separately.
An observed amortization point exists only at a measured common checkpoint
where `T_H(N) < T_P(N)`. No crossover is extrapolated.

A secondary prepared-state view may subtract explicitly named preparation
stages, but it is never presented as the complete lifecycle result.

## 6. Sampling and PostgreSQL behavior

The physical arm does not force a 30,000-row sample. PostgreSQL 16.14's
effective extended-statistics sampling request is verified from source and
recorded for each configuration, including column-level target interactions.
If native ANALYZE's effective sample differs from the nominal rule, the actual
rule and recorded diagnostics are reported. The benchmark does not require
identical sampled row IDs, payloads, estimates, or plans.

## 7. Provenance and observability

Every manifest and event stream binds to the protocol, producer commit, source
Forest10 digest, full population count, 30,000-row sample recipe and digest,
candidate universe, query IDs and SQL digests, source pins, run/repetition,
planner settings, and output namespace. Live adapters must record relation
`reltuples`, `relpages`, population count, effective target/sample evidence,
ordinary-statistics fingerprints, candidate kinds/targets/OIDs/order, payload
states, and EXPLAIN/ANALYZE counts.

No pass condition requires equal payloads, Plan Rows, or q-error. A missing or
unverified source/sample artifact is a readiness failure, not a reason to
substitute data.

## 8. Safety and lifecycle

The harness is offline-first. Live use requires explicit stock and patched
DSNs, pinned binary identities, the historical Advisor checkout, an audited
Forest10 data root, a sealed v3 snapshot and candidate universe, and an unused
invocation namespace. Physical destructive operations are restricted to
adapter-owned clone names. v2 manifests and historical evidence are never
overwritten.

## 9. Readiness decision

The v3 protocol and offline artifact preparation are eligible for validation
after the full source and sealed 30,000-row snapshot pass their gates. No live
correctness preflight or formal Analysis A/B timing is authorized by this task.
