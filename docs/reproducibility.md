# Reproducibility contract

## Frozen inputs

`arecel-census13` uses the AreCELearnedYet upstream commit
`aa52da7768023270bad884232972e0b77ec6534a` and archive SHA256
`5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79`.
The audited local root is selected by `EXTSTATS_RESEARCH_DATA_ROOT`; absolute
paths are not semantic identities. The source CSV and canonical workload hashes
are recorded in `dataset-manifest.json`.

The SUT is the production advisor at
`524a17d4a9dea1a436bb4e30aadcc77e2bda4edd`. The patched PostgreSQL source is
`6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`. Both are checked before a
canonical run.

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

## ANALYZE disclosure

For a non-empty deployment, the Recommendation still contains one final
`ANALYZE "schema"."relation";`. That refreshes ordinary relation statistics,
builds the newly recommended extended statistics, and may rebuild data for
externally managed extended statistics on the same relation. This does not
transfer ownership of those external objects to the advisor. This harness does
not apply deployment.
