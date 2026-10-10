# VLDB manuscript

This directory contains the working manuscript for the `extstats-advisor-research`
repository.  It is a source document, not a copy of benchmark data or a second
experiment harness.  The sections refer to the versioned research artifacts in
`experiments/`, `paper-baselines/`, `docs/`, and the production advisor and
patched PostgreSQL revisions recorded in each RunManifest.

## Template

The manuscript uses the official `vldbproceedings/VLDB-Template` revision
`39c95f5c6fcbe652a83be24e4eff8f2134cd3fbc` (`Update main.tex`, 2026-10-02).
The copied files are `acmart.cls`, `pvldb.sty`, and
`ACM-Reference-Format.bst`.  The template's formatting files should not be
edited as part of ordinary paper writing.

## Build

From this directory, if `latexmk` is installed:

```bash
latexmk -pdf main.tex
```

The equivalent template-documented sequence is:

```bash
pdflatex main.tex
bibtex main
pdflatex main.tex
pdflatex main.tex
```

Build outputs are ignored by `paper/.gitignore`; do not commit PDFs, logs, or
auxiliary files.  Replace the anonymous author and placeholder VLDB metadata
before submission.

## Evidence discipline

The Census13, Forest10, Power7, and DMV11 materials currently provide pilot or
preliminary evidence.  A paper result becomes confirmatory only when its raw
artifact, derived artifact, manifest, and source commits can be traced without
ambiguity.  The research harness requires a clean committed research tree for
canonical runs and records the research, advisor, patched PostgreSQL, stock
PostgreSQL, dataset/workload, and experiment-parameter identities.

The machine-readable `paper-experiment-v1.json` records the confirmatory
protocol, source-identity freeze gates, experiment status ledger, required raw
and derived artifacts, and expected paper outputs. Its current freeze gate
selects `paper/system-freeze-v2.json`; historical artifacts retain their
embedded `system-freeze-v1` provenance. The v2 readiness evidence is in
`paper/system-freeze-v2-readiness-review-v1.json`.

`AdvisorSnapshot` remains the portable `(Sigma, S, P, W)` artifact. Exact or
authoritative cardinality truth is a separately validated `GroundTruthSet`
bound to the sealed snapshot and workload; it is used by the
`GroundTruthProvider` to evaluate planner estimates and define utility, not to
construct native statistics or change PostgreSQL's estimator. The current
truth sources are `production-exact-execution` and
`authoritative-external-exact`. The former is restricted to the supported
simple-selection counting contract and may be expensive; the latter records
external provenance without claiming advisor execution.

The production deployment contract is add-only: existing externally managed
extended statistics are outside advisor ownership.  The advisor does not model,
reconcile, drop, rename, or replace those objects.  A recommendation describes
the selected membership and required relative ordering of advisor-managed
objects; it is not a claim that a production catalog must equal that set.

## Submission reproducibility checklist

The current paper-facing evidence map is
`paper/evidence-to-manuscript-v2.json`; the claims audit, submission checklist,
and ranked follow-up backlog are `paper/scientific-claims-audit-v1.json`,
`paper/submission-readiness-review-v1.json`, and
`paper/research-backlog-v1.json`. The system-source mapping is
`paper/source-to-paper-technical-mapping-v1.json`, and the restructuring
change log is `paper/system-centric-restructuring-changelog-v1.md`. Before submission, archive the exact research
commit and these source/artifact trees in a permanent public repository, and
provide instructions for obtaining any benchmark inputs that cannot be
redistributed. A GitHub URL in the LaTeX source is not by itself a guarantee
that the full raw bundles, dependencies, and data-source instructions are
permanent or sufficient for artifact evaluation. The reproducibility package
should document the clean-tree requirement, frozen Advisor/PostgreSQL commits,
Python/LaTeX environment, offline validators, and the distinction between
formal runs, post-hoc analyses, and integration-only evidence.
