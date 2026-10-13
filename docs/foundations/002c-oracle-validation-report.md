# EXP-002C Oracle Validation Report

Status: **BLOCKED**
Classification: `integration-readiness-only`
Invocation: `exp-002c-readiness-audit-001`
Smoke observations: none

## What was actually executed

The research repository was at `565e8011638ee6c9d7325f12786cfaa615c056f2` before
the audit. The managed stock and patched lab instances were started from their
recorded identities, checked, and stopped. The following operations were allowed
and performed:

- managed lab doctor;
- patched backend capability probe;
- PostgreSQL version checks;
- read-only catalog inventory of non-system relations and physical extended
  statistics;
- managed lab shutdown and stopped-state verification.

No `ANALYZE`, `COUNT`, `EXPLAIN`, DDL, native repository materialization, repository
registration, Advisor objective evaluation, or EXP-003 operation was performed.

## Identity and artifact results

| Gate | Evidence | Result |
|---|---|---|
| Stock source | `0d1c00c624fa7367d4a895f44381887757289682`, clean managed install, server 16.14 | pass |
| Patched source | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, clean managed install, server 16.14 | pass |
| Patched contract | `postgresql-pgextadv-16.14-v1` and reference commit `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` | pass |
| Snapshot | `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5` | pass offline |
| CandidateUniverse | `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`, 57 | pass offline |
| Native repository | `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`, 57/57 PRESENT | pass offline |
| GroundTruthSet | `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2`, 10,000 | pass offline |

## Database observations

Both managed databases exposed `public.forest10` with `reltuples=581012` and
`relpages=7792`. The patched database did not contain the required
`extstats_advisor_internal` sandbox or frozen sample relation. Neither database
contained physical extended-statistics objects. The stock database also had an
unrelated `public.dmv11`; no research operation touched it.

The sealed AdvisorSnapshot's 30,000-row sample was validated offline, but it was
not loaded into the live database during this task. Therefore the live sample
identity, ordinary-statistics fingerprint, repository registration, virtual OIDs,
configuration activation, and workload relation checks remain unverified.

## Smoke result

| Requested observation | Result |
|---|---|
| M0 empty objective | not run |
| M1 singleton objective | not run |
| M2 pair objective | not run |
| Planner calls | 0 observed; 30,000 is only the declared expected volume |
| Objective values | none |
| Timing evidence | none |
| State leakage | not assessable |
| Cleanup | both managed servers stopped; no smoke resources created |

The live smoke was correctly not attempted because `PostgresPlannerSession` would
fail its sandbox contract before planner evaluation. Preparing that sandbox would
require the prohibited database preparation work. This is a safety-preserving
blocker, not a failed objective evaluation.

## Gate status

- PostgreSQL environment identity: **GO**.
- Experimental sample identity: **GO offline / NO-GO live**.
- Frozen artifact compatibility: **GO**.
- Planner registration and activation: **NO-GO**.
- End-to-end objective evaluation: **NO-GO**.
- Session isolation: **NO-GO live**.
- EXP-003 readiness: **NO-GO**.

No objective, timing, or algorithmic conclusion can be drawn from this audit.
