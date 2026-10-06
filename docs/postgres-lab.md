# Reproducible local PostgreSQL lab

The research harness owns a disposable, user-owned PostgreSQL lab at
`.runtime/postgres-lab`. It never installs system packages, uses `sudo` or
`systemd`, edits either PostgreSQL source checkout, or accepts an arbitrary
deletion path.

The two roles are deliberately different:

- `stock` is the simulated production/baseline/deployment target. It is built
  from `/home/wqts/projects/postgresql-src` at its recorded clean commit.
- `patched` is the design-time planner sandbox. It is built from
  `/home/wqts/projects/postgresql-src-pgextadv` at the pinned patched commit
  and is checked with the existing patched-backend capability probe.

Both roles use the same out-of-tree configure features, compiler policy, and
local-only runtime policy. The default ports are stock `55432` and patched
`55433`; a port collision is an error rather than a reason to select a random
port. The servers listen only on their fixed Unix socket directories, use
`trust` for local lab connections and reject host authentication, and use the
explicit `C.utf8`/`UTF8` initdb policy.

## Lifecycle

From the research repository root:

```text
extstats-research postgres-lab doctor --role all
extstats-research postgres-lab build --role all --jobs 4
extstats-research postgres-lab init --role all
extstats-research postgres-lab start --role all
extstats-research postgres-lab status --role all
extstats-research postgres-lab env --role all
```

The `env` command prints shell exports containing only local socket DSNs. A
typical lightweight smoke check is to connect with the role's installed
`psql`, create a disposable table, insert a few rows, and run `ANALYZE`.
Research experiments then use the patched socket DSN for design-time planner
evaluation and the stock socket DSN for simulated production/baseline work.

When finished, stop the servers before cleanup:

```text
extstats-research postgres-lab stop --role all
extstats-research postgres-lab destroy --role all
```

`destroy` accepts only the fixed `stock` or `patched` role below the exact
runtime root. It requires the lab marker, a valid `identity.json`, matching
source identity, and matching managed paths; it refuses symlink escapes,
unmarked directories, arbitrary paths, or a still-running server. To create a
fresh lab explicitly, use:

```text
extstats-research postgres-lab recreate --role all --jobs 4
```

## Build identity and paper provenance

Each built role records `identity.json` with the source repository and exact
commit, clean-source status, configure features, compiler and make versions,
PostgreSQL version, prefixes, port, locale/encoding, and initdb command. The
build uses a temporary clean `git archive` of the validated source commit.
This prevents ignored products from an earlier in-tree build in a source
checkout from contaminating PostgreSQL's VPATH makefiles while leaving the
source checkout untouched.

The `paper-experiment-v1` specification treats these lab identities as the
source of final PostgreSQL build provenance. It does not silently resolve the
paper freeze gate: final confirmatory manifests must still record the exact
research, advisor, patched PostgreSQL, and stock PostgreSQL build identities.

The runtime tree is ignored by Git. Do not copy build products, PGDATA, WAL,
sockets, logs, or local identities into version control.
