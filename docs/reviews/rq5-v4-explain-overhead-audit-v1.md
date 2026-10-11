# RQ5 v4 EXPLAIN-path overhead audit

**Status:** `CONDITIONAL GO`  
**Classification:** offline diagnostic audit; no PostgreSQL connection was made.  
**Decision boundary:** the v4 lifecycle comparison remains usable for its declared
mechanism-cost question. The existing evidence does not identify a server-side
planner-overhead ratio, so no such attribution is made.

## Scope and bindings

This audit examines the completed v4 Analysis A EXPLAIN stages only. It does not
run a workload, acquire truth, change a manifest, or alter either PostgreSQL
source tree. The formal experiment binding is:

- v4 Analysis A manifest semantic digest:
  `813870b1bbdefdce9450f70a6dacb42abcd8b159a1d00c0c96fa8c015b604c77`;
- producer commit: `f57f920d9f2b64a669b5215226c1e9e0f155867a`;
- stock PostgreSQL source: `0d1c00c624fa7367d4a895f44381887757289682`;
- patched PostgreSQL source: `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`;
- historical Advisor source: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`;
- PostgreSQL version: 16.14.

The raw inputs are the six immutable streams under
`experiments/rq5-whatif-evaluation-cost-v4/formal-analysis-a/`. Their SHA256
digests at audit time are:

| Arm/repetition | Raw event-stream SHA256 |
|---|---|
| Physical 1 | `75efb67a1cfdaf762f0f6985e8c1f3b7d3299ca56540ad16b114fbeca87e92b6` |
| Physical 2 | `b90e5f7a2082ca536a702dc5de7035e982700c09ac046a33ef273f9718539fc4` |
| Physical 3 | `9b2b2f94ce1cbdad0a480dd8fd18d1d25cc386633000133f4c3e86f55548d720` |
| Catalogless 1 | `b6896e85aab6b6f4a7d29214087de2b37aae6bcbffccaaeed7eab1dd1c286ec1` |
| Catalogless 2 | `d9411cfb79365c3958d002c1592bfceb057ae47684549a7d78cbc2aa359c5b72` |
| Catalogless 3 | `7a0c57d416c9385979ca7283c3bd2876d43e4edb8e8db36d313ed604afbed6ea` |

## Raw-event reconciliation

All six streams pass the repository's v4 `validate_events()` contract. Each has
20 configurations and 102 events for Physical or 67 events for Catalogless.
Every configuration has one successful EXPLAIN stage with operation count 128.
The persisted per-configuration adapter audits independently contain 20 files
per run; all 120 files report `attempted=128`, `successful=128`, `failed=0`, and
the exact manifest query ID sequence. Thus each arm has 2,560 successful calls
per repetition and 7,680 across three repetitions.

The EXPLAIN totals reconstructed by summing raw `elapsed_ns` values are:

| Arm | Repetition | EXPLAIN stage (ms) | Calls | Mean per call (ms) |
|---|---:|---:|---:|---:|
| Physical | 1 | 629.380703 | 2,560 | 0.245852 |
| Physical | 2 | 617.053794 | 2,560 | 0.241037 |
| Physical | 3 | 589.901402 | 2,560 | 0.230430 |
| Catalogless | 1 | 947.700175 | 2,560 | 0.370195 |
| Catalogless | 2 | 932.252882 | 2,560 | 0.364161 |
| Catalogless | 3 | 1,042.751130 | 2,560 | 0.407325 |

These values reconcile exactly with `stage-summary-v1.json`: the medians are
617.054 ms for Physical and 947.700 ms for Catalogless. The descriptive median
per-call ratio is approximately 0.370195 / 0.241037 = **1.54x**. This is a
client-side StageTimer result, not a server-reported planning-time result.

## Exact measured call paths

### Physical

`run_inplace_physical_arm()` invokes the shared `StageTimer` around
`StockPhysicalCostAdapter.explain()` (the implementation remains inherited by
the in-place adapter). In
`src/extstats_advisor_research/rq5_whatif_cost_postgres.py:851-883`, the adapter:

1. validates that the supplied IDs exactly equal the manifest sequence;
2. looks up already-loaded SQL in the in-memory `self._queries` dictionary;
3. removes a terminal semicolon;
4. executes one `EXPLAIN (FORMAT JSON) <sql>` on the reused PostgreSQL
   connection and calls `fetchone()`;
5. decodes JSON if the driver returned text, reads the root `Plan Rows`, and
   appends the result to an in-memory list; and
6. writes one per-configuration JSON audit file before returning.

The adapter is not opening a connection, creating a cursor, activating a
configuration, or running another SQL statement per query. The connection was
prepared outside this stage. The final adapter audit write is inside the timed
`explain()` operation; the StageTimer event write itself occurs after the end
timestamp (`src/extstats_advisor_research/rq5_whatif_cost.py:596-681`).

### Catalogless

The corresponding stage invokes
`CataloglessWhatIfCostAdapter.explain()` at
`src/extstats_advisor_research/rq5_whatif_cost_postgres.py:1344-1372`. It also
validates the manifest sequence and writes one audit file inside the measured
operation, but each query calls the historical Advisor
`PostgresPlannerSession.estimate_query()`.

In `/tmp/rq5-advisor-0865-bXtAsZ-git/src/extstats_advisor/dbms/postgres/planner.py:282-339`,
`estimate_query()` performs additional in-process work before the one database
round trip:

- scans the snapshot workload list for the query ID;
- scans the candidate-universe query-profile list and checks that the query is
  supported;
- checks for parameters using a regular expression (and only invokes the
  parser on a match);
- resolves the target relation from the snapshot;
- builds a psycopg composable SQL object; and
- sends `EXPLAIN (VERBOSE, FORMAT JSON) <query>`.

After `fetchone()`, it recursively walks the JSON tree to locate exactly one
node matching the target relation and extracts that node's `Plan Rows`, then
constructs a `PlannerEstimate`. This is more Python-side validation and JSON
traversal than the Physical root-node extractor. There is still exactly one
EXPLAIN SQL execution per query, and activation is not repeated per query:
activation is performed once per configuration by the adapter, while
deactivation is performed once afterward.

The two paths therefore measure the same broad operation category—client
submission, server planning, result transfer/decoding, and research-side
result handling—but not an identical implementation path.

## Verified differences and controls

### EXPLAIN options

The option sets are not identical. Physical uses `EXPLAIN (FORMAT JSON)`;
Advisor uses `EXPLAIN (VERBOSE, FORMAT JSON)`. `VERBOSE` can change server-side
plan construction/serialization and response size. The existing raw events do
not isolate that effect. It is an uncontrolled implementation difference in
the measured stage, not evidence that the patched estimator is slower.

The SQL text also has a minor construction difference: Physical strips a
terminal semicolon before interpolation, whereas Advisor wraps the snapshot
SQL in a composable object and appends a trailing space. The manifest and all
120 persisted audits establish identical query IDs and SQL digests; they do not
establish byte-identical wire strings after these adapter transformations.

### Connection and configuration lifecycle

Both formal arms reuse a prepared connection/session across their 20
configurations. Catalogless registration and sandbox preparation occur before
the configuration loop. `PostgresPlannerSession.activate()` sends activation
SQL once per configuration and checks the active OID array; the per-query path
does not call activation or issue GUC-setting SQL. Physical likewise does not
create a database connection or run DDL inside its EXPLAIN stage.

The v4 state audit records 2,560 calls per repetition and no failures. It also
records the intended population identity and 16.14 server identity. Physical
ordinary-statistics fingerprints evolve after each native ANALYZE; Catalogless
uses the fixed sandbox ordinary-statistics fingerprint
`9c880f2bb54fff888b0afd1c9f30b4ea7f17ecaf56a74d87c50b97b85e6d0f55`. This is a
declared statistical-state difference that can affect both plan content and
planning work. It is not a per-query adapter defect, but the persisted formal
EXPLAIN events do not contain server CPU or a complete per-query GUC inventory.

The state audit reports `reltuples=581012` for the Catalogless sandbox and the
Physical preflight records the same population scale. The arms use different
relation/OID identities and different native realization paths by design. No
persisted artifact establishes identical relation-cache state, planner GUC
values, or server planning work for each query.

### Hypothetical lookup and native payload loading

The pinned patched source uses backend-local hash tables, not a linear scan for
each active payload lookup:

- `src/backend/statistics/hypothetical.c:68-107` uses `hash_search()` keyed by
  `(statoid, kind)` and an active-OID hash;
- `:427-467` filters the catalog list, scans the repository once to determine
  whether the relation is targeted, then iterates only the active order and
  uses hash lookups; and
- `:470-489` retrieves an active payload with an active hash lookup followed by
  a repository hash lookup.

For the singleton configurations, the active-order loop is therefore over the
one active candidate, although the relation-target check scans the registered
repository once during planner relation-statistics setup. The repository's 57
registered candidates are not all decoded for every query.

For a present MCV, patched
`src/backend/statistics/mcv.c:563-595` returns the registered bytea and calls
`statext_mcv_deserialize()`; dependencies use the analogous path in
`src/backend/statistics/dependencies.c:622-654`. The stock MCV loader also
deserializes a native catalog payload after a syscache lookup. Thus the source
confirms a repeated native payload deserialization on planning paths, but does
not show that the patched path decodes all 57 payloads or that it is inherently
slower than stock. The source alone cannot quantify the difference between
syscache access plus deserialization and hash lookup plus deserialization.

The virtual definitions are consumed through the existing planner statistics
machinery in patched `src/backend/optimizer/util/plancat.c:1498-1535`; this is
native-estimator reuse, not a replacement estimator.

## What the existing evidence establishes

### Verified from source

- The Catalogless path has extra in-memory query/profile lookup, relation-node
  traversal, and estimate-object construction per query.
- The option sets differ (`VERBOSE` is Catalogless-only).
- Both paths issue one EXPLAIN SQL execution per query on a reused session;
  neither path performs per-query activation or ANALYZE.
- Patched hypothetical payload lookup is hash-based, with active-order filtering
  and repeated deserialization for a present payload.
- The Physical and patched native loaders both ultimately deserialize native
  MCV/dependency data during planning.

### Measured from immutable formal events

- Physical EXPLAIN stage: 589.901–629.381 ms per repetition, median
  617.054 ms.
- Catalogless EXPLAIN stage: 932.253–1,042.751 ms, median 947.700 ms.
- Each repetition: 2,560 calls; all calls succeeded.
- The lifecycle result remains the v4 in-place comparison. At N=20, the
  published medians are Physical 6,751.457 ms and Catalogless 7,979.233 ms;
  the EXPLAIN-stage difference is one component of that lifecycle result.

### Not identifiable without a new measurement

The raw events do not partition an EXPLAIN stage into:

- client wrapper and Python lookup/decoding time;
- network/client round-trip time;
- server planner CPU;
- server JSON serialization; or
- patched hypothetical lookup/deserialization time.

No percentage of the 0.129 ms median per-call difference is assigned to these
components. In particular, source-level extra Python work and `VERBOSE` make
the difference plausible, but neither proves that it explains all of it. The
existing formal run also does not establish whether ordinary-statistics drift
or other planner-state differences change planning CPU independently of the
hypothetical overlay.

## Impact on Analysis A

There is no demonstrated implementation defect that invalidates the declared
v4 lifecycle question. The arms completed the same frozen query sequence with
the required configuration counts, and the v4 protocol did not require
identical Plan Rows, payloads, or server-side estimator work. The formal result
can therefore be used as an operational comparison of the two implemented
lifecycle paths.

The result must not be rewritten as “the patched estimator is 1.54x slower” or
as a pure hypothetical-lookup cost. The EXPLAIN component is a mixed
client/server measurement with a known `VERBOSE` difference and known
Advisor-side processing. It is also not an end-to-end Advisor-search measure.

## Smallest useful future diagnostic (not executed)

Before making a planner-overhead claim, run a separate, fresh diagnostic on the
same frozen query set and one fixed active configuration:

1. direct stock `EXPLAIN (FORMAT JSON)`;
2. direct patched `EXPLAIN (FORMAT JSON)` under the same active hypothetical
   configuration;
3. the current Advisor `estimate_query()` path using its existing
   `VERBOSE, FORMAT JSON` contract; and
4. where available, a server-side planning-time measure collected consistently
   on both binaries.

The diagnostic should reuse connections, hold activation fixed, record the
exact SQL option set, and separate wrapper, round-trip, result-decoding, and
server-reported components. It must remain outside v4 formal artifacts and
must not be treated as a timing campaign. No such diagnostic was executed for
this audit.

## Recommendation

**CONDITIONAL GO.** The v4 Analysis A lifecycle result remains scientifically
valid for its stated mechanism-level cost comparison, and no concrete source
defect was found in the hypothetical lookup path. However, an additional live
diagnostic is required before claiming that the 1.54x EXPLAIN-stage difference
is server-side planner overhead, patched-estimator overhead, or any other
isolated component. The current audit supports reporting the observed mixed
EXPLAIN-stage cost and its implementation confounders, not a causal percentage
attribution.

