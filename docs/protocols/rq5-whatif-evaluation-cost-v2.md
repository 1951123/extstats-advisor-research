# RQ5 What-if Evaluation Cost v2

**Status:** research-only harness and protocol revision; formal timing not authorized
**Protocol identity:** `rq5-whatif-evaluation-cost-v2`
**Historical predecessor:** [`rq5-whatif-evaluation-cost-v1.md`](rq5-whatif-evaluation-cost-v1.md) (preserved unchanged)

## 1. Scientific question and scope

With one fixed logical sample, candidate universe, workload, and frozen configuration sequence, how do the cumulative costs of repeated physical evaluation on stock PostgreSQL 16 and catalogless what-if evaluation on patched PostgreSQL 16 compare?

The primary estimand is mechanism-level wall-clock cost and its amortization as the number of completed configurations grows. This is not a measurement of Advisor optimization time, estimator accuracy, production deployment cost, or a universal speedup claim.

The v1 sequence mixed evaluation count and configuration size. v2 separates them:

- **Evaluation count `N`** is the number of configurations evaluated.
- **Configuration size `K`** is the number of active extended-statistics candidates in one configuration.

No runtime observation may influence a manifest.

## 2. Frozen source and dataset binding

The initial benchmark uses Forest10 if the producer-bound preflight passes.

| Binding | Required identity |
|---|---|
| PostgreSQL | 16.14 |
| Stock PostgreSQL | `0d1c00c624fa7367d4a895f44381887757289682` |
| Patched PostgreSQL | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` |
| Historical Advisor stratum | `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` |
| RQ4 candidate source | `experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json` (`736ba74fcd41e34bc9e468dbf1e1ea149ee8caac7ca940be5236af6319d226a9`) |
| Candidate definitions | `experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json` (`71483b2e67c7d57cd2696ed0925ea6d817c6548eec5df9b594cc71c8cfe44283`) |
| Candidate count | 57 eligible, fixed before timing |
| Sample | 10,000 rows, seed 42, statistics target 100 |
| Workload | `arecel_forest10_test_v1`, 10,000 canonical queries |

Forest10 is suitable for the v2 design because the tracked design artifact contains all 57 eligible physical definitions and the existing workload source contains SQL and provenance. The historical-v1 Advisor identity remains an explicit source stratum; it is not promoted to system-freeze-v2.

The manifest must also bind the exact workload-source file SHA256 and the raw UTF-8 SHA256 of every selected SQL statement. A missing workload source or definition mismatch is a preflight failure, not an invitation to substitute another source.

## 3. Analysis A: primary amortization

Analysis A holds `K=1` while increasing `N`.

- Candidate universe: all 57 eligible candidates are materialized/prepared in the declared setup policy.
- Evaluated configurations: the first 20 distinct singleton configurations in frozen canonical candidate order.
- Each configuration has exactly one active candidate.
- Checkpoints: `N = 1, 5, 10, 20`.
- The exact 20-entry sequence is written and hashed before timing.
- The same singleton sequence and query sequence are used by both arms.

This analysis can identify an observed cumulative-cost crossover only at a measured checkpoint. It does not study how changing `K` changes a configuration's quality or planner behavior.

## 4. Analysis B: configuration-size sensitivity

Analysis B preserves the useful v1 prefix configurations, but does not contribute to Analysis A's amortization curve:

- `K=0`: empty baseline;
- `K=1`: first singleton in canonical candidate order;
- `K=5`, `K=10`, `K=20`, `K=50`: deterministic candidate-prefix configurations.

Analysis B describes configuration-size sensitivity. It must be reported separately from the fixed-`K=1` cumulative cost analysis and must not be used to claim an amortization point.

## 5. Workload subset

The initial harness uses 128 queries selected without runtime observations by:

`source_index_i = floor(i * (10000 - 1) / (128 - 1))`, for `i=0,...,127`,

over the canonical 10,000-query order. This includes the endpoints and produces a deterministic, evenly spaced subset. The manifest records original indices, query IDs, SQL SHA256 digests, weights, and order.

The subset makes the first benchmark a mechanism-cost study, not a full-workload timing result. It must not be described as representative of all possible workloads without additional evidence. Expanding to all 10,000 queries is a separately authorized scope decision.

## 6. Arm definitions

### 6.1 Repeated physical evaluation

For each manifest configuration on stock PostgreSQL:

1. establish a clean baseline;
2. create the configuration's statistics objects in declared order;
3. set the frozen kind, columns, and target;
4. execute one native `ANALYZE`;
5. validate relation identity, definitions, OIDs, order, ordinary-statistics fingerprint, and payload state;
6. execute the 128 fixed `EXPLAIN (FORMAT JSON)` calls;
7. drop statistics and destroy the disposable clone or otherwise verify a complete reset.

The physical reset must not assume that `DROP STATISTICS` restores ordinary statistics. Clone creation/destruction is measured separately and included in the complete lifecycle.

### 6.2 Catalogless what-if evaluation

For each independent run on patched PostgreSQL:

1. load the same logical rows and schema;
2. materialize the complete fixed 57-candidate native payload universe once;
3. register it in a backend-local repository;
4. activate each manifest configuration in the exact declared order;
5. execute the same 128 `EXPLAIN` calls;
6. deactivate configurations and reset the repository at run end.

No per-configuration `ANALYZE` is permitted in this arm. The existing `PostgresPlannerSession` and native materialization interfaces are reused through explicit research-owned adapters; no timing API is added to Advisor or PostgreSQL.

## 7. Timing and cost accounting

Both arms use the same client-side monotonic `perf_counter_ns()` wrapper. Timing instrumentation and event serialization are outside the timed operation. Every event records protocol, producer, run, arm, repetition, configuration, stage, monotonic start/end, elapsed nanoseconds, operation count, status, and error details.

Run-level preparation stages are:

- physical: baseline initialization;
- catalogless: fixed-sample preparation, native payload materialization, and repository registration.

Physical configuration stages are clone creation, CREATE/ALTER STATISTICS, ANALYZE, EXPLAIN, DROP STATISTICS, and clone destruction. Catalogless configuration stages are activation, EXPLAIN, and deactivation. Both arms have an explicit final cleanup stage.

For `N` completed configurations:

\[
T_{physical}(N)=P_{physical}+\sum_{i=1}^{N}C_{physical,i},
\]

\[
T_{catalogless}(N)=P_{catalogless}+\sum_{i=1}^{N}C_{catalogless,i}.
\]

`P` includes mandatory run-level preparation. `C_i` includes the configuration-specific stages and cleanup. EXPLAIN counts, ANALYZE counts, configuration counts, and planner-call counts are reported separately. The primary result uses complete lifecycle totals. Warm-start totals subtract only explicitly identified `P` stages and are secondary.

An amortization point exists only when the catalogless complete-lifecycle total is lower at an observed common `N`. No unmeasured crossover is extrapolated.

## 8. Repetitions and failure rules

The initial target is three independent complete runs per arm and analysis. A run resets database/session state, uses explicit pinned binaries and settings, and does not reuse a previous run's payload repository or database state.

Three runs support descriptive spread, not significance testing or a population probability claim. A failed or partial configuration is preserved in the append-only event stream and failure record. Complete summaries reject missing, duplicate, failed, or out-of-order events. Cleanup errors are recorded independently and do not overwrite the primary failure.

## 9. Controls and limitations

The preflight records stock and patched binary identities, session GUCs, logical sample identity, candidate definitions, actual physical OIDs, hypothetical virtual OIDs and activation order, ordinary-statistics fingerprints, payload states, query IDs and SQL digests, EXPLAIN counts, and cleanup status.

Stock `ANALYZE` payload bytes need not equal the fixed catalogless payload bytes. This is an acknowledged binary/realization boundary, not a reason to manufacture equality. Physical CREATE order is recorded but is not assumed to prove planner precedence. If effective physical order cannot be established, the result remains an operational cost observation and cannot claim strict ordered-planner equivalence.

The experiment excludes Advisor candidate derivation, singleton profiling, Greedy search, truth acquisition, production deployment/refresh, multi-table workloads, join workloads, and Catalog Mask. It does not claim end-to-end Advisor speedup or estimator superiority.

## 10. Harness contract

The research-owned implementation consists of:

- a producer-bound manifest builder for Analysis A or B;
- explicit stock and catalogless adapter protocols;
- a common append-only JSONL event writer and monotonic stage timer;
- fail-closed event and cumulative-cost validation;
- deterministic arm-pair comparison at common checkpoints;
- a non-executing integration-preflight plan generator.

The default commands only build/validate manifests or plans. No DSN fallback exists, and ordinary offline tests must not open a database connection.

The future tiny integration preflight is separately authorized and limited to two candidates and three fixed queries. It must verify real catalog identity, payload state, activation order, EXPLAIN JSON shape, and cleanup. It is not part of this implementation task.

## 11. Decision

**Protocol v2: GO for offline harness implementation; NO-GO for live correctness preflight and formal timing execution in this task.**

The v2 separation of fixed `K` and increasing `N` makes the amortization question interpretable. The remaining correctness questions are adapter and isolation questions: loading identical logical rows into both binaries, proving physical reset, recording physical order, and preserving complete failure accounting. Offline tests may establish accounting correctness but cannot establish live PostgreSQL readiness.
