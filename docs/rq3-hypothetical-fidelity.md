# RQ3 mechanism-fidelity harness

This harness tests the mechanism claim in `paper-experiment-v1`: whether
catalogless hypothetical native statistics reproduce the physical PostgreSQL
statistics behavior they are intended to represent. It is not a cardinality
estimator, candidate generator, search implementation, or utility benchmark.

## Causal comparison

The primary comparison keeps one patched PostgreSQL binary, one database, one
relation, one schema, one deterministic workload, one ordinary-statistics
state, one statistics target, one planner/session setting set, and one native
statistics design per configuration. The only intended factor within each
configuration is:

```text
physical pg_statistic_ext realization
    versus
backend-local catalogless hypothetical realization
```

The harness runs three independently rebuilt configurations over `(a, b)`:
MCV only, functional dependencies only, and MCV plus functional dependencies.
For each configuration, the physical arm creates and analyzes the selected
objects. The harness extracts their native payload bytes, records catalog OIDs
and definitions, then drops the physical objects without a second `ANALYZE`.
The hypothetical arm registers those exact extracted bytes through
`pg_hypothetical_extstats_register_definition`, activates the same candidate
order, and runs the identical workload. Registration, activation, payload
deserialization, planner lookup, and reset are PostgreSQL patched-backend
contracts; the research code only orchestrates them and captures evidence.
Each configuration records non-empty payload evidence, supported equality
clause forms, and any observed physical-versus-no-extstats estimate change;
the artifact does not claim planner object-consumption instrumentation.

The physical and hypothetical arms both run inside
`REPEATABLE READ READ ONLY` planner transactions. Ordinary statistics are
fingerprinted after the physical `ANALYZE`, after dropping the physical
objects, and during the hypothetical arm. The harness fails closed on a
fingerprint change, physical-state leakage, activation-order change,
payload-correspondence failure, or cleanup failure.

## Artifact contract

Each successful run writes one `rq3-fidelity-v1` JSON artifact. It binds:

- `paper-experiment-v1` and the RQ3 primary experiment ID;
- three configuration records (`mcv-only`, `fd-only`, and `mcv-plus-fd`), each
  with its own physical/hypothetical state and cleanup proof;
- research, advisor, patched PostgreSQL source, backend contract, and server
  identities;
- deterministic fixture/data/workload identities and digests;
- physical definitions, physical OIDs, catalog order, payload sizes, and
  SHA-256 payload digests;
- hypothetical virtual OIDs, active candidate order, and the fact that the
  payload source was the physical extraction;
- both session-setting records and ordinary-statistics fingerprints;
- complete paired EXPLAIN JSON and digests for every query;
- direct `Plan Rows`, absolute/relative deltas, exact-match flags, and an
  evidence-backed mismatch category;
- cleanup and no-leak verification, plus a semantic digest of the artifact.

The primary metric is direct exact root `Plan Rows` agreement. The artifact
reports query count, exact count/fraction, mismatch count, maximum absolute
delta, maximum relative delta, and the closed mismatch taxonomy. No near-exact
threshold is used without an empirical protocol change. Objective or q-error
comparisons, if later added, are derived diagnostics and not the primary
mechanism-fidelity metric.

## Commands

The small validation fixture is intentionally separate from official AreCEL
benchmark runs:

```bash
extstats-research validate hypothetical-fidelity run \
  --dsn "$PATCHED_DSN" \
  --output experiments/rq3/rq3-primary-mechanism-fidelity-v1.json \
  --formal

extstats-research validate hypothetical-fidelity validate \
  experiments/rq3/rq3-primary-mechanism-fidelity-v1.json

extstats-research validate hypothetical-fidelity inspect \
  experiments/rq3/rq3-primary-mechanism-fidelity-v1.json
```

The formal command requires a clean committed research tree, binds the producer
SHA and `system-freeze-v2` semantic digest, and verifies the pinned v2 Advisor
and patched-source revisions. It does not run Census13, Forest10, Power7, or
DMV11. The non-`--formal` mode remains useful for local readiness fixtures.
The stock-versus-patched physical sanity check is a separate secondary
contract and is recorded in
`experiments/rq3/rq3-secondary-build-sanity-v1.json`; it is not substituted
for the primary same-binary comparison.

## Limitations

The fixture validates the MCV and dependency registration paths and
multi-object activation order on independently rebuilt single-relation
fixtures. It does not establish fidelity for all
PostgreSQL expressions, statistics kinds, relation shapes, planner settings,
or workload classes. It also does not establish sample-to-full-data utility;
that remains RQ2b. A runner result is `artifact-created` with a
`fidelity_gate` of `pass` or `fail`; mismatches are preserved in the artifact
rather than hidden. A formal run records execution completion separately from
the exact `fidelity_gate`, which is `pass` only when all paired Plan Rows are
equal.
