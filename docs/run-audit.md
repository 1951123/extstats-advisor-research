# Canonical run audit

`extstats-research audit` is a research-owned diagnostic layer for one
completed run. It reads the frozen `AdvisorSnapshot`, `GroundTruthSet`,
`CandidateUniverse`, native repository, singleton profile, search result, and
recommendation, then writes `analysis/audit-v1.json` and
`analysis/per-query-v1.jsonl` below that run.

The audit replays exactly two configurations in a new patched PostgreSQL
planner sandbox: the empty extended-statistics configuration and
`SearchResult.final_ordered_candidate_ids`. It uses the frozen advisor's
`qerror-cardinality-floor-1-v1` loss and `weighted-workload-mean-v1` utility.
It does not recapture truth, resample data, rematerialize native statistics,
deploy statistics, or change optimizer semantics.

The weighted mean q-error is the authoritative optimization objective. The
reported median and tail quantiles are descriptive research metrics only.

`budget-expired-incomplete-round` means that the final configuration is
feasible and validated and its objective is an achieved value, but the search
did not prove local optimality within screened `K=8`; the unfinished round may
contain another improving move. Such a result must not be called optimal,
converged, or best possible.

The audit records both the source run research SHA and the separate committed
research SHA that implements the audit. It never rewrites the source run's
manifest, compact summary, advisor artifacts, or logs.
