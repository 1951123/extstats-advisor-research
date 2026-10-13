# Foundations Interaction Landscape v1

This directory contains the versioned protocol binding and offline analysis status
for the first black-box interaction-landscape study.

Current state: **planned / blocked for live PostgreSQL execution**.

The manifest freezes an eight-candidate universe and 256 memberships, but the exact
matching serialized AdvisorSnapshot, CandidateUniverse incidence map, native
repository, and GroundTruthSet must be loaded and validated from the authorized
runtime artifact location before execution. The manifest therefore contains no
empirical objective values and no fabricated observations.

## Offline validation

The implementation is intentionally dependency-light and does not open a database:

```python
from extstats_advisor_research.foundations_interaction import (
    enumerate_subsets,
    validate_complete_landscape,
    pairwise_interaction_report,
)
```

`tests/test_foundations_interaction.py` exercises deterministic subset identities,
failure handling, monotonicity, interaction, submodularity, complementarity,
finite-universe optima, and oracle-assisted budget traces using synthetic functions.

## Evidence labels

- `synthetic`: offline set-function correctness fixtures only.
- `smoke-test`: a bounded live integration check, if separately authorized.
- `exploratory`: a complete 256-subset run with all bindings and integrity checks.

None of these labels permits claims beyond the finite declared universe.
