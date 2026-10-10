# RQ5 Tiny Integration Environment v1

This document prepares, but does not execute, the isolated databases required
by the tiny correctness preflight.  It is not a timing protocol and its fixture
is not formal Forest10 evidence.

## Required identities

Use two dedicated PostgreSQL 16.14 laboratory instances:

| Instance | Port | Expected source commit | Template database |
|---|---:|---|---|
| Stock | `55432` | `0d1c00c624fa7367d4a895f44381887757289682` | `rq5_tiny_stock_template` |
| Patched | `55433` | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` | `rq5_tiny_patched_template` |

The Advisor implementation used by this fixture is
`0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.  At runtime, record the executable
identity and source identity separately; a source SHA in a JSON file is an
expectation, not proof that a binary was built from it.

Use explicit TCP DSNs, for example:

```text
host=127.0.0.1 port=55432 dbname=rq5_tiny_stock_template user=<fixture-owner>
host=127.0.0.1 port=55432 dbname=postgres user=<fixture-admin>
host=127.0.0.1 port=55433 dbname=rq5_tiny_patched_template user=<fixture-owner>
```

Do not omit `host`, `port`, `dbname`, or `user`.  Do not use the default Unix
socket or a default database.  The owner role must own each template database
and relation.  The admin role needs only the permissions required by the
validated clone mechanism (normally `CREATEDB` and ownership of the disposable
databases); superuser access is not assumed.

The following is a preparation template, with absolute paths and role names
deliberately left for the laboratory operator to fill in.  It is not executed
by this task:

```bash
# Use the pinned stock and patched pg_ctl/initdb binaries, not PATH defaults.
STOCK_PGDATA=/absolute/lab/rq5-tiny-stock
PATCHED_PGDATA=/absolute/lab/rq5-tiny-patched
/path/to/stock/bin/initdb -D "$STOCK_PGDATA" --username=<fixture-admin>
/path/to/patched/bin/initdb -D "$PATCHED_PGDATA" --username=<fixture-admin>
/path/to/stock/bin/pg_ctl -D "$STOCK_PGDATA" -o '-p 55432 -h 127.0.0.1' start
/path/to/patched/bin/pg_ctl -D "$PATCHED_PGDATA" -o '-p 55433 -h 127.0.0.1' start
/path/to/stock/bin/createdb -h 127.0.0.1 -p 55432 -U <fixture-admin> \
  -O <fixture-owner> rq5_tiny_stock_template
/path/to/patched/bin/createdb -h 127.0.0.1 -p 55433 -U <fixture-admin> \
  -O <fixture-owner> rq5_tiny_patched_template
/path/to/stock/bin/psql -h 127.0.0.1 -p 55432 \
  -U <fixture-owner> -d rq5_tiny_stock_template -f fixture-rows-stock.sql
/path/to/patched/bin/psql -h 127.0.0.1 -p 55433 \
  -U <fixture-owner> -d rq5_tiny_patched_template -f fixture-rows-patched.sql
```

Before using this template, reserve ports `55432` and `55433`, verify both
`pg_ctl` binaries with their full paths and source identity records, and ensure
the data directories are dedicated to this fixture.  The operator must also
verify the role and database ownership with the explicit DSNs.  These commands
are setup instructions only; no cluster or database was created for this
record.

## Prepared setup sequence

The committed files `fixture-rows-stock.sql` and `fixture-rows-patched.sql`
contain identical schema, twelve deterministic rows, and one initial ordinary
`ANALYZE`.  Run each file only against its explicitly created, empty template
database after independently checking the DSN and database owner.  This task
does not run those files.

The relation is `public.rq5_tiny_cost_fixture` with columns:

```text
id integer NOT NULL
region text NOT NULL
tier text NOT NULL
segment text NOT NULL
```

The rows intentionally repeat correlated `(region, tier, segment)` values.  The
fixture generator and sealed snapshot are the source of truth for the exact
row order and values.  The workload contains five simple single-table
selection queries; no joins, OR predicates, expressions, ndistinct, or
parameters are used.

## Isolation and cleanup

The adapter must receive the two explicit template DSNs and a separate stock
admin DSN.  It refuses a source database whose name starts with `rq5wc_` and
creates only names matching `rq5wc_<run-hash>_c<ordinal>`.  Never point a source
DSN at an existing production database and never drop a database outside the
registered experiment-owned prefix.  The source templates are never dropped.

Before a future preflight, verify:

1. the selected ports are unused or owned by the dedicated lab instance;
2. each DSN resolves to the expected database and role;
3. PostgreSQL reports version 16.14;
4. the binary/source identity record matches the expected commit and is clean;
5. the fixture relation has exactly twelve rows and no experiment-owned
   statistics objects;
6. the output namespace and run identity are unused.

Afterward, stop only the dedicated lab instances and remove only adapter-owned
`rq5wc_` clones and run-scoped output.  Cleanup failures must remain visible in
the preflight artifact.

## Corrected fixture binding

The original `tiny-fixture-v1` is preserved unchanged.  Live preflight revealed
that the historical patched planner sandbox requires the sealed relation
catalog to equal the patched `current_database()`.  The append-only corrected
fixture
`experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4`
binds the snapshot catalog to `rq5_tiny_patched_template` and records
PostgreSQL's default text collation as `pg_catalog."default"`; it must be used
for the next correctness invocation. Its distinct digests are intentional; the
original v1 artifact and first catalog-bound correction remain historical
evidence of the earlier fixture contracts.

## Invocation boundary

The future correctness-only command is the existing explicit-opt-in entrypoint
shown below.  It is documented here but intentionally not executed as part of
fixture preparation:

```bash
rq5-whatif-cost integration-preflight \
  --enable-live-preflight \
  --manifest experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/manifest-v1.json \
  --snapshot experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/snapshot \
  --candidate-universe experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/candidate-universe.json \
  --workload experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/workload.json \
  --advisor-root /path/to/extstats-advisor-at-0865 \
  --stock-dsn 'host=127.0.0.1 port=55432 dbname=rq5_tiny_stock_template user=<fixture-owner>' \
  --stock-admin-dsn 'host=127.0.0.1 port=55432 dbname=postgres user=<fixture-admin>' \
  --patched-dsn 'host=127.0.0.1 port=55433 dbname=rq5_tiny_patched_template user=<fixture-owner>' \
  --stock-identity experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/identities/stock-identity.json \
  --patched-identity experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1-catalog-corrected-v4/identities/patched-identity.json \
  --output-dir /absolute/path/to/new-output \
  --output /absolute/path/to/new-output/preflight.json \
  --run-id rq5-tiny-preflight-001
```

The live command is a future, separately authorized action.  It must not be
run by offline tests or during artifact generation.
