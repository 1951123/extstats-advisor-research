# Foundations Interaction Landscape v1

Status: `protocol-design; live execution pending`

## Research question and boundary

This protocol asks whether workload-derived candidate structure and selectively
measured interactions can support more efficient black-box statistics selection than
generic combinatorial optimization. It studies a finite set function exposed by a
DBMS oracle; it does not assume the oracle's internal estimator formula.

The first execution is bounded to one established Forest10 native realization and a
small eight-candidate universe. It is not evidence for all PostgreSQL workloads,
other DBMSs, or the full 57-candidate design space. It does not measure query
execution time or claim that q-error improvements imply runtime improvements.

## Mathematical contract

Let `C'` be the ordered eight-candidate subset, `M ⊆ C'`, and `Γ(M)` the deterministic
ordered configuration produced by the frozen plan precedence. Let `J(M)` be the
existing `weighted-workload-mean-v1` objective using
`qerror-cardinality-floor-1-v1`, and define `F(M) = J(∅) - J(M)`. The oracle is
black-box: only metadata, candidate incidence, measured objectives, and prior
evaluations are visible to the analysis.

The following assumptions are explicit and local to this protocol:

1. **Cardinality-estimation assumption:** q-error and workload weights are the
   existing contracts; they are not a claim that q-error is the only useful loss.
2. **Candidate-structure assumption:** column overlap, query incidence, native kind,
   and canonical precedence are observable metadata; structural association is not
   assumed to imply nonzero interaction.
3. **Configuration assumption:** membership and activation order are deterministic
   under one frozen native repository and planner session. A different physical
   realization is a different experiment.
4. **Fidelity assumption:** the snapshot, truth set, and native payload repository
   are fixed and independently bound. This controls the oracle input but does not
   prove sample-to-population or cross-version fidelity.
5. **Budget assumption:** the primary budget is the number of distinct complete
   configuration-oracle evaluations; planner query calls, preprocessing, cache hits,
   and wall-clock time are reported separately.

## Frozen data and candidate subset

The initial empirical binding is the current RQ5 Forest10 fixed-sample contract:

- Dataset/workload: `arecel_forest10_test_v1`.
- Snapshot: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- Candidate universe: 57 eligible candidates, digest
  `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`.
- Native repository: digest
  `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.
- Advisor source binding: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.
- Patched PostgreSQL source binding: `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`,
  version 16.14.
- Truth set: the exact `GroundTruthSet` bound by the source artifact used for the
  execution; its semantic digest is mandatory in the manifest and cannot be inferred
  from a nearby RQ4 file.

The eight candidates are frozen by a rule that does not inspect exhaustive,
held-out interaction outcomes:

- four highest singleton-gain candidates in the already-frozen singleton profile,
  ordered by decreasing recorded gain and then canonical candidate precedence;
- four structurally relevant weak-singleton candidates selected from the remaining
  eligible universe by zero/near-zero recorded singleton gain, maximum distinct
  query incidence, then canonical precedence and candidate ID.

The selection is a design-time use of existing singleton evidence, not a result of
this landscape. The manifest must record the input singleton-profile digest, all
selection keys, the resulting ordered IDs, and each candidate's columns, kind, and
incidence. If fewer than four qualifying weak-singleton candidates are present, the
protocol is blocked rather than silently replacing the rule. All eight candidates
must have `state=present` in the frozen native repository.

For the currently inspected Forest10 profile, the high-gain prefix is:
`cand_0deae0d5de82a5a1fdc34111`, `cand_76679921292e8c769dbbd7bc`,
`cand_77c3e92fff291d4d9515dc69`, and `cand_5ba9cc937a696a76876f820d`.
The four weak-singleton IDs are deliberately left as manifest data produced by the
deterministic rule, so the protocol does not hide a hand-picked structural choice.

There are `2^8 = 256` configurations. Enumerate every membership, including the
empty set, in canonical cardinality-then-universe order. The ordered membership is
the manifest's configuration identity; no set is silently re-ordered by a runtime
container.

## Oracle execution unit

One run binds one snapshot, truth set, candidate universe, native repository, planner
settings, configuration precedence, query order, and source identities. The runner:

1. validates all bindings before opening a planner session;
2. opens one backend-local session and registers the immutable repository;
3. evaluates the empty configuration and all 255 non-empty subsets in manifest
   order, activating each ordered configuration;
4. reuses the session only under the reset/activation contract recorded in the run;
5. records objective, elapsed monotonic time, oracle-call ordinal, planner query
   count, status, and error details for every membership; and
6. closes the session and records cleanup independently.

No per-subset native `ANALYZE` is allowed. No new Greedy search, singleton
profiling, truth acquisition, or candidate generation is part of the landscape.
Failures remain failed observations and invalidate a complete landscape summary.

## Required analysis

### Monotonicity

Check every directed edge `M → M ∪ {c}`. Report exact and tolerance-classified
violations, counts, fractions, magnitudes, and deterministic witnesses.

### Interactions

For every pair, report `I(a,b) = F({a,b}) - F({a}) - F({b})`. For every context
`M` disjoint from `{a,b}`, report the second difference
`I_M(a,b) = F(M+a+b)-F(M+a)-F(M+b)+F(M)`. Use exact recorded values internally;
descriptive signs use a declared absolute tolerance and retain raw values.

### Submodularity

Check every `A ⊆ B` and `c ∉ B` for diminishing returns. Report exact and
tolerance-qualified violations over this finite universe only.

### Complementarity and optima

Find candidates with weak/non-positive singleton gain but a positive conditional
marginal gain, retaining witness context. For each `k`, report exact-cardinality
and at-most-`k` optima, deterministic ties, and the unrestricted optimum within
`C'`. None is a global optimum over the original 57 candidates.

### Structure and budgets

Join interaction summaries with candidate column overlap, query-incidence overlap,
native kind, and graph distance as exploratory descriptive groups. Missing graph
edges do not mean zero interaction. Compare singleton ranking, existing Greedy ADD,
deterministic Random Search, and best observed solutions under distinct-evaluation
budgets. Greedy and Random traces replay the complete landscape and are therefore
oracle-assisted analyses, not online runtime claims. Cache hits, distinct oracle
calls, planner calls, preprocessing, and elapsed time remain separate.

## Reproducibility and status rules

The manifest is producer-bound and includes all source and semantic digests, exact
candidate ordering, loss/utility contracts, and the actual evaluation parameters.
The raw observation stream is append-only. A missing, duplicate, failed, or
identity-mismatched configuration is not imputed.

The package will include synthetic analyses for known monotone, non-monotone,
submodular, and complementary functions. These are `synthetic` evidence only. A
small PostgreSQL run, if the pinned environment is available and explicitly
authorized, is `smoke-test` evidence. A complete 256-subset Forest10 run satisfying
all bindings is `exploratory` for the foundations paper unless a later protocol
declares a confirmatory design.

No large or unbounded experiment is authorized by this protocol. If the pinned
snapshot, truth artifact, or native repository is unavailable, the offline audit and
synthetic tests are still valid, but empirical execution is `BLOCKED`.
