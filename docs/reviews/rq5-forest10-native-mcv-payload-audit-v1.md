# RQ5 Forest10 Native MCV Payload Discrepancy Audit v1

**Status:** `CONDITIONAL GO`

**Scope:** Offline audit of the selected Forest10 singleton in
`forest10-preflight-004`. No PostgreSQL instance was started, and no new
`ANALYZE`, `EXPLAIN`, `COUNT`, workload, truth, or timing operation was run.

## 1. Executive conclusion

The 2,966-byte physical payload and 160-byte catalogless payload are both
native PostgreSQL MCV serializations produced through the same
`pg_mcv_list_send` contract. Their lengths differ because the two ANALYZE
paths passed different `totalrows` values to PostgreSQL's unchanged native MCV
builder, not because the catalogless payload was truncated or wrapped.

The key source-level chain is:

1. The physical relation contains the 10,000-row sample. With statistics
   target 100, PostgreSQL requests `300 * 100 = 30,000` rows for an extended
   statistic; because the relation is smaller than that request, stock
   ANALYZE can use all 10,000 relation rows. The exact physical `totalrows`
   value was not persisted by the preflight, but the source behavior and
   sample binding support a value near 10,000.
2. Patched replay reads the same 10,000 rows verbatim but assigns
   `pg_extstats.frozen_totalrows = 581012`. The persisted sandbox audit
   verifies both `sample_row_count = 10000` and `reltuples = 581012`.
3. `statext_mcv_build` uses `samplerows` and `totalrows` in
   `get_mincount_for_mcv_list`. With `n=10000` and `N=581012`, the retention
   threshold is approximately `24.5095`; with `N` near `n`, it is near zero.
   Thus patched replay retains only sufficiently frequent groups, while stock
   ANALYZE may retain up to the target of 100 groups.
4. The same builder computes retained-item frequencies from the sampled rows;
   `totalrows` changes the keep/drop threshold, not the sampled frequency
   fractions of an item that is retained.

This is a real statistical-state difference and should be disclosed and
audited before formal RQ5 timing. It is not a serialization or payload
transfer defect. It does not invalidate the mechanism-level cost question,
provided the formal benchmark records this boundary and does not claim
identical estimator states or q-error. The recommendation is therefore
**CONDITIONAL GO**, consistent with the preceding statistical-state audit.

## 2. Identities and immutable evidence

The research repository was at `68d49cbac1a69cfac8520fccea6e194bb5a0dfac`
(`main == origin/main`) before this report. The preflight used stock
PostgreSQL `0d1c00c624fa7367d4a895f44381887757289682`, patched PostgreSQL
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, historical Advisor
`0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`, and PostgreSQL 16.14.

| Artifact | SHA256 or semantic identity |
|---|---|
| `docs/protocols/rq5-whatif-evaluation-cost-v2.md` | `56175f66c6e05066de91ef7219984c7f5889a6484173c4d4a6766ce8aeaf5192` |
| Prior state audit | `544be3304deec3c1120b467c13d4e2188571f42db359b6c28ac3c41a7d51d8e8` |
| Preflight summary | `24a8886be8ab9c5f4f66fa6f8295102add2c018484746d765a4c294cf5b58220` |
| Preflight result | `be453f3e79ec2a1183aa8b5a46e214553f5b0f5726a9e8939d0c9ca85c08c5a6` |
| Limited manifest | `aabe4fda9d8dcae80b2abdc2eda5a159da34aa03c2a72a369d67ad463c603ebf` |
| Catalogless repository manifest | `6ac582050142f2d8647b92beb2fdd2a1c6a3286063e3a52f63728d6740243aaa` |

The selected candidate is
`cand_1537a6ae8ef67c379c8d0fa0`, relation
`rel_96c2e4f82f8ed10b05330552`, columns `aspect` and `hillshade_9am`,
ordinals `[2, 7]`, kind `postgresql.mcv`, and statistics target 100.

## 3. What the measured bytes represent

### Physical path

`StockPhysicalCostAdapter._capture_state` queries:

```sql
pg_catalog.pg_mcv_list_send(d.stxdmcv)
```

and converts the returned value to Python `bytes` before recording its length
and SHA256 (`src/extstats_advisor_research/rq5_whatif_cost_postgres.py:609--655`).
The persisted physical record is:

* state: `present`;
* size: 2,966 bytes;
* SHA256:
  `58c2f68167630df6079f9e859d0e1ef50bdd6cb34fa78117a556a73d15e2af09`.

The physical byte sequence itself was not retained, so it cannot now be
decoded offline.

### Catalogless path

Historical Advisor `native_stats.py:_extract_native_payload` uses the same
SQL expression `pg_catalog.pg_mcv_list_send(d.stxdmcv)` and records the
serialization contract `postgresql.pg_mcv_list_send-v1`.
`write_native_stats_repository` writes the returned `bytes` directly to
`payloads/<candidate>.bin` and validates its length and digest; it adds no
wrapper or alternate encoding (`native_stats/repository.py:62--88,
130--198`).

The retained catalogless record is:

* state: `present`;
* size: 160 bytes;
* SHA256:
  `049adae92c1bba44ba38999be733fd1817f071217be5879624038d24b17eca6b`.

The payload starts with the PostgreSQL MCV magic `0xe1a651c2`, type `1`, and
two dimensions. Its native size formula is exactly consumed: 160 bytes. The
two source trees' `mcv.c` files differ only in the patched hypothetical-load
path; `statext_mcv_build` and `statext_mcv_serialize` are otherwise the same.

**Conclusion:** the two lengths are directly comparable as byte counts under
the same native serialization contract, but they are not a direct measure of
the same information content. They represent different MCV lists.

## 4. Offline decode of the retained catalogless payload

The PostgreSQL 16.14 serialization layout is documented and implemented in
`src/backend/statistics/mcv.c` and
`src/include/statistics/extended_stats_internal.h`. The retained catalogless
payload decodes as:

* magic: `0xe1a651c2`;
* type: `1` (`STATS_MCV_TYPE_BASIC`);
* dimensions: `2`;
* type OIDs: `(701, 701)`, PostgreSQL `double precision`;
* deduplicated values: first dimension `[43.0, 45.0]`, second dimension
  `[222.0, 223.0]`;
* MCV item count: **3**;
* all three items have non-NULL values.

| Item | Values | Frequency | Base frequency |
|---:|---|---:|---:|
| 0 | `(45.0, 223.0)` | 0.0041 | 0.00019845 |
| 1 | `(43.0, 222.0)` | 0.0028 | 0.00007761 |
| 2 | `(45.0, 222.0)` | 0.0025 | 0.00020895 |

The item count and values are available only for the catalogless payload. The
physical artifact retains only size and digest, so no physical item count,
frequency, NULL combination, or value list can be claimed.

## 5. ANALYZE and MCV construction semantics

### Effective target and sampled rows

For an extended statistic target of 100, stock PostgreSQL's
`ComputeExtStatisticsRows` returns `300 * result` (`extended_stats.c:255--327`),
so the requested sample target is 30,000 rows for the singleton. The stock
sampling loop stores every row while `numrows < targrows`
(`analyze.c:1253--1296`). The fixed physical relation is the 10,000-row sample,
so the source logic supports all 10,000 rows participating in physical MCV
construction. The preflight did not retain PostgreSQL's “rows in sample” log
or relation page count, so this is source-supported rather than a directly
persisted physical diagnostic.

The patched replay path is stronger and directly specified:
`acquire_frozen_sample_rows` checks column count and type metadata, scans the
auxiliary frozen relation, copies each row, rejects rows beyond the requested
target, and returns the exact 10,000-row sample
(`postgresql-pgextadv/src/backend/commands/analyze.c:1435--1504`).
The persisted sandbox audit records `sample_row_count: 10000`.

### `totalrows` and MCV item selection

Both stock and patched PostgreSQL call the same extended-statistics pipeline:
`BuildRelationExtStatistics` passes `totalrows`, `numrows`, and the sampled
tuples to `statext_mcv_build` (`extended_stats.c:115--213`). The MCV builder:

1. sorts and groups the sampled rows;
2. caps the candidate list at the effective statistics target;
3. computes `mincount = get_mincount_for_mcv_list(numrows, totalrows)`;
4. drops groups whose sample count is below `mincount`; and
5. stores retained item frequency and base frequency as group/column counts
   divided by `numrows` (`mcv.c:183--343`).

For the patched replay values `numrows=10000` and `totalrows=581012`, the
source formula gives:

```text
mincount = 10000 * (581012 - 10000)
           / ((581012 - 10000) + 0.04 * 10000 * (581012 - 1))
         = 24.509539...
```

For a physical 10,000-row relation whose stock ANALYZE observes approximately
`totalrows=10000`, the numerator is zero and the threshold is zero. Therefore
the physical path can retain up to 100 grouped values, while replay retains
only groups with counts at least approximately 25. This directly explains
why a 2,966-byte list can coexist with a valid 160-byte three-item list from
the same logical 10,000-row sample.

`totalrows` does not rescale the stored frequency fields in this implementation:
those fields are computed from `numrows`. It changes the inclusion threshold,
and therefore the set and size of retained MCV items. It also changes planner
interpretation later through the relation population state.

## 6. Explanation classification

| Hypothesis | Classification | Evidence |
|---|---|---|
| A. The relations contain different rows or types | **Types verified; identical physical row contents strongly supported but not directly re-hashed in the preflight** | Same sealed snapshot, 10,000-row binding, matching relation schema; patched replay explicitly uses the sealed Arrow sample and checks type metadata. Physical row digest was not retained. |
| B. ANALYZE processes different sampled rows | **Not supported as the primary cause; physical sample participation is not directly logged** | Stock target is 30,000 for a 10,000-row relation, while patched replay scans the exact frozen 10,000 rows. The payload difference is already explained by `totalrows`. |
| C. Different effective statistics target/sample-size calculation | **Target difference ruled out; totalrows difference verified** | Both candidate records say target 100. Both source trees compute 300×target sampling. Patched replay uses 10,000 rows but population 581,012. |
| D. `frozen_totalrows=581012` changes MCV construction | **Verified and causally sufficient at source level** | `analyze.c:1494--1503` sets totalrows; `mcv.c:153--167, 241--255` uses it for the retention threshold. |
| E. Different serialization representation | **Ruled out** | Both paths call `pg_mcv_list_send`; both use the native `postgresql.pg_mcv_list_send-v1` contract. Stock/patched `mcv.c` serialization is unchanged. |
| F. Catalogless payload truncation/transfer defect | **Ruled out for the retained catalogless payload** | Repository writer copies bytes directly; manifest size/SHA match the file; native header and exact serialization layout decode completely to 160 bytes and three items. |

## 7. Scientific implications

1. **Are both payloads valid native PostgreSQL statistics objects?**
   Yes, based on the physical catalog's successful native extraction and the
   catalogless payload's valid native header/layout. The physical byte sequence
   itself cannot be independently decoded from the retained evidence.
2. **Are they derived from the same frozen logical sample?** The catalogless
   path is directly tied to the sealed sample. The physical path is bound to
   the same snapshot/schema and a 10,000-row relation, but no physical row
   digest or sample-log artifact remains. Exact byte-for-byte input identity is
   therefore strongly supported, not directly proven by this preflight record.
3. **Why do bytes differ?** The patched replay uses population 581,012 in the
   native MCV retention calculation; stock physical ANALYZE on the 10,000-row
   relation uses its observed relation total. This changes retained MCV item
   selection and hence serialization length.
4. **Is this a Catalogless correctness defect?** No transfer or serialization
   defect was found. It is a deliberate replay semantic difference in the
   statistical realization.
5. **Does it affect mechanism-level lifecycle-cost comparability?** It does
   not prevent timing the two mechanisms, but it means the arms are not
   estimator-state equivalent. Timing claims must remain lifecycle-cost claims,
   not accuracy, q-error, or plan-equivalence claims.
6. **Is a change required before formal Analysis A?** Yes, a narrow
   research-harness correction/decision is required: persist sample counts,
   relation metadata, and MCV-state diagnostics, then explicitly choose and
   document whether both arms use sample-scale or population-replay
   `totalrows`. No frozen source or candidate change is justified.
7. **Paper disclosure:** State that stock and catalogless arms may materialize
   different native payloads because stock ANALYZE and frozen-sample replay use
   different relation-population semantics; the RQ5 result measures mechanism
   cost, not payload or estimator equivalence.

## 8. Live diagnostic decision

No bounded live diagnostic was executed. Static source inspection plus the
persisted repository manifest and payload were sufficient to distinguish the
serialization path, validate the retained catalogless bytes, and identify the
`totalrows` mechanism. Recreating the deleted lab databases would not recover
the missing physical payload bytes and would exceed the need of this audit.

## 9. Required follow-up

Before formal Analysis A, the research harness should, in a new versioned
preflight invocation:

1. record physical `numrows`/sample participation, `totalrows`, `relpages`,
   `reltuples`, and effective extended-statistics target;
2. record the same native MCV item-count and state information for the
   catalogless repository;
3. retain physical payload bytes or an explicit native item inventory if
   payload-content comparison is needed;
4. retain complete planner and replay GUC state; and
5. keep the declared cost experiment from interpreting payload-size or Plan
   Rows differences as timing causality.

These are instrumentation and protocol-boundary repairs. They do not alter
the historical preflight or frozen Analysis A/B manifests.

## 10. Final recommendation

**CONDITIONAL GO for formal RQ5 Analysis A.** The payload discrepancy is
understood as a valid native MCV-list difference induced by the patched
replay's population metadata, with no evidence of truncation or incorrect
serialization. Formal timing is meaningful only after the relation-scale and
MCV-state boundary is explicitly recorded and either aligned or reported as
an intentional stock-versus-replay difference. Do not interpret the existing
preflight as an accuracy comparison, and do not begin Analysis A or B under
the current uninstrumented state.
