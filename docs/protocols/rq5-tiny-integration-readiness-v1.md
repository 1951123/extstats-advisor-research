# RQ5 Tiny Integration Readiness Checklist v1

Classification: `offline-fixture-ready`; `integration-preflight-not-run`;
`formal-timing-not-authorized`.

## Offline gates

- [x] Fixture uses the Advisor sealed snapshot serializer.
- [x] Candidate universe is derived with the historical Advisor candidate
  derivation and validated against the sealed snapshot.
- [x] External workload bytes match the sealed snapshot workload.
- [x] Manifest binds snapshot, candidate universe, workload, source commits,
  candidate definitions, order, and query SQL digests.
- [x] Stock and patched setup SQL are byte-identical and contain no destructive
  cleanup.
- [x] Live adapter configuration remains explicitly opt-in and requires DSNs,
  identities, and experiment-owned output.
- [x] Formal Forest10 Analysis A/B manifests are unchanged.

## Not established by this record

- [ ] PostgreSQL binaries are installed and match the expected revisions.
- [ ] Either template database exists or contains the fixture rows.
- [ ] Native payloads can be materialized in a live patched backend.
- [ ] Physical OIDs and hypothetical activation order have passed live checks.
- [ ] Any EXPLAIN output or timing observation exists.
- [ ] The formal RQ5 benchmark is authorized.

## Decision

Offline preparation is **GO for a separately authorized tiny correctness
preflight**, subject to the environment checks in the companion setup guide.
Live execution in this task is **NO-GO by scope**.  The formal timing campaign is
**NO-GO** until the live preflight succeeds and receives separate authorization.
