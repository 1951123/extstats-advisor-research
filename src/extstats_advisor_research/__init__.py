"""Research harness for reproducible experiments against a frozen advisor."""

__version__ = "0.1.0"

FROZEN_RESEARCH_REPOSITORY = "1951123/extstats-advisor-research"
FROZEN_ADVISOR_REPOSITORY = "1951123/extstats-advisor"
FROZEN_ADVISOR_SHA = "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
# Historical Forest10/Power7 source runs were produced by the advisor revision
# immediately before authoritative external truth was added.
PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA = "bb4d58d46e734981a4542de4bcf59441d3effb98"
# The immutable source RunManifest predates the deployment-preflight hotfix.
TRANSFER_SOURCE_ADVISOR_SHA = "aa65af49fdbbf7443f8fa7295677724babfddfc5"
FROZEN_PATCHED_POSTGRES_REPOSITORY = "1951123/postgresql-pgextadv"
FROZEN_PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"

from .external_truth import import_audited_authoritative_truth, write_authoritative_observations

__all__ = [
    "FROZEN_ADVISOR_REPOSITORY",
    "FROZEN_ADVISOR_SHA",
    "FROZEN_PATCHED_POSTGRES_REPOSITORY",
    "FROZEN_PATCHED_POSTGRES_SHA",
    "FROZEN_RESEARCH_REPOSITORY",
    "PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA",
    "TRANSFER_SOURCE_ADVISOR_SHA",
    "import_audited_authoritative_truth",
    "write_authoritative_observations",
]
