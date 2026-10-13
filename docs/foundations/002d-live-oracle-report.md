# EXP-002D Live Oracle Report

Status: **BLOCKED**
Classification: `integration-readiness-only`
Bootstrap: not executed
Smoke: not executed

## Operations actually performed

The managed lab doctor was run for both roles. Both source/build identities passed.
The managed stock and patched servers were started only long enough to run the
documented version/capability checks and read-only catalog inventory, then stopped
cleanly. No database write occurred during this task.

The patched capability probe confirmed:

```text
PostgreSQL 16.14 / server_version_num 160014
backend_contract = postgresql-pgextadv-16.14-v1
reference_source_commit = 6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6
```

## Bootstrap result

No bootstrap duration, ANALYZE count, metadata fingerprint, sample row count,
repository registration, or virtual OID observation exists. The standard Advisor
bootstrap was deliberately not called because:

- its database-catalog check expects `extstats_stock`, while the authorized managed
  database is `extstats_lab`; and
- its target relation check requires `public.forest10` to be absent, while the
  current database already contains `public.forest10` with full-population metadata.

The existing `public.dmv11` relation was also protected. No relation, schema,
statistics object, metadata table, or payload was created or changed.

## Smoke result

| Observation | Result |
|---|---|
| M0 empty objective | not run |
| M1 singleton objective | not run |
| M2 pair objective | not run |
| Planner calls | 0 |
| Expected primary calls | 30,000, contract-derived only |
| Objective values | none |
| Activation/reset diagnostics | none |
| Timing evidence | none |
| Cleanup | lab servers stopped; no bootstrap resources created |

The absence of observations is an intentional safety outcome, not an unsuccessful
objective evaluation.

## Frozen artifact status

Offline validation passed for Snapshot `ed5d82...a4f5`, CandidateUniverse
`292247...3134`, NativeStatsRepository `fef941...3184`, and GroundTruthSet
`bcea3b...00c2`. The 57-candidate repository remains unchanged and no candidate
payload was regenerated.

## Decision

The environment identity and frozen artifact gates pass. Sandbox bootstrap,
registration, M0/M1/M2 evaluation, and session isolation are **NO-GO** for the
current database binding. EXP-003 remains **NO-GO**.
