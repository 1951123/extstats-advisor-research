# Foundations Frozen Sandbox Bootstrap Protocol v1

Status: **defined; blocked for the current `extstats_lab` database**.

## Purpose

Prepare the exact Advisor planner sandbox required by the Foundations black-box
oracle, then run only the M0/M1/M2 smoke defined below. This protocol does not run
EXP-003, candidate search, native payload regeneration, or estimator experiments.

## Frozen binding

Before any write, validate these identities with Advisor's existing loaders and
validators:

```text
snapshot       ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5
universe       292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134
repository     fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184
ground truth   bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2
workload       arecel_forest10_test_v1 (10,000 positive-weight queries)
advisor        0865c5a6afb8bc176bd7d3b10b13b3da83f1f641
patched PG     6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6 (16.14)
```

The database must be an explicitly registered disposable resource. Its database
name must equal the snapshot relation catalog (`extstats_stock`) unless a new,
versioned artifact binding changes that contract. The existing managed
`extstats_lab` database is not eligible for bootstrap because its database name
and target relation contents do not satisfy this requirement.

## Bootstrap stages

Use `prepare_postgres_planner_sandbox()` only after verifying that the target
relation and Advisor internal objects are absent and that no unrelated objects will
be touched. The expected effects are:

1. Begin an ownership-checked transaction and take the sandbox advisory lock.
2. Create only the source schema if absent, the Advisor internal schema, metadata
   table, target relation, and frozen sample relation.
3. Copy exactly 30,000 typed rows from the sealed Snapshot to target and frozen
   relations.
4. Set autovacuum-disabled relation options and the prescribed patched replay GUCs.
5. Set statistics target 100 and execute exactly one ordinary `ANALYZE` on the
   target relation.
6. Compare ordinary-statistics fingerprint and population metadata with the frozen
   NativeStatsRepository and Snapshot.
7. Insert one metadata row and commit.
8. Run `verify_postgres_planner_sandbox()` before registration.

If any check fails, rollback and preserve the error. Do not drop or rename an
unrelated relation to make the preparation pass.

## Registration and smoke

Load the frozen NativeStatsRepository and use one `PostgresPlannerSession`. The
session registers definitions backend-locally; it must not run native materialization
or per-configuration `ANALYZE`.

Freeze these configurations before objective evaluation:

```text
M0 = ()
M1 = (cand_0deae0d5de82a5a1fdc34111)
M2 = (cand_0deae0d5de82a5a1fdc34111,
      cand_76679921292e8c769dbbd7bc)
```

Evaluate exactly the 10,000 positive-weight queries for each primary configuration
through `BlackBoxMembershipOracle`, `WeightedWorkloadUtility`, and the frozen
`QErrorLoss`. Expected primary planner calls are 30,000. After M2, an empty
activation may be used to verify reset without evaluating another workload.

Record bootstrap, registration, activation, planner, utility, cleanup, identities,
fingerprints, virtual OIDs, and failures in a fresh invocation directory. Do not
publish partial objective results as a successful smoke.

## Cleanup

After the smoke, deactivate the repository, close the backend session, verify no
active virtual OIDs remain, and call the ownership-checked
`destroy_postgres_planner_sandbox()` only for the sandbox recorded by its metadata.
Verify the source database and unrelated relations are unchanged. Stop the managed
lab instances after the invocation.

## Current decision

This protocol cannot be executed in the current `extstats_lab`: the database name
does not match the sealed catalog and `public.forest10` is already occupied. A new
authorized disposable database/binding is required.
