# Reproducibility contract

## Frozen inputs

`arecel-census13` uses the AreCELearnedYet upstream commit
`aa52da7768023270bad884232972e0b77ec6534a` and archive SHA256
`5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79`.
The audited local root is selected by `EXTSTATS_RESEARCH_DATA_ROOT`; absolute
paths are not semantic identities. The source CSV and canonical workload hashes
are recorded in `dataset-manifest.json`.

The current execution SUT is the production advisor at
`bb4d58d46e734981a4542de4bcf59441d3effb98`. The patched PostgreSQL source is
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. Both are checked before a
canonical run.

The full-data transfer is a one-time bridge from the immutable source run,
which used advisor `aa65af49fdbbf7443f8fa7295677724babfddfc5`, to that current
execution advisor. The bridge is valid only because the intervening production
change is limited to deployment preflight rendering of typmods with
`format_type(atttypid, atttypmod)` and selected-column collation verification.
Snapshot/acquisition, candidates, native statistics, sandbox, singleton and
precedence, optimization, search, Recommendation, and artifact contracts are
unchanged. This is not a generic source-artifact compatibility mechanism.

## Run identity and artifacts

`research-run-manifest-v1` hashes benchmark content, workload, the committed
research harness revision, both frozen system identities, sample rows/seed,
statistics target, candidate limit, and search budget. Its provenance fields
are `research_repository`, `research_commit_sha`, `advisor_repository`,
`advisor_commit_sha`, `patched_postgres_repository`, and
`patched_postgres_commit_sha`. Timestamps are runtime metadata only. The
manifest contains no DSN or password. Runtime commands are recorded in JSONL
with DSNs redacted.

The harness rejects a dirty research working tree or an unresolvable research
HEAD before a canonical run starts. The research commit participates in the
deterministic RunID, so identical experiment inputs under different harness
revisions cannot silently collide. A completed research unit must be committed
and pushed to `origin/main` before it is used as canonical experiment
provenance.

The optimizer evaluates the advisor workload against the production exact
truth. If externally managed extended statistics exist in a real production
database, the advisor objective is for its evaluated membership `M*`, not a
guaranteed objective for the combined state `E_existing ∪ M*`. The DBA decides
whether external statistics remain, are removed, are replaced, or coexist.

The run summary reports the production objective values and the complete
artifact digest chain. It does not introduce a competing research objective.

## Search-budget calibrations

Full run bundles are bulk experimental artifacts. Large immutable source
artifacts must be referenced by run ID and semantic digest rather than copied
into derivative experiments. Calibration and analysis commits contain compact
derived evidence only; `/runs/` remains ignored for ordinary future runs.

Search-budget calibration reuses the immutable snapshot, GroundTruthSet,
CandidateUniverse, NativeStatsRepository, and SingletonProfile from one
canonical run. It creates a fresh OptimizationPlan for each tested budget and
invokes the frozen advisor search API in an isolated patched planner backend.
It does not recapture, resample, rematerialize, or deploy.

## K=12 screening sufficiency

`diagnose screening-k12` is a single screening diagnostic. It reuses RunID
`bf7fda28d90b3da88e7a14e4` and its immutable source artifacts, but creates a
fresh OptimizationPlan with candidate limit 12 and a 600-second convergence
cap. The frozen production advisor starts greedy ADD from `M = {}`; it is not
initialized from the converged K=8 membership, and the K=12 membership is not
required to be nested in K=8. No Recommendation is built or deployed.

The diagnostic records source advisor `aa65af49fdbbf7443f8fa7295677724babfddfc5`
separately from execution advisor
`bb4d58d46e734981a4542de4bcf59441d3effb98`. The distinction uses the same
narrow deployment-preflight bridge documented above; optimization planning,
singleton precedence, planner sandbox, utility, greedy ADD, and SearchResult
contracts are unchanged. Ranks 9--12 are reported with native state, singleton
objective/improvement, live evaluation outcome, and the full accepted-move
trace. A budget termination is classified as `k12-incomplete` and never causes
the cap to be increased automatically.

## ANALYZE disclosure

For a non-empty deployment, the Recommendation still contains one final
`ANALYZE "schema"."relation";`. That refreshes ordinary relation statistics,
builds the newly recommended extended statistics, and may rebuild data for
externally managed extended statistics on the same relation. This does not
transfer ownership of those external objects to the advisor. This harness does
not apply deployment.

## Full-data transfer validation

`validate full-data-transfer` is one physical-transfer experiment for the
converged Census13 K=8 SearchResult. It builds the Recommendation through the
frozen advisor CLI from immutable source artifacts and explicitly checks
`D = F|M*`: accepted greedy move order is evidence about search, while
deployment order comes from frozen SingletonProfile precedence.

The experiment measures all 10,000 frozen workload queries on stock PostgreSQL
16.14 before deployment (P0), after the real add-only deployment and final
`ANALYZE` (P1), and in a paired research-only transaction that drops only the
seven advisor-managed objects, measures without `ANALYZE`, and rolls back (P2).
P2 is not production reconciliation. Existing production statistics are outside
advisor ownership, and the SearchResult objective describes `M*`, not a
guarantee for the combined state `E_existing ∪ M*`.

The compact derivative contains only the new Recommendation, DeploymentResult,
summary, and per-query transfer JSONL. It records source digests, calibration,
dataset identity, system revisions, physical object verification,
ordinary-stat fingerprints, and no credentials. The DBA remains responsible
for deciding whether externally managed statistics remain, are removed,
replaced, or coexist. The artifact records source and execution system roles
separately, including both research revisions, advisor revisions, the patched
PostgreSQL revision, stock PostgreSQL version, source digests, and calibration.
The optimizer evaluated `M*`, not necessarily `E_existing ∪ M*`; therefore the
SearchResult objective is not a guarantee for the combined production state
when external statistics coexist.
