# Foundations Truth Rebinding Protocol v1

## Scope

This protocol creates a new Advisor GroundTruthSet identity for the current Forest10
snapshot from already audited, DBMS-neutral AreCEL exact-cardinality observations. It does
not collect new truth, run PostgreSQL, or alter any historical truth artifact.

## Required gates

1. Verify the AreCEL authority, upstream revision, archive/source hashes, dataset identity,
   and 10,000 contiguous test labels.
2. Verify snapshot query IDs, order, adapted literal SQL, workload ID, and positive weights.
3. Verify each label is unique, non-negative, and equal to the audited source observation.
4. Import with Advisor's `import_authoritative_ground_truth()` rather than editing digest
   fields.
5. Validate the resulting GroundTruthSet against the exact current snapshot.
6. Record snapshot digest, new truth digest, observation hash, source revision, producer
   revision, and contract status.

## Identity boundary

The old production-exact GroundTruthSet is not modified or rebound in place. The new
artifact has its own semantic digest and is valid under
`authoritative-external-exact-cardinality-v1`. AreCEL Forest10 is provenance-validated,
not independently PostgreSQL COUNT-equivalence-validated; results must preserve that
qualification.

## Candidate policy

The interaction experiment uses a fixed prespecified eight-candidate universe. No current
singleton utility is used to select it. Membership ordering follows the current
CandidateUniverse static order and native repository identity.

## Smoke gate

Only after explicit lab DSNs, pinned binary identities, and resource isolation are verified
may a separate bounded smoke invocation evaluate empty, one singleton, and one pair. The
smoke must use one backend-local session, no ANALYZE, no truth recapture, and at most
`3 × 10,000 = 30,000` planner-query calls. EXP-002B does not run that invocation when the
environment gate is absent.
