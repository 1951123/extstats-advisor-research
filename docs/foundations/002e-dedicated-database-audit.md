# EXP-002E Dedicated Database Audit

Status: **PARTIAL / NO-GO for the live black-box oracle smoke test**  
Classification: `integration-readiness-only`

## Scope and identities

This task prepared the dedicated database required by the frozen Advisor
planner-sandbox contract. It did not run EXP-003, regenerate native payloads, or
modify `extstats_lab`. The research repository was at
`2c3be98c18a94c7a34c8a3472d3d3573dcbc627f` before the live operation. The pinned
source identities were:

| Component | Identity |
|---|---|
| Advisor implementation used for the smoke | `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3` |
| Historical Advisor binding in the frozen manifest | `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` |
| Patched PostgreSQL | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` |
| PostgreSQL | 16.14 (`160014`) |
| Patched contract | `postgresql-pgextadv-16.14-v1` |

The exact frozen inputs were validated before writes:

| Artifact | Semantic identity | Result |
|---|---|---|
| AdvisorSnapshot | `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5` | valid |
| CandidateUniverse | `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134` | valid; 57 candidates |
| NativeStatsRepository | `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184` | valid; 57/57 PRESENT |
| GroundTruthSet | `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2` | valid |

The workload was `arecel_forest10_test_v1` with 10,000 supported positive-weight
queries. The frozen smoke memberships were fixed before objective inspection:

```text
M0 = ()
M1 = (cand_0deae0d5de82a5a1fdc34111)
M2 = (cand_0deae0d5de82a5a1fdc34111, cand_76679921292e8c769dbbd7bc)
```

## Database safety gate

The managed patched lab used its verified local-only endpoint (port 55433) and
reported PostgreSQL 16.14 and the pinned patched contract. The authenticated
role was the database owner with the required database-creation privilege. The
database inventory was inspected through the `postgres` database before any
write.

`extstats_stock` already existed, was owned by the lab role, was non-template,
and was connectable. It contained only the normal `public` schema and no user
relations or physical extended-statistics objects. It was therefore accepted as
the intended empty disposable database; no database creation, drop, rename, or
overwrite was performed. `extstats_lab` was not modified. The dedicated
database is retained as an explicitly owned experimental resource, but is empty
again after the failed smoke cleanup.

The managed environment doctor and capability probe were run before writes. No
credentials or full DSN were recorded in the evidence.

## Bootstrap operation and effects

The existing Advisor `prepare_postgres_planner_sandbox()` API was used directly;
no improvised table creation was used. Its transaction created the Advisor
internal metadata and frozen-sample structures and the target relation
`extstats_stock.public.forest10`, copied the sealed 30,000-row typed sample,
applied the prescribed replay metadata and statistics target 100, and ran the
single API-prescribed ordinary-statistics `ANALYZE`.

The subsequent read-only verification reported:

| Observation | Result |
|---|---|
| Target relation | `extstats_stock.public.forest10` |
| Target relation OID | `17633` |
| Frozen sample relation | `extstats_advisor_internal.frozen_sample_ed5d82ffd6999989d86097d4` |
| Sample rows | 30,000 |
| Frozen population metadata | 581,012 |
| Statistics target | 100 |
| Ordinary-statistics fingerprint | `9c880f2bb54fff888b0afd1c9f30b4ea7f17ecaf56a74d87c50b97b85e6d0f55` |
| Physical extended statistics | 0 |
| Native repository candidates | 57/57 PRESENT |
| Autovacuum disabled | true |
| Bootstrap elapsed | 867,877,177 ns |
| Verification elapsed | 382,154,109 ns |

The machine-readable bootstrap record is
`experiments/foundations-interaction-landscape-v1/exp-002e-bootstrap-v1.json`.

## Resulting gate

Bootstrap and artifact registration prerequisites passed. The live smoke did
not pass: M0 and M1 completed, but the M2 activation-order gate failed before
any M2 planner query. The failure and cleanup are preserved in the companion
report and machine-readable smoke record. Consequently this task does not
establish a valid three-configuration oracle, and EXP-003 remains NO-GO.

