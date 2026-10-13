# EXP-002B Candidate-Selection Audit

Decision: **GO only as Option A, a prespecified structural study universe**.

## Current artifact evidence

The eight EXP-001 IDs are all present in the current 57-candidate universe
`292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134` and all have
`present` native payloads in repository
`fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.
Their relation, columns, statistic kinds, current static precedence, incidence counts,
and incidence digests are recorded in the EXP-002 artifact audit.

## Singleton evidence audit

No SingletonProfile or OptimizationPlan bound to the current snapshot
`ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`, current universe,
and the new GroundTruthSet `bcea3b0f...` was found. The available historical profile and
RQ4 design artifacts bind to snapshot `65cc525...`, universe `4eae9a...`, and truth
`f2ea981e...`. Their numeric singleton objectives are therefore not transferred to the
current realization.

The candidate IDs are not re-ranked and no current singleton measurement is performed in
EXP-002B. This avoids both realization substitution and post-hoc selection on a newly
available objective.

## Chosen option

The versioned EXP-002B manifest preserves all eight pre-registered IDs but changes their
interpretation to:

`prespecified-structural-study-universe-v1`

The four historical “high-singleton-gain” and four “weak-singleton-structural” labels are
not claims about current utility. They are retained only as historical selection
provenance. No statement that the set is current top-four, weak-four, or utility-ranked is
permitted.

This is scientifically stronger than silently re-establishing the selection from current
measurements: the universe remains fixed independently of the interaction outcomes and is
reproducible. The cost is that EXP-002B no longer supports a current singleton-ranking
claim. A future Option B study may measure current singleton gains under a new experiment
identity, but that is not part of this task.
