# Foundations Dedicated Sandbox Protocol v1

Status: **executed with a preserved M2 activation failure**  
Classification: `smoke-test`; not EXP-003 evidence

## Purpose

Use a dedicated, disposable patched PostgreSQL database whose catalog name
matches the sealed snapshot (`extstats_stock`), prepare the standard Advisor
planner sandbox, register the frozen native repository, and evaluate exactly
M0, M1, and M2. This protocol does not enumerate subsets or implement a search
algorithm.

## Frozen binding

Before any write, validate Snapshot `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`,
CandidateUniverse `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`,
NativeStatsRepository `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`,
and GroundTruthSet `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2`.
Require workload `arecel_forest10_test_v1`, 57/57 PRESENT payloads, the pinned
Advisor revision `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`, and patched
PostgreSQL source `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` on PostgreSQL 16.14.

The database must be an explicitly owned, empty disposable resource. The
source database `extstats_lab` and all unrelated databases are protected.

## Bootstrap

1. Inspect database inventory and ownership through the verified managed lab.
2. Run `prepare_postgres_planner_sandbox()` against `extstats_stock`.
3. Let the API transaction create only its recorded Advisor metadata, target,
   and frozen-sample relations.
4. Permit its single ordinary-statistics `ANALYZE` and validate sample rows,
   schema/types, population metadata, ordinary fingerprint, no physical
   extended statistics, and repository compatibility.
5. Open one `PostgresPlannerSession` and register the already-frozen repository.

## Bounded smoke

Freeze the following memberships before reading objectives:

```text
M0 = ()
M1 = (cand_0deae0d5de82a5a1fdc34111)
M2 = (cand_0deae0d5de82a5a1fdc34111, cand_76679921292e8c769dbbd7bc)
```

Use the existing `BlackBoxMembershipOracle`, `WeightedWorkloadUtility`,
`ArtifactGroundTruthProvider`, and `QErrorLoss`. Each primary membership must
run the same 10,000 positive-weight workload queries. The expected primary
volume is 30,000 planner calls. Record objective, activation, elapsed time,
membership, identities, and failures. Stop on an activation or identity
mismatch; do not silently change order or retry.

The attempted execution completed M0 and M1 (20,000 planner calls) and stopped
at the M2 activation-order check before planner calls. Thus no successful smoke
result may be reported from this invocation. A reset/replay diagnostic was not
run because the mandatory primary sequence had already failed.

## Cleanup and evidence

Close the planner session and invoke the ownership-checked sandbox destruction
API. Verify that the target relation, frozen sample, Advisor internal schema,
and physical statistics are absent afterward. Keep the dedicated database but
leave it empty. Store sanitized records under the Foundations experiment
directory; never store credentials, full DSNs, database dumps, or payload data.

