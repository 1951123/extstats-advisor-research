# EXP-002D Sandbox Bootstrap Audit

Status: **BLOCKED before bootstrap**
Classification: `integration-readiness-only`; no sandbox or smoke observation was produced.

## Identity gates

The research repository started at `b97b7a8e5d78a2f28376af498bd2f004a6c93ae1`,
with a clean `main` branch synchronized to `origin/main`. The Advisor checkout is
clean at `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`; the patched PostgreSQL source
is clean at `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`; and the stock PostgreSQL
source is clean and detached at `0d1c00c624fa7367d4a895f44381887757289682`.

The managed lab doctor passed for both roles. The patched capability probe reported
PostgreSQL 16.14, server version number 160014, backend contract
`postgresql-pgextadv-16.14-v1`, and reference source commit
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. Both managed instances were stopped
before this audit ended.

The frozen Advisor inputs passed the existing validators:

- Snapshot: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- CandidateUniverse: `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`.
- NativeStatsRepository: `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.
- GroundTruthSet: `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2`.

## Preparation contract inspected

The authoritative implementation is Advisor
`extstats_advisor/dbms/postgres/sandbox.py`:

- `prepare_postgres_planner_sandbox()` requires one sealed relation/sample,
  compatible native repository metadata, the patched backend contract, and a
  database name equal to the snapshot relation catalog.
- It starts a transaction, obtains the planner-sandbox advisory lock, requires the
  snapshot target relation to be absent, ensures the source schema and
  `extstats_advisor_internal` schema, and creates the target relation
  `public.forest10` plus the internal frozen relation
  `extstats_advisor_internal.frozen_sample_<snapshot-prefix>`.
- It copies the 30,000-row Arrow sample into both relations, disables autovacuum on
  both, sets `default_statistics_target` to the repository target, sets patched
  replay GUCs including `pg_extstats.frozen_totalrows`, and executes one ordinary
  `ANALYZE` on the target relation.
- It records the snapshot, universe, repository, backend, population, sample-row,
  target, and ordinary-statistics fingerprint in
  `extstats_advisor_internal.sandbox_metadata`. On any preparation error the
  transaction is rolled back.
- `verify_postgres_planner_sandbox()` uses a read-only repeatable-read transaction
  and checks ownership, schema/columns, exact sample row counts, population
  `reltuples`, ordinary-statistics fingerprint, replay state, repository binding,
  and zero physical extended-statistics definitions.
- `destroy_postgres_planner_sandbox()` requires metadata and ownership proofs,
  drops only the recorded frozen/target/metadata tables, and drops schemas only if
  the metadata proves that this invocation created them and they contain no other
  objects.

Repository registration is a separate step in Advisor
`PostgresPlannerSession.open()` / `_register_repository()`. It registers the
already serialized payloads as backend-local virtual definitions and rejects any
physical `pg_statistic_ext` definitions on the target. It does not regenerate the
native payload repository.

## Existing database safety check

The fixed managed patched database is `extstats_lab`. Read-only catalog inspection
found:

- `public.forest10` already exists, with `reltuples=581012` and `relpages=7792`.
- `public.dmv11` also exists.
- No Advisor sandbox metadata or frozen sample relation exists.
- No physical extended-statistics definitions exist.

The sealed snapshot declares relation catalog `extstats_stock`, schema `public`,
and relation `forest10`. The preparation API therefore has two independent fail-
closed conflicts in the current lab:

1. It rejects a connection whose current database is not `extstats_stock`; the
   authorized managed database is `extstats_lab`.
2. Even if that catalog-name check were changed, it requires `public.forest10` to
   be absent, while the current lab already contains that relation.

Making the API run would require a different database identity or modifying,
renaming, or dropping existing `public.forest10`. The latter would violate the
explicit protection of existing relations, and the former is outside the
authorized database binding. No such operation was attempted.

## Audit decision

The standard preparation path is safe and transactional when used on its intended
empty, Advisor-owned database, but it is **not safe to invoke against the current
`extstats_lab` contents**. Bootstrap was therefore not executed. There is no
machine-readable bootstrap result, no repository registration result, and no
smoke result to publish.

The minimal follow-up is a separately authorized, explicitly bound disposable
database whose catalog name matches the sealed snapshot or a separately versioned
snapshot/catalog binding. It must be initialized without touching the existing
`public.forest10` or `public.dmv11`.
