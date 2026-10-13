# EXP-002 Artifact-Binding Audit

Status: `NO-GO`

This audit records the exact offline binding work completed for EXP-002. It is a
provenance audit, not a PostgreSQL observation and not evidence from the interaction
landscape.

## Source identities

| Component | Binding |
|---|---|
| Research producer at audit start | `f8c7c3566d58b78935148edf5f12e75db90769a5` |
| Historical Advisor API/evidence source | `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641` |
| Locally inspected Advisor implementation | `e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3` |
| Patched PostgreSQL | `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6` |
| Stock PostgreSQL reference | `0d1c00c624fa7367d4a895f44381887757289682` |
| PostgreSQL version | `16.14` |

The historical Advisor checkout is the binding for frozen Forest10 evidence. The current
installed package was inspected only to verify API behavior; it is not substituted for
the historical evidence source.

## Artifact matrix

| Artifact | Authoritative location | Digest / identity | Validation | Status |
|---|---|---|---|---|
| AdvisorSnapshot | `.runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/advisor-snapshot/` | `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5` | `load_snapshot` and `validate_snapshot` | Valid locally; ignored runtime artifact |
| CandidateUniverse | `.runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/candidate-universe.json` | `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134` | `load_candidate_universe` and snapshot compatibility validation | Valid locally; 57 candidates, 10,000 profiles, 210,640 incidence rows |
| NativeStatsRepository | `experiments/rq5-whatif-evaluation-cost-v4/formal-analysis-a/catalogless/repetition-01/native-stats-repository/` | `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184` | repository validator and compatibility validator | Valid; 57/57 present; tracked formal artifact |
| Workload | `experiments/.../runs/_forest10_workload.json` | `362b8b9af...` as bound by v4 manifest | manifest SHA and workload identity | Valid; `arecel_forest10_test_v1`, 10,000 queries |
| GroundTruthSet | `.runtime/rq1-forest10-matched/.../ground-truth-v1.json` | `f2ea981e2e11bc600fe7a21f2d27a041c82ed0313c1021a069929c04762a12be` | standalone validator passes; current snapshot compatibility fails | **Blocked** |
| SingletonProfile | `.runtime/rq1-forest10-matched/.../singleton-profile.json` | `80f515d...` | valid for old binding | Historical only: snapshot `65cc...`, universe `4eae...` |
| OptimizationPlan | old RQ1/RQ2 advisor-design artifacts | `09b3ded...` / `3a83b...` | valid for old binding | No exact current-snapshot plan found |

The `...` path abbreviations above are expanded in the local artifact inventory and are
not claims that ignored runtime artifacts are portable deliverables. No raw dataset,
credential, DSN, or large payload was copied into the repository.

## Exact blocker

The available GroundTruthSet declares source snapshot digest `65cc525...`; loading it
against the current 30K snapshot fails with Advisor's
`GroundTruthValidationError: ground-truth snapshot digest mismatch`. Its standalone
validation does not establish compatibility. The old SingletonProfile and
OptimizationPlan carry the same old snapshot/universe binding and cannot repair this
mismatch.

Consequently the eight IDs in the EXP-001 manifest are now bound to the current candidate
metadata and authoritative incidence, but the manifest remains blocked. A future separate
rebound realization must construct or locate a GroundTruthSet for the exact current
snapshot and assign it a new identity; it must not relabel old RQ4 artifacts.

## Candidate incidence

The current universe was loaded and incidence was extracted by candidate. The digest
contract is SHA-256 of compact UTF-8 JSON for the sorted query-ID list. The selected
candidates are all native `present` in the validated repository:

| Candidate | Kind | Static rank | Incidence count | Incidence digest |
|---|---|---:|---:|---|
| `cand_0deae0d5de82a5a1fdc34111` | MCV | 7 | 3659 | `d6646403945486a97b41467472ee86722db97a2ca7a7f8b0898b374967932ed6` |
| `cand_76679921292e8c769dbbd7bc` | MCV | 6 | 3689 | `7d51e58ce4e7e6b71cc56955d1a2d9e24856cc31b045f378a566b9d3590af3d0` |
| `cand_77c3e92fff291d4d9515dc69` | MCV | 8 | 3748 | `99f4534b7d8366e0b435e37f22cefbf1c53ef5c5af0cdb4d8a10ebe5a46a7709` |
| `cand_5ba9cc937a696a76876f820d` | MCV | 9 | 3653 | `8e14cf764a0cef02b8429f687a5bad5ea6bfc0085c6e67ac4c6c3b002ca3e59e` |
| `cand_da7591327792c9bc98b427d7` | dependencies | 20 | 3777 | `a261f2d6c8b4cca97df55101534128fb0cfcd67ce583c33770b1e147359abe97` |
| `cand_ea8b2557e86251bb3994535b` | dependencies | 46 | 3766 | `5a170dafac5adee5ad1629ad2738ffb183666922af949a2a1199b142a3c046f2` |
| `cand_4af628931e0d02eb50415f2b` | dependencies | 35 | 3748 | `99f4534b7d8366e0b435e37f22cefbf1c53ef5c5af0cdb4d8a10ebe5a46a7709` |
| `cand_c7595f3b6addcec1b9919cbe` | dependencies | 55 | 3743 | `a24c95f9604b1464ab6d348cac3d793bb2a746e655ac976fe40cd40195c74486` |

The current CandidateUniverse order is authoritative for activation and differs from the
preliminary ranks previously recorded by EXP-001. Selection IDs and the deterministic
selection rule are unchanged; the manifest now records the validated current metadata.

## API audit

The historical/current API surface needed by a future oracle exists: `load_snapshot`,
`load_candidate_universe`, native repository loading/compatibility validation,
`PostgresPlannerSession`, `PostgresStatisticsConfiguration`, and
`WeightedWorkloadUtility`. `PostgresPlannerSession` supports one backend-local registered
repository, ordered activation, and `estimate_query`. GroundTruth validation correctly
rejects the mismatched old snapshot. No source repository was changed.

## Readiness decision

**NO-GO for the live smoke test and EXP-003.** Exact candidate incidence and three current
runtime artifacts are validated, but the current-snapshot GroundTruthSet is not. The
research-owned oracle adapter can be implemented and tested offline, but no objective may
be evaluated or reported until a compatible truth artifact is bound. This preserves the
black-box claim boundary and prevents a silent substitution of historical evidence.
