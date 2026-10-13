# EXP-002B Ground-Truth Provenance Audit

Status: **GO for offline current-snapshot binding; NO-GO for live smoke until environment gates pass**.

## Authoritative source

The Forest10 truth source is the committed AreCEL external observation artifact:

- Authority: `sfu-db/AreCELearnedYet`
- Upstream commit: `aa52da7768023270bad884232972e0b77ec6534a`
- Dataset identity: `fc25a1e2c7deefd2ba071985afd8dc78a7d547602e49f9fff7ed3def8d230556`
- Full CSV SHA256: `3484f3037f890f6bae46ae51fb387ee883351119667139c5894654276789a73c`
- Workload pickle SHA256: `501a3dcfe82ec5742fce4c3dc3b298eabcf78b82dab3a8ff8ad2914a61f4eaea`
- Label pickle SHA256: `34d830e6a30b48228ae41e6ae5643fb199e9794b3f36c915fb742af58fdfb974`
- Canonical workload SHA256: `a922d87660b2dec431a77c2e4a054c4f5528b0f25040859d76d0148a9ed73e74`
- Observation artifact SHA256: `88f752eda0a55e3b361ac2a24651a4faf5039aec77c4669bb7a8f6b0802a0744`
- Observation semantic digest: `4158c749c583d9c21137564361b6fb538ad9f6a7bcac11645384540d37476546`

The repository's Forest10 adapter independently verified the audited archive, source
hashes, 581,012 rows, schema, canonical workload, contiguous test indices, and source
query hashes. The policy status is `validated-provenance`; it does not claim a new
PostgreSQL COUNT equivalence experiment.

## Mapping checks

Against the current sealed snapshot
`ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`:

- 10,000 snapshot queries, source records, and labels are present.
- Query IDs and order are identical.
- Adapted literal SQL strings are identical.
- All workload weights are `1.0`.
- All observed cardinalities are non-negative integers and match the audited source labels.
- Workload ID is `arecel_forest10_test_v1`.
- Source mapping is count-star to select-star with the audited Forest10 relation and
  identifier adapters.

This validates query identity, literal semantics, ordering, coverage, uniqueness, and
cardinality-label meaning under the existing external exact-cardinality contract.

## New current-snapshot GroundTruthSet

Using Advisor's `import_authoritative_ground_truth()` and
`write_ground_truth_set()`, a new artifact was generated offline at the ignored runtime
path:

`.runtime/foundations-interaction-landscape-v1/current-snapshot-ground-truth-v1.json`

Its binding is:

- GroundTruthSet semantic digest: `bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2`
- Source snapshot digest: `ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5`
- Query count: 10,000
- Source kind: `authoritative-external-exact`
- Collection contract: `authoritative-external-exact-cardinality-v1`

Advisor's current validator accepts this new artifact against the current snapshot. The
old `f2ea981e...` GroundTruthSet remains untouched and remains historical evidence bound
to the old `65cc525...` snapshot.

The new artifact is not committed as a large raw truth copy. The committed binding record
and source artifact hash make its construction reproducible from the existing source
observation file and snapshot runtime artifact.

## Scientific boundary

The rebinding changes only the Advisor wrapper identity needed to bind the same audited
external labels to the current sealed snapshot. It does not edit source digest fields,
change labels, execute COUNT, or claim production-exact equivalence. The resulting oracle
is valid for the pre-registered external truth contract; a claim requiring independent
PostgreSQL exact-label equivalence would remain unsupported.
