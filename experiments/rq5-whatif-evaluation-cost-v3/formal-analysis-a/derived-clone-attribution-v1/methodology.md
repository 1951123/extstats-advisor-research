# RQ5 v3 Analysis A — Clone Attribution Methodology

Classification: `derived-accounting-sensitivity-not-measured-baseline`

This analysis is an offline transformation of the six immutable Analysis A
event streams. It performs no PostgreSQL I/O and does not represent a new
experiment.

## Inputs and validation

The analysis reads the frozen Analysis A manifest, the published
`checkpoint-summary-v2.json`, and all six `events.jsonl` files. Each stream is
validated with the existing `validate_events()` contract before any metric is
derived. Producer, protocol, binding, event order, operation counts, timestamps,
and successful status are checked. There must be three complete runs for each
arm, 20 configurations per run, and 128 EXPLAIN operations per configuration.

The raw-event SHA256 values and manifest identities are recorded in every JSON
deliverable. The reconstructed full-lifecycle values are reconciled point by
point against the published checkpoint summary.

## View A — observed full lifecycle

For each run and checkpoint `N`:

```
T_full(N) = preparation + sum(configuration stages 1..N)
            + final cleanup, only when N = 20
```

The intermediate checkpoints therefore remain observed through-checkpoint
costs. No final cleanup duration is added or redistributed at `N=1,5,10`.

## View B — clone-excluded sensitivity

For each Physical run, clone exclusion is computed before aggregation:

```
T_no_clone(N) = T_full(N)
                - clone-create(1..N)
                - clone-destroy(1..N)
```

Only the two recorded clone stages are removed. DDL, ANALYZE, EXPLAIN, DROP,
baseline preparation, and final cleanup remain included. This is labelled
`derived-accounting-sensitivity-not-measured-baseline`: it is not evidence that
a real no-clone physical workflow would have the same cache state, reset
semantics, ANALYZE state, or DDL cost.

No clone-excluded median is calculated by subtracting independent stage
medians. Run-level values are derived first, then summarized.

## View C — intrinsic stage decomposition

Stage totals remain at the instrumentation boundary. Physical stages are
baseline preparation, clone creation, CREATE/ALTER STATISTICS, ANALYZE, EXPLAIN,
DROP STATISTICS, clone destruction, and cleanup. Catalogless stages are sample
acquisition, snapshot construction, snapshot serialization/validation,
fixed-sample preparation, native payload materialization, combined repository
registration and sandbox preparation, activation, EXPLAIN, deactivation, and
cleanup.

The combined repository/sandbox stage is not split because the experiment did
not instrument those operations separately.

## Aggregation and interpretation

Medians, minima, and maxima are descriptive summaries over the three derived
run-level values. The N=20 full-lifecycle median ratio is a descriptive median
ratio; it is not a significance test. A checkpoint crossover in View B is called
a *derived accounting crossover*, never an observed no-clone execution result.

These outputs do not establish end-to-end Advisor optimization cost, estimator
equivalence, or a universal speedup over physical design alternatives.
