# EXP-002E Live Oracle Report

Status: **FAIL / NO-GO**  
Classification: `smoke-test`

## Operations actually executed

The verified managed patched lab was started on its local-only port 55433. The
pre-write safety gate passed, and the pre-existing owned-empty database
`extstats_stock` was selected without being dropped or recreated. The standard
Advisor sandbox preparation and verification APIs then ran successfully. One
`PostgresPlannerSession` registered the frozen repository. No source checkout or
PostgreSQL source was changed. The running Advisor implementation was
`e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3`; the historical Advisor source
binding recorded by the frozen manifest remains
`0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.

The complete binding is recorded in
`experiments/foundations-interaction-landscape-v1/exp-002e-smoke-v1.json`.

## Bootstrap and registration

| Stage | Result |
|---|---:|
| Sandbox bootstrap | success; 867,877,177 ns |
| Sandbox verification | success; 382,154,109 ns |
| Sample rows | 30,000 |
| Population metadata | 581,012 |
| Target OID | 17633 |
| Ordinary-statistics fingerprint | `9c880f2bb54fff888b0afd1c9f30b4ea7f17ecaf56a74d87c50b97b85e6d0f55` |
| Physical extended statistics | 0 |
| Registered native candidates | 57 |
| Session open/registration | success; 389,426,908 ns |

## Primary observations

| Configuration | Objective J(M) | Planner calls | Evaluation elapsed |
|---|---:|---:|---:|
| M0 empty | 7.130279777941661 | 10,000/10,000 | 3,679,368,909 ns |
| M1 singleton `cand_0deae0d5de82a5a1fdc34111` | 6.910739517685949 | 10,000/10,000 | 3,635,108,375 ns |
| M2 pair | not produced | 0 | not produced |

M0 and M1 are preserved as partial smoke observations, not evidence of
algorithm effectiveness. The accumulated primary planner-call count was
20,000; the required three-configuration count was 30,000.

At M2 the frozen ordered candidate IDs mapped to virtual OIDs
`(2152206366, 3096182549)`. The activation call returned successfully, but the
subsequent active-OID observation was
`(3096182549, 2152206366)`. The exact preserved failure was:

```text
RuntimeError: M2-pair activation mismatch: (3096182549, 2152206366) != (2152206366, 3096182549)
```

No M2 planner query, reset diagnostic, repeated evaluation, or additional
configuration was executed. No objective was inferred for M2.

## Cleanup

The planner session closed successfully. The ownership-checked
`destroy_postgres_planner_sandbox()` call succeeded in 14,788,468 ns. A
post-cleanup catalog check found no `public.forest10`, no frozen sample
relation, no Advisor internal schema, and no physical extended statistics in
`extstats_stock`. The dedicated database was retained but left empty. The
source database `extstats_lab` was not modified. The managed patched server was
stopped after the invocation.

Because the failure occurred before the planned reset diagnostic, session
isolation is not live-validated. The smoke gate therefore fails closed.

## Source-level interpretation

The pinned patched implementation builds `HypoOrder` by iterating the OID list
and appending each OID (`hypothetical_extstats_activate()` in
`src/backend/statistics/hypothetical.c`). The public active-list function also
iterates `HypoOrder` in increasing index order. The pinned Advisor
`PostgresPlannerSession.activate()` independently compares the returned active
array with the ordered OID array and is designed to reject a mismatch.

The live record nevertheless observed a reversal in the outer activation audit
after M2. This is a concrete integration-contract failure, not an acceptable
scientific difference. Its exact runtime cause was not patched or guessed in
this task. A future research-only correction must first reproduce the mismatch
with a fresh invocation and determine whether it is a session/driver state
issue, a duplicate activation observation, or a backend ordering defect. No
formal landscape execution is authorized until that is resolved and revalidated.

