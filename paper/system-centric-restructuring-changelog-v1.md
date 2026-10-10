# System-centric restructuring v1

This revision makes the PostgreSQL what-if mechanism and `extstats-advisor`
the organizing narrative of the paper.

## Emphasized

- The distinction between native payload materialization and repeated ordered
  configuration activation.
- Backend-local registration, virtual definitions, planner filtering, and
  native MCV/dependency consumption.
- The Advisor path from workload predicates to candidates, native sample
  payloads, singleton profiling, bounded Greedy ADD, and ordered
  Recommendation.
- The stock PostgreSQL deployment boundary and DBA ownership contract.
- The three principal contributions and the RQ1--RQ5 mapping to them.

## Compressed or clarified

- Repeated experiment-status narration in the introduction, discussion, and
  conclusion.
- Provenance details that do not explain a scientific or architectural
  invariant; the evidence ledger remains the traceability record.
- Physical-loop language was qualified to acknowledge shared-parent-and-clone
  experimental controls and to avoid unsupported runtime-speedup claims.

## Preserved

- All validated numerical results and tables.
- The Census13 Singleton counterexample and budget-censored incumbents.
- Same-generator RQ1b wording, RQ3 fixture scope, OID physical confounding,
  Native ANALYZE v1 failure/v2 post-hoc status, and RQ5 cost limitations.
- Frozen protocols, experiment artifacts, source revisions, and negative
  findings.
