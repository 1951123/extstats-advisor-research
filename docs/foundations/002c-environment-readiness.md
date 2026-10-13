# EXP-002C PostgreSQL Environment Readiness

Status: **PARTIAL READINESS / NO-GO for the live oracle smoke test**
Classification: `integration-readiness-only`; no smoke objective was measured.

## Scope

This audit checks the managed local PostgreSQL environments and the frozen
EXP-002B inputs. It does not run `ANALYZE`, `COUNT`, `EXPLAIN`, Greedy search, or
landscape enumeration. The only live database actions were the managed lab start,
the built-in capability probe, and read-only catalog inspection. Both lab instances
were stopped after inspection.

## Source and binary identity

The three repositories were fetched and checked before the audit. All were on
`main`, clean, and synchronized with `origin/main` at the time of inspection:

| Component | Identity | Result |
|---|---|---|
| Research | `565e8011638ee6c9d7325f12786cfaa615c056f2` | verified |
| Stock PostgreSQL source | `0d1c00c624fa7367d4a895f44381887757289682` | clean, verified |
| Patched PostgreSQL source | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` | clean, verified |
| Advisor | `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` | installed/frozen dependency |

The managed lab doctor found PostgreSQL 16.14 executables for both roles. The
instances use fixed local-only sockets and ports 55432 (stock) and 55433
(patched); these are not arbitrary localhost connections. The patched capability
probe returned:

```text
backend_contract: postgresql-pgextadv-16.14-v1
reference_source_commit: 6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6
server_version: 16.14
server_version_num: 160014
```

The stock and patched servers both reported PostgreSQL 16.14. The source checkout
and managed install identity are established; no server is left running after this
audit.

## Frozen artifact gate

The existing Advisor validators passed for the exact current binding:

| Artifact | Semantic identity | Result |
|---|---|---|
| AdvisorSnapshot | `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5` | validated |
| CandidateUniverse | `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134` | validated; 57 candidates |
| NativeStatsRepository | `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184` | validated; 57/57 present |
| GroundTruthSet | `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2` | validated; 10,000 truths |

The workload is `arecel_forest10_test_v1` with 10,000 positive-weight supported
queries. The eight prespecified structural-study candidates remain unchanged and
all have compatible PRESENT native payloads. The manifest remains
`experiments/foundations-interaction-landscape-v1/manifest-v2.json`; its status is
still blocked pending a live smoke observation.

## Database-plane observations

Read-only catalog inspection of the managed `extstats_lab` databases found:

- `public.forest10` in both roles, with `reltuples=581012` and `relpages=7792`.
- No physical extended-statistics objects in either database.
- No `extstats_advisor_internal` planner sandbox, frozen sample relation, or
  Advisor sandbox metadata in the patched database.
- The stock database also contained an unrelated `public.dmv11` relation; it was
  not used and was not modified.

The offline snapshot contains the 30,000-row typed sample and full-population
metadata, but that sample is not evidence that the corresponding live patched
planner sandbox exists. The catalogless `PostgresPlannerSession.open()` contract
calls `_verify_postgres_planner_sandbox_connection()` and requires the prepared
Advisor sandbox, its metadata, frozen relation, ordinary-statistics fingerprint,
and repository binding. Those objects were absent.

Creating them would require the sandbox preparation path, which performs writes
and a required `ANALYZE`; rebuilding/registering the native repository would also
cross the current task's explicit prohibition. The smoke therefore fails closed
before any workload EXPLAIN.

## Gate decision

| Gate | Decision | Basis |
|---|---|---|
| PostgreSQL environment identity | **GO** | managed binaries, source pins, versions, and patched capability contract verified |
| Experimental sample identity | **GO offline / NO-GO live** | sealed sample validated; live sample/sandbox absent |
| Frozen artifact compatibility | **GO** | all four Advisor artifacts and native compatibility validators pass |
| Planner registration and activation | **NO-GO** | no prepared patched sandbox; registration was not attempted |
| End-to-end objective evaluation | **NO-GO** | no live planner calls or utility evaluation occurred |
| Session isolation | **NO-GO live** | session contract is tested offline but not live-validated |
| EXP-003 readiness | **NO-GO** | bounded live smoke gate remains incomplete |

The next authorized setup must explicitly prepare or restore the pinned patched
sandbox, verify its 30,000-row sample and metadata, and then rerun the separate
smoke protocol. It must not use a different snapshot, repository, database, or
source revision.
