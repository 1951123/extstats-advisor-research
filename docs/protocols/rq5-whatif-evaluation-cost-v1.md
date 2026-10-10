# RQ5 What-if Evaluation Cost v1

**Status:** protocol design only; not preregistered for execution
**Protocol identity:** `rq5-whatif-evaluation-cost-v1`
**Decision point:** implementation may begin after the gates in Section 12 pass; no database experiment is authorized by this document.
**Design producer:** research repository `7b0fcea6174ec2e85173a9bb146ff3b07b67ef34`

## 1. Question and claim boundary

The experiment asks:

> With one fixed logical sample, candidate universe, workload, and frozen configuration sequence, how do the cumulative costs of repeated physical evaluation on stock PostgreSQL and catalogless what-if evaluation on the patched PostgreSQL compare, and at what number of completed configurations, if any, does the one-time what-if preparation cost amortize?

This is a mechanism-level cost comparison. It is not a comparison of Advisor search algorithms, a claim about q-error superiority, or a measurement of full production deployment cost. It does not establish that sample-side native payloads equal independently realized full-data payloads, nor that the patched binary is faster than stock PostgreSQL for every workload.

The primary result is a paired cost curve over the same frozen sequence of configurations. Quality observations, if retained for configuration auditing, are secondary and cannot be used to select the sequence or claim estimator equivalence.

## 2. Frozen source and evidence bindings

The eventual execution must publish a producer-bound preflight before any live timing. The source identities below are the default bindings inferred from the existing Forest10 artifacts; a future preflight must verify them rather than trusting this prose.

| Binding | Required identity |
|---|---|
| PostgreSQL version | 16.14 |
| Stock PostgreSQL | `1951123/postgresql-src`, `0d1c00c624fa7367d4a895f44381887757289682` |
| Patched PostgreSQL | `1951123/postgresql-pgextadv`, `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` |
| Forest10 historical Advisor stratum | `1951123/extstats-advisor`, `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` |
| Candidate universe source | `experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json`, semantic digest `736ba74fcd41e34bc9e468dbf1e1ea149ee8caac7ca940be5236af6319d226a9` |
| Design-evaluation source | `experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json`, semantic digest `71483b2e67c7d57cd2696ed0925ea6d817c6548eec5df9b594cc71c8cfe44283` |
| Existing physical evidence | `experiments/arecel-forest10/rq4-fixed-k/physical/*.json`; the existing Greedy child digest is `56270e34ba7bfc5ffe574e9624ac4dea78a433b9c45b27ed8b31d174b888bd06` |
| Workload | `arecel_forest10_test_v1`, 10,000 canonical queries |
| Existing sample contract | 10,000 rows, sample seed 42, statistics target 100 |

Forest10 is suitable for a first implementation because its frozen eligible universe has 57 candidates, the source artifacts expose physical definitions and workload/truth bindings, and its existing RQ4 physical path already records catalog and payload controls. Its RQ4 selection evidence remains a historical-v1 stratum. It must not be silently relabeled as system-freeze-v2 evidence. If the historical candidate definitions cannot be loaded into both arms without changing their physical meaning, the formal run is blocked and Census13/DMV11 may be considered only through a separately bound protocol revision.

## 3. Comparison arms

### Arm A: repeated physical evaluation

Use the pinned stock PostgreSQL build and the fixed logical sample relation. For each configuration:

1. Start from a clean baseline state with the same table, rows, schema, ordinary-statistics settings, and no experiment-owned extended statistics.
2. Create exactly the configuration's objects in the declared order and set their frozen statistics target.
3. Run the required native `ANALYZE` exactly once for that configuration.
4. Verify definitions, relation identity, kinds, keys, targets, object OIDs, ordinary-statistics fingerprint, and payload state.
5. Execute the same frozen workload as ordinary `EXPLAIN (FORMAT JSON)` calls.
6. Drop the configuration objects and record cleanup.

The preferred reset is a disposable database clone made from a canonical baseline before each configuration. Clone creation and destruction are recorded separately and are included in the complete-lifecycle total; they must not be silently omitted from an end-to-end claim. If the harness instead reuses one database, it must restore and verify the ordinary-statistics fingerprint before every configuration. A failed restoration makes the affected run non-comparable.

`DROP STATISTICS` is not assumed to restore ordinary per-column statistics. The baseline fingerprint and relation identity are therefore mandatory controls.

### Arm B: catalogless what-if evaluation

Use the pinned patched PostgreSQL build and the same logical sample relation and configuration sequence. Before the first timed configuration:

1. Materialize the frozen candidate universe's native payloads once using the existing native materialization path.
2. Record the sample, native repository, ordinary-statistics fingerprint, candidate states, payload lengths and digests, and materialization time.
3. Register the repository in one backend-local planner session.
4. For each configuration, activate only its ordered candidate IDs, execute the same workload `EXPLAIN` calls, and record activation/deactivation time.
5. Reset the backend-local repository when the run ends.

The arm must not run `ANALYZE` per configuration. The native payload repository is not free: cold preparation is part of the complete-lifecycle total. A separately labeled warm-start curve may begin after materialization and registration, but it cannot replace the primary cold curve.

The two arms are required to agree on logical rows, schema, candidate definitions, workload IDs, planner settings, and configuration membership. They are not required to produce identical payload bytes or Plan Rows: stock and patched binaries, native realization paths, and physical versus catalogless identity are known comparison boundaries.

## 4. Cost boundary and timers

All timers use a client-side monotonic clock (`time.perf_counter` or an equivalent monotonic clock), with the same timing wrapper and event schema in both arms. Each event records start/end monotonic timestamps, elapsed seconds, operation count, database/session identity, status, and error information. SQL server time is not mixed into client time.

| Category | Arm A | Arm B | Primary treatment |
|---|---|---|---|
| Environment provisioning | PostgreSQL startup, cluster creation, base load, database population | same class of setup for patched lab | excluded from primary; reported separately |
| Mandatory preparation | fixed sample loading, baseline ordinary statistics, connection/session initialization | fixed sample loading, one native payload materialization, registration, session initialization | included in complete-lifecycle total; common data loading also reported separately |
| Candidate preparation | none beyond per-configuration physical DDL | native payload construction and repository registration | included; not free |
| Configuration setup | `CREATE STATISTICS`, `ALTER STATISTICS`, verification | ordered activation and activation verification | included in per-configuration total |
| Native construction | one `ANALYZE` per physical configuration | zero per-configuration `ANALYZE` calls | included in the respective arm |
| Workload evaluation | fixed `EXPLAIN` sequence | same fixed `EXPLAIN` sequence | included and counted separately |
| Reset/cleanup | drop statistics and disposable clone cleanup | deactivation, repository reset, session cleanup | included; cleanup failures remain visible |

The primary complete-lifecycle cumulative totals for the first `N` completed configurations are:

\[
T_A(N)=P_A+\sum_{i=1}^{N}(D_i+A_i+X_i+R_i),
\]

\[
T_B(N)=P_B+\sum_{i=1}^{N}(V_i+X'_i+R'_i),
\]

where `P` is mandatory preparation after environment provisioning, `D` is physical DDL, `A` is native `ANALYZE`, `V` is hypothetical activation, `X`/`X'` are workload `EXPLAIN` spans, and `R`/`R'` are reset and cleanup spans. Physical clone/reset time belongs in `R_i` when it is necessary to establish the clean baseline. The same `N` means the same manifest entries, not the same number of internal SQL statements.

Evaluation-only totals subtract `P_A`/`P_B` and are reported as secondary warm-start views. They must never be presented without the complete-lifecycle totals. Marginal cost is the increment from the previous completed manifest entry, including that entry's declared setup and cleanup. The observed amortization point is the first measured `N` for which `T_B(N) < T_A(N)` in a complete run. No crossover is extrapolated beyond the measured sequence.

The protocol reports DDL, `ANALYZE`, `EXPLAIN`, activation, preparation, reset, cleanup, PostgreSQL planner-call count, and configuration count independently. Configuration evaluations are not treated as a uniform unit of planner work.

## 5. Fixed configuration sequence

The sequence is generated and hashed before timing, from the immutable Forest10 eligible candidate IDs and their physical definitions. Runtime, q-error, or a preliminary timing result cannot influence it.

The default sequence is:

1. `C0`: empty configuration (baseline).
2. `S1`, `S2`, `S3`: singleton configurations for the first three candidates in the frozen canonical candidate order.
3. `M5`, `M10`, `M20`, `M50`: prefix configurations containing the first 5, 10, 20, and 50 candidates in that same order.

Forest10 has 57 eligible candidates, so all proposed checkpoints exist in the inspected artifact. A future preflight must still verify the count, candidate IDs, kind, column ordinals, relation, target, deterministic object names, and exact order. The manifest contains the complete ordered list, each configuration's membership, and a semantic digest. A missing or changed definition blocks the run; the harness must not silently shorten or replace the sequence.

The sequence is a cost probe, not an RQ4 recommendation claim. The first three singleton choices are an immutable prefix rule, not a statement that they are the best singletons. If a later study needs a different sequence, it requires a new versioned protocol.

## 6. Workload and session controls

Both arms use `arecel_forest10_test_v1` in canonical query-ID order. The exact SQL digests and query count are recorded in the manifest. No `W_test`, new truth acquisition, Advisor search, or query filtering based on measured cost is allowed.

The run uses one declared concurrency level, no competing user workload, identical statement and planner GUCs wherever the two binaries support the same setting, explicit schema qualification, and a named database/session. A one-time fixed warm-up is performed before the timed region in both arms and recorded but excluded from the primary total. If the warm-up changes shared cache state, the same policy is used in every repetition and cold-start effects are reported rather than hidden.

The primary measurement uses a fresh connection/session for each complete arm run, then a fixed per-configuration query order. Any prepared-statement or plan-cache behavior is recorded; the protocol does not assume that a stock and patched backend have identical cache behavior. `EXPLAIN` is ordinary planning only; `EXPLAIN ANALYZE` is prohibited.

Three independent complete runs are the initial target. They support descriptive median, minimum, maximum, and run-to-run spread only. They do not support significance claims or a population estimate of all possible workloads. A failed, timed-out, or partial configuration remains in the raw event log with its status and does not become a zero-cost observation. A run with missing configuration events cannot contribute a complete cumulative curve, although its partial prefix is preserved and reported.

## 7. Required observations and controls

For every arm, run, and configuration record:

- protocol, producer, binary, dataset snapshot, sample, workload, and session identities;
- configuration digest, candidate membership, candidate definitions, and declared order;
- relation OID, statistics-object OIDs, names, keys, kinds, targets, and actual catalog order for physical objects;
- hypothetical virtual OIDs and observed activation order for the patched arm;
- payload presence/state, size, SHA256, and serialization for each applicable kind;
- ordinary-statistics fingerprint before and after the configuration;
- exact DDL/activation, `ANALYZE`, `EXPLAIN`, reset, and cleanup event timings;
- configuration-object evaluation count and PostgreSQL planner-call count;
- failure phase, exit/SQL error, stdout/stderr references where applicable, and cleanup outcome.

Physical creation order is not treated as proof of planner precedence. The harness records actual OIDs and relative order. The patched planner session must verify the active virtual OID sequence through its existing activation observation. If the physical order does not match the declared order, the timing observation may be retained as an operational physical observation, but it is not eligible for a claim that the two arms evaluated the same ordered planner configuration.

The common fixed sample is a logical contract, not a claim of identical native payload realization. The physical arm's stock `ANALYZE` may change ordinary and extended statistics; the hypothetical arm's fixed sample materialization is built once. These differences are reported as design boundaries, not silently normalized.

## 8. Correctness and optional quality audit

The primary estimand is elapsed cost. Before accepting a run, the harness must nevertheless prove that both arms evaluated the intended definitions and query sequence. It must validate query IDs and SQL digests, result count, EXPLAIN JSON shape, relation identity, and no cross-configuration state leakage.

If the frozen truth mapping is available without new acquisition, Plan Rows and q-error may be recorded as secondary audit fields using the existing `qerror-cardinality-floor-1-v1` contract. They must not be used to choose configurations, stop a run, or imply equal estimator quality. This protocol does not authorize a new quality experiment.

## 9. Feasibility audit against current implementation

| Capability | Evidence inspected | Assessment |
|---|---|---|
| Deterministic physical definitions | Research `rq5_static_deployment_cost._execute_ddl`, `rq4_physical._create_statistics`, `rq4_physical._candidate_definition` | Supported with small harness adaptation. Existing code creates and validates DDL, but not a complete per-configuration cost manifest. |
| Clean physical reset | `rq4_physical._make_clone_dsn`, `_create_clone`, `_drop_database`; RQ5 loaders | Supported with small adaptation. A per-configuration clone policy and timer boundary are not currently unified. |
| Same logical sample in both arms | Advisor snapshot contract and `native_stats.materialize_native_stats` | Supported with small adaptation if the snapshot can be loaded into a stock relation with identical rows/types. Native payload byte identity is not expected. |
| Candidate mapping and DDL | Forest10 RQ4 artifacts; `rq4_physical._candidate_definition`; Advisor recommendation rendering | Supported today for the inspected candidate kinds and definitions; a new immutable configuration manifest is still required. |
| One-time native payload construction | Advisor `dbms/postgres/native_stats.py:materialize_native_stats` | Supported today. It performs one fixed-sample ANALYZE in its materialization session and returns payloads plus ordinary-statistics metadata. |
| Repeated ordered hypothetical activation | Advisor `dbms/postgres/planner.py:PostgresPlannerSession.activate` and `estimate_query`; patched `statistics/hypothetical.c` | Supported today for backend-local activation. Existing code verifies the observed virtual OID order; timing wrappers are still needed. |
| Same workload replay | Advisor planner session query lookup and research physical `_explain_rows` | Supported with small adaptation. Existing paths can issue JSON EXPLAIN, but no cross-arm cost event schema exists. |
| Separate stage timers | Research RQ5 `time.perf_counter` stages and RQ4 physical `analyze_seconds`/`explain_wall_clock_seconds` | Supported with small adaptation. A common event format and cumulative-accounting validator are missing. |
| Ordinary-statistics and physical-order audit | `rq4_physical._ordinary_fingerprint`, `_payload_record`, catalog OID queries | Supported with a small-to-moderate adaptation. Per-configuration baseline restoration and order equivalence must be added to the future harness. |
| Exact stock/patched planner equivalence | Stock catalog versus patched backend-local repository | Cannot establish as identity. The experiment can compare cost for intended definitions, not prove identical planner behavior or payload realization. |
| Historical Catalog Mask as primary arm | `pg-extstats-selection/src/extstats/measure_mask.py` | Not appropriate for the primary comparison: it updates/restores catalog payloads after one ANALYZE, focuses on singleton measurement, and does not provide arbitrary hypothetical OID-order control. |

The patched source confirms the mechanism boundary: `hypothetical.c` maintains backend-local payload and active-order state; `plancat.c` filters physical statistics and constructs planner metadata for virtual definitions; the Advisor planner session checks that activation order is preserved before issuing JSON EXPLAIN. This is sufficient for a what-if timing arm, not for a claim that the two PostgreSQL builds have identical execution internals.

## 10. Fairness and validity threats

1. **Unequal setup accounting.** A warm repository or excluded physical clone cost can create an artificial advantage. Primary totals include mandatory preparation and per-configuration reset; excluded environment provisioning is reported for both arms.
2. **Different native realizations.** Stock `ANALYZE` and patched fixed-sample materialization are not byte-equivalent by default. The result is a mechanism-cost comparison, not an estimator-fidelity result.
3. **Ordinary-statistics drift.** Physical `ANALYZE` can change ordinary statistics. Fingerprints are captured per configuration; a reused database without restoration is ineligible for a clean comparison.
4. **Order and OID differences.** Physical OID allocation and planner traversal are not inferred from CREATE order. Mismatches qualify the configuration-comparability claim.
5. **Binary differences.** Stock and patched PostgreSQL revisions are fixed and reported. A timing difference includes the cost of the deployed what-if substrate; it is not an upstream PostgreSQL microbenchmark.
6. **Cache and warm-up effects.** Repetitions use one fixed warm-up policy and no concurrency. Cold and warm spans are separately recorded.
7. **State leakage.** Every configuration has explicit membership, payload, ordinary-statistics, and cleanup checks. Cleanup failure preserves the run as partial.
8. **Workload scope.** One established Forest10 workload probes this mechanism; it does not generalize to joins, multi-table statistics, or every PostgreSQL workload.
9. **Full Advisor cost overreach.** Candidate derivation, singleton profiling, Greedy search, truth acquisition, and DBA deployment are not included unless explicitly placed in a secondary lifecycle report. The primary result cannot be called an end-to-end Advisor speedup.
10. **Three-run variability.** Three runs are descriptive only. A crossover must be observed in the raw cumulative traces in each reported run or explicitly qualified as run-dependent.

## 11. Historical Catalog Mask context

The historical `pg-extstats-selection` Protocol-M (`src/extstats/measure_mask.py`) creates and analyzes a set of native statistics once, saves payloads, masks nonselected payload columns in `pg_statistic_ext_data`, measures a singleton, then restores payloads and analyzes again during cleanup. Its sub-batching timings are useful historical context for catalog update/restore overhead, but they are not directly comparable to this proposed two-arm result: it targets a different singleton/capacity question, changes catalog payload state, does not expose arbitrary catalogless ordered activation, and uses a different paper and source contract.

Protocol-M is therefore excluded from the primary arm pair. A future, separately identified shared-realization study could use it as a third operational baseline if catalog mutation and restoration are measured under the same boundary.

## 12. Minimal follow-up implementation and gates

The next task should implement only a benchmark-specific harness, not a generic experiment platform:

1. Build an offline manifest generator that pins the Forest10 source artifacts, candidate definitions, exact eight-entry sequence, workload digest, and all binary identities.
2. Add a common stage-event schema and an offline validator for cumulative totals, planner-call counts, failures, and cleanup.
3. Add stock physical configuration reset using isolated named database clones; include clone/reset in complete totals and verify ordinary-statistics fingerprints.
4. Wrap `materialize_native_stats`, `PostgresPlannerSession.activate`, and `estimate_query` with the same event schema for the patched arm.
5. Add mocked tests for membership, order, payload/state identity, timing accounting, and failed cleanup. Do not use live PostgreSQL in this stage.
6. Run a separately authorized, tiny integration fixture before any Forest10 timing campaign.

Formal measurement is **NO-GO at this design checkpoint** until these gates are demonstrated:

- exact sequence manifest exists and is producer-bound before timing;
- both arms load the same logical sample rows and schema;
- stock per-configuration baseline restoration is verified;
- physical OID/order and hypothetical activation order are recorded;
- payload/ordinary-statistics states are audited without requiring cross-binary byte equality;
- complete-lifecycle and evaluation-only totals are both produced;
- no partial run is silently summarized as complete.

Implementation is feasible with bounded harness adaptation; the current repository is not yet ready to execute this benchmark because it lacks the unified timer/accounting layer and a demonstrated fair physical reset boundary. No runtime data or amortization conclusion exists under this protocol.

## 13. Planned output contract

The future run must publish, append-only:

- `preflight-v1.json`: producer, protocol, source bindings, sequence and workload digests;
- `run-<id>/manifest.json`: arm, repetition, session, binary, and environment identities;
- `run-<id>/events.jsonl`: raw stage events with monotonic spans and statuses;
- `run-<id>/config-<ordinal>/controls.json`: definitions, OIDs/order, fingerprints, and payload states;
- `run-<id>/summary.json`: cumulative and marginal cost decomposition, counts, and eligibility;
- `analysis-v1.json`: deterministic recomputation from raw events, including each measured crossover or its absence.

The summary must report failed and partial configurations separately. It must not turn an unmeasured stage into zero, infer planner-call equality from configuration count, or merge stock and patched q-error into a single quality estimate.

## 14. Decision

**Protocol design outcome: CONDITIONALLY FEASIBLE; GO for a bounded implementation task, NO-GO for execution.**

The existing code establishes the core capabilities: physical DDL/ANALYZE, fixed-sample native payload materialization, backend-local ordered activation, JSON EXPLAIN, payload inspection, and ordinary-statistics fingerprints. The unresolved issues are measurement-contract issues rather than a demonstrated impossibility: a fixed-sample stock loader, per-configuration physical reset, unified event accounting, and explicit order comparability still require implementation and isolated validation.

The strongest defensible eventual claim will be: under the pinned binaries, fixed logical sample, frozen configuration prefix, and declared lifecycle boundary, one mechanism accumulated less or more measured wall-clock cost than the other over the observed checkpoints. It will not be a universal Advisor speedup claim, a physical/statistical equivalence claim, or a claim about full end-to-end design cost.
