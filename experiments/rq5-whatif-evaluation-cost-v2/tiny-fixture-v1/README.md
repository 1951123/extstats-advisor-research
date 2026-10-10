# RQ5 Tiny Cost Fixture v1

This directory is a standalone correctness-preflight fixture for the RQ5
What-if Evaluation Cost harness.  It is not Forest10 evidence, not timing data,
and not a formal RQ5 result.

The sealed snapshot and candidate universe were generated with the historical
Advisor implementation `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` and the
research producer commit recorded in `manifest-v1.json`.  The generator uses
Advisor's own `write_snapshot` and `write_candidate_universe` serializers; the
offline validator reloads both artifacts before accepting the manifest.

Contents:

- `snapshot/`: sealed AdvisorSnapshot v1 with one relation and 12 rows;
- `candidate-universe.json`: six derived candidates: three MCV and three
  dependencies definitions over the three column pairs;
- `workload.json`: five fixed single-table selection queries;
- `manifest-v1.json`: two ordered configurations and all source/digest bindings;
- `fixture-rows-stock.sql` and `fixture-rows-patched.sql`: byte-identical,
  prepared setup scripts for separately owned lab databases;
- `identities/`: expected stock and patched source identity records, which must
  be checked against actual binaries before live use.

Regenerate a new fixture only into a new empty destination:

```bash
rq5-tiny-fixture generate \
  --output /absolute/path/to/new/tiny-fixture-v1 \
  --advisor-root /absolute/path/to/advisor-at-0865 \
  --producer-sha <research-producer-commit>
```

Validate this committed fixture offline with:

```bash
rq5-tiny-fixture validate \
  --output experiments/rq5-whatif-evaluation-cost-v2/tiny-fixture-v1
```

The SQL files are intentionally not executed by generation, tests, or CI.
Environment preparation and the separately authorized live command are
documented in `docs/protocols/rq5-tiny-integration-environment-v1.md`.
