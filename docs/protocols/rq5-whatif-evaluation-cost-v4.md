# RQ5 What-if Evaluation Cost v4

## Scope

This amendment corrects the Physical lifecycle boundary used by the v3
cost experiment.  It preserves the Forest10 population, the fixed 30,000-row
Catalogless sample, the 57-candidate universe, the 20 singleton
configurations, the 128-query sequence, source pins, target 100, and the
three-repetition design.  All v2/v3 protocols and evidence remain historical
and unchanged.

The v4 question is:

> For the frozen Forest10 inputs and configuration sequence, how does
> sequential in-place physical evaluation on stock PostgreSQL compare with
> catalogless evaluation using one fixed native payload repository?

This is a mechanism-level lifecycle-cost comparison.  It does not measure
Advisor search, cardinality-estimation quality, q-error, or equivalence of
native payload realizations.

## Scientific amendment

The v3 Physical arm used one disposable database clone per configuration.  The
clone-attribution analysis showed that clone creation and destruction were a
large measured component of that lifecycle.  v4 therefore uses one fresh,
experiment-owned full-population database per independent Physical repetition
and evaluates all configurations sequentially in that database:

```text
CREATE STATISTICS → ALTER STATISTICS SET STATISTICS 100 → ANALYZE
→ EXPLAIN (FORMAT JSON) → DROP STATISTICS → verify absence
```

No database clone is created between configurations.  Ordinary-column
statistics are not restored between configurations; native ANALYZE state
evolution is recorded as an observed property of the sequential workflow.
Provisioning the repetition database from the protected full-data template is
completed before the primary timing boundary and is excluded as common
full-population provisioning.  The database owner, template identity, and
provisioning exclusion are recorded in the binding and cleanup evidence.

The Catalogless arm is unchanged from v3: runtime sample acquisition and
snapshot construction are timed, all 57 native payloads are materialized once,
the patched sandbox is prepared once, and the 20 configurations are activated
and evaluated in order without per-configuration ANALYZE.

## Frozen dimensions

- Forest10 population: 581,012 rows, with the v3 full-source digest.
- Catalogless sample: 30,000 rows, `sha256-row-rank-v1`, seed 42, with the
  v3 sample-index and snapshot identities.
- Candidate universe: 57 frozen pairwise MCV/dependencies candidates.
- Statistics target: 100.
- Analysis A: 20 frozen singleton configurations, checkpoints N=1, 5, 10, 20.
- Workload: the v3 frozen 128-query subset, in canonical order.
- PostgreSQL and historical Advisor source pins: unchanged from v3.

The v4 manifest is a new producer-bound artifact.  It is not a relabeling of
v3 raw events and v3 observations are not included in v4 summaries.

## Timing contract

The primary lifecycle begins after each arm's full population is available in
its experiment-owned environment.  For Physical, database provisioning is
outside that boundary; all in-place configuration work and final cleanup are
inside it.  For Catalogless, sample acquisition, Snapshot construction and
serialization/validation, fixed-sample preparation, native materialization,
sandbox preparation/registration, activation, EXPLAIN, deactivation, and
cleanup are inside it.

Physical stages are:

```text
baseline-initialization
  [create-alter-statistics, analyze, explain, drop-statistics,
   post-drop-verification] × 20
arm-cleanup
```

Catalogless stages retain the v3 names and boundaries.  The combined
`repository-registration-and-sandbox-preparation` stage remains combined when
the existing Advisor interface does not expose a smaller valid boundary.

For each checkpoint, the cumulative total includes run preparation and the
completed configuration stages.  Final cleanup is included only at N=20 under
the existing convention; earlier checkpoints do not receive an invented or
redistributed cleanup cost.  Prepared-state values exclude only explicitly
recorded run-level preparation stages.

## Validity and safety gates

The in-place adapter accepts only explicit database names in the
`rq5wc_v4_` namespace, verifies ownership and the pinned stock identity, and
refuses an unexpected baseline extended-statistics inventory.  It drops only
objects created by the current configuration and verifies that no
experiment-named statistics remain before the next configuration.  On a
failure it does not continue against a potentially contaminated database.

The v4 validator rejects mixed v3/v4 event streams, clone stages in a v4
Physical run, missing post-drop checks, duplicate or failed stages, source-pin
drift, and incomplete configuration/query coverage.  Payload bytes, Plan Rows,
and q-error need not match between arms.

## Interpretation boundary

The result estimates the observed cost of the declared in-place stock
workflow versus the declared catalogless workflow.  It does not establish a
universal speedup, full Advisor end-to-end cost, production refresh cost, or
estimator fidelity.  The v3 clone-based result and its clone-excluded derived
sensitivity remain separate historical analyses.

Formal execution is authorized only after the v4 offline validator and the
two-configuration live correctness preflight pass.  Analysis B remains outside
this amendment.
