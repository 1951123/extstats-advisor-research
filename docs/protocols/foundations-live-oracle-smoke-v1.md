# Foundations Live Oracle Smoke Protocol v1

Status: **protocol defined; execution blocked by missing prepared sandbox**
Evidence class: `smoke-test` only when a complete invocation actually runs.

## Objective and boundary

Validate the existing black-box membership oracle on one pinned patched
PostgreSQL backend. This is a correctness and feasibility smoke test, not EXP-003,
not exhaustive enumeration, and not evidence about a new search algorithm.

The oracle must use the existing Advisor `PostgresPlannerSession`,
`WeightedWorkloadUtility`, `ArtifactGroundTruthProvider`, and `QErrorLoss`. The
research `BlackBoxMembershipOracle` supplies canonical membership, explicit failure
states, cache accounting, and planner-call accounting. It must not reimplement
estimation or utility semantics.

## Frozen binding

The invocation must bind all of the following before connecting:

- Snapshot: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`.
- CandidateUniverse: `292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134`.
- NativeStatsRepository: `fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184`.
- GroundTruthSet: `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2`.
- Workload: `arecel_forest10_test_v1`, exactly 10,000 positive-weight queries.
- Advisor: `0865c5a6afb8bc176bd7d3b10b13b3da83f1f641`.
- Patched PostgreSQL: `6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6`, PostgreSQL 16.14.
- Candidate policy: `prespecified-structural-study-universe-v1`.

The managed lab endpoint is the fixed patched role described by
`docs/postgres-lab.md`. Credentials and secrets must never be written to an
artifact. An invocation must record only a redacted endpoint identity and the
managed database name.

## Three primary configurations

Candidate IDs are frozen before observing any objective:

```text
M0 = ()
M1 = (cand_0deae0d5de82a5a1fdc34111)
M2 = (cand_0deae0d5de82a5a1fdc34111,
      cand_76679921292e8c769dbbd7bc)
```

The pair order is the declared CandidateUniverse order. Every primary
configuration evaluates the same 10,000-query workload in canonical workload
order. With no cache reuse, the planned volume is 30,000 planner-query calls and
three utility evaluations. This is a contract-derived count, not a runtime result.

After M2, the session may activate M0 once without running workload queries and
verify an empty active-OID list. That is a reset diagnostic, not a fourth primary
configuration or objective observation.

## Preconditions

The smoke must stop before workload planning unless all gates pass:

1. The managed stock/patched source and running binary identities match the frozen
   pins and PostgreSQL 16.14.
2. The Advisor validators pass for the four bindings above.
3. The patched capability probe reports the pinned backend contract.
4. The target database is an experiment-owned managed lab database.
5. The full typed Forest10 relation and its schema identity are verified.
6. The patched database already contains the Advisor planner sandbox, including the
   30,000-row frozen relation, metadata, ordinary-statistics fingerprint, full
   population metadata, and no physical extended-statistics objects.
7. Repository registration can be performed without changing the frozen payload
   artifact. No per-configuration ANALYZE is allowed.

At the current EXP-002C audit, condition 6 is false. The existing
`PostgresPlannerSession.open()` check would therefore reject the database, so no
smoke run is valid yet.

## Execution and measurements

Use one backend-local planner session. Record, per configuration:

- canonical ordered membership and configuration identity;
- activation result and observed virtual OIDs;
- planner attempts and successful calls (expected 10,000/10,000);
- `J(M)` from the existing utility result;
- elapsed time for session preparation, repository registration, activation,
  workload planning, utility aggregation, and total lifecycle;
- source, artifact, backend, and database identities;
- complete status or the exact failure and cleanup status.

The smoke must preserve append-only raw observations in a fresh invocation
directory. A partial configuration is a failed observation, never zero, infinity,
or a fabricated objective. Repeated or diagnostic calls must be separately marked
and excluded from the three-primary summary.

## No-go conditions

Do not run the smoke if the sandbox is absent, the sample/repository identity is
uncertain, the actual running server identity cannot be established, a database is
not clearly managed and isolated, or a query/workload binding differs. Do not
repair a mismatch by running ANALYZE, recapturing truth, changing source pins, or
substituting nearby artifacts under this protocol.

## Prospective EXP-003 accounting

EXP-003 planning must separately account for session setup, repository registration,
activation, planner calls, utility aggregation, and cleanup. Three smoke
configurations cannot provide a precise runtime forecast for 256 configurations.
Longer execution would require append-only observations, deterministic resume
identity, checkpoint validation, and explicit interruption recovery.
