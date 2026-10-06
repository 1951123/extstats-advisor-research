# Forest10 canonical advisor run

The Forest10 canonical command is the one corrected end-to-end advisor run for
the audited AreCELearnedYet `base:test` workload:

```text
extstats-research run arecel-forest10 \
  --production-dsn "$STOCK_DSN" \
  --planner-dsn "$PATCHED_DSN" \
  --sample-rows 10000 \
  --sample-seed 42 \
  --statistics-target 100 \
  --candidate-limit 8 \
  --search-wall-clock-seconds 300 \
  --advisor-command /path/to/extstats-advisor
```

The stock relation is `public.forest10`, with 581012 rows, ten ordered
lowercase `double precision` columns, nullable catalog metadata, and no
extended statistics. Loading sets every ordinary statistics target to 100 and
performs exactly one initial `ANALYZE`. Before snapshot capture, the runner
records `forest_full_target100_ordinary` by explaining all 10000 audited test
queries against the full relation. Its labels are used only as descriptive
preflight truth; it is not a `GroundTruthSet`.

Snapshot capture uses the production advisor public APIs, while canonical truth
is imported from the audited AreCEL observation artifact through the frozen
`authoritative-external-exact` contract. The runner validates the artifact's
provenance, workload mapping, and snapshot binding before deriving candidates.
A mismatch, missing query, or extra query stops the run. The canonical path
performs only the configured representative sanity checks; it does not
recapture all 10,000 exact cardinalities. The production exact path remains
available as an explicit independent validation and cost experiment.

The planner side receives only the sealed 10000-row snapshot through the
patched PostgreSQL build. Candidate derivation, native materialization,
sandbox verification, singleton profiling, and greedy ADD search use the
production advisor contracts. A Recommendation is built only when the K=8
search terminates with `local-optimum` or
`all-screened-candidates-selected`; the runner never deploys it.

The ignored `runs/<RunID>/` directory contains the complete provenance and
artifact bundle. Tracked compact evidence belongs under:

```text
experiments/arecel-forest10/canonical-k8/
```

Runtime timings are recorded separately from semantic artifact identities and
are not inputs to the RunID.
