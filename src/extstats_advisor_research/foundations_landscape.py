"""Offline-tested execution boundary for the eight-candidate Foundations landscape.

This module is intentionally specific to the prespecified structural study.  It
enumerates masks and delegates every evaluation to the existing black-box oracle;
it does not implement candidate generation, planner behavior, or a persistence
layer.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _run_oracle_configurations,
)
from .foundations_oracle import OracleEvaluation
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

STRUCTURAL_CANDIDATES = (
    "cand_0deae0d5de82a5a1fdc34111",
    "cand_76679921292e8c769dbbd7bc",
    "cand_77c3e92fff291d4d9515dc69",
    "cand_5ba9cc937a696a76876f820d",
    "cand_da7591327792c9bc98b427d7",
    "cand_ea8b2557e86251bb3994535b",
    "cand_4af628931e0d02eb50415f2b",
    "cand_c7595f3b6addcec1b9919cbe",
)
QUERY_SUFFIXES = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    17,
    19,
    21,
    22,
    23,
    30,
    43,
    51,
    61,
    67,
    81,
    111,
    139,
    146,
    169,
    197,
)
STRUCTURAL_QUERY_IDS = tuple(f"arecel_forest10_test_{suffix:06d}" for suffix in QUERY_SUFFIXES)
STRUCTURAL_CONFIGURATION_COUNT = 1 << len(STRUCTURAL_CANDIDATES)
EXPECTED_PRIMARY_PLANNER_QUERIES = STRUCTURAL_CONFIGURATION_COUNT * len(STRUCTURAL_QUERY_IDS)
STRUCTURAL_SAMPLE_ROWS = 30_000
STRUCTURAL_STATISTICS_TARGET = 100


class FoundationsLandscapeInputError(ValueError):
    """Raised when the fixed structural-study binding is incomplete."""


@dataclass(frozen=True, slots=True)
class FoundationsLandscapeInput:
    """Runtime inputs for one isolated structural-landscape invocation."""

    admin_dsn: str
    advisor_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    ground_truth_path: Path
    dataset_id: str
    relation_id: str
    relation_schema: str
    relation_name: str
    workload_id: str
    global_precedence: tuple[str, ...]
    expected_snapshot_digest: str
    expected_candidate_universe_digest: str
    expected_ground_truth_digest: str
    expected_advisor_sha: str
    statistics_target: int = STRUCTURAL_STATISTICS_TARGET
    sample_rows: int = STRUCTURAL_SAMPLE_ROWS
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations"
    query_ids: tuple[str, ...] = STRUCTURAL_QUERY_IDS

    def __post_init__(self) -> None:
        required_strings = (
            "admin_dsn",
            "dataset_id",
            "relation_id",
            "relation_schema",
            "relation_name",
            "workload_id",
            "expected_snapshot_digest",
            "expected_candidate_universe_digest",
            "expected_ground_truth_digest",
            "expected_advisor_sha",
        )
        for field in required_strings:
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise FoundationsLandscapeInputError(f"{field} must be a non-empty string")
        if tuple(self.query_ids) != STRUCTURAL_QUERY_IDS:
            raise FoundationsLandscapeInputError(
                "query_ids must equal the frozen 32-query structural-study workload"
            )
        precedence = tuple(self.global_precedence)
        if any(not isinstance(value, str) or not value for value in precedence):
            raise FoundationsLandscapeInputError(
                "global_precedence must contain non-empty candidate IDs"
            )
        if len(precedence) != len(set(precedence)):
            raise FoundationsLandscapeInputError("global_precedence must not contain duplicates")
        if (
            isinstance(self.sample_rows, bool)
            or not isinstance(self.sample_rows, int)
            or self.sample_rows != STRUCTURAL_SAMPLE_ROWS
        ):
            raise FoundationsLandscapeInputError(f"sample_rows must be {STRUCTURAL_SAMPLE_ROWS}")
        if (
            isinstance(self.statistics_target, bool)
            or not isinstance(self.statistics_target, int)
            or self.statistics_target != STRUCTURAL_STATISTICS_TARGET
        ):
            raise FoundationsLandscapeInputError(
                f"statistics_target must be {STRUCTURAL_STATISTICS_TARGET}"
            )
        if (
            isinstance(self.native_batch_size, bool)
            or not isinstance(self.native_batch_size, int)
            or self.native_batch_size < 1
        ):
            raise FoundationsLandscapeInputError("native_batch_size must be positive")


@dataclass(frozen=True, slots=True)
class StructuralConfiguration:
    mask: int
    membership: tuple[str, ...]


def enumerate_structural_configurations() -> tuple[StructuralConfiguration, ...]:
    """Enumerate masks 0..255, mapping bit i to candidate i."""

    return tuple(
        StructuralConfiguration(
            mask,
            tuple(
                candidate_id
                for bit, candidate_id in enumerate(STRUCTURAL_CANDIDATES)
                if mask & (1 << bit)
            ),
        )
        for mask in range(STRUCTURAL_CONFIGURATION_COUNT)
    )


@dataclass(frozen=True, slots=True)
class StructuralLandscapeEvaluation:
    mask: int
    membership: tuple[str, ...]
    oracle_evaluation: OracleEvaluation

    @property
    def activation_membership(self) -> tuple[str, ...]:
        """The actual π-restricted order observed by the oracle."""

        return self.oracle_evaluation.diagnostics.membership


@dataclass(frozen=True, slots=True)
class FoundationsLandscapeResult:
    database_name: str
    query_ids: tuple[str, ...]
    evaluations: tuple[StructuralLandscapeEvaluation, ...]
    sandbox_verification: dict[str, Any]

    @property
    def complete(self) -> bool:
        return len(self.evaluations) == STRUCTURAL_CONFIGURATION_COUNT and all(
            item.oracle_evaluation.diagnostics.status == "complete"
            and item.oracle_evaluation.diagnostics.planner_query_successes == len(self.query_ids)
            for item in self.evaluations
        )

    @property
    def primary_planner_query_count(self) -> int:
        return sum(
            item.oracle_evaluation.diagnostics.planner_query_successes for item in self.evaluations
        )


def _validate_structural_binding(snapshot: Any, universe: Any) -> None:
    candidate_ids = tuple(candidate.candidate_id for candidate in universe.candidates)
    if len(candidate_ids) != 57:
        raise FoundationsLandscapeInputError(
            f"structural landscape requires 57 eligible candidates, got {len(candidate_ids)}"
        )
    missing = sorted(set(STRUCTURAL_CANDIDATES) - set(candidate_ids))
    if missing:
        raise FoundationsLandscapeInputError(
            f"candidate universe is missing structural candidates: {missing}"
        )
    workload_ids = {query.query_id for query in snapshot.workload.queries}
    missing_queries = sorted(set(STRUCTURAL_QUERY_IDS) - workload_ids)
    if missing_queries:
        raise FoundationsLandscapeInputError(
            f"snapshot workload is missing structural queries: {missing_queries}"
        )


def run_foundations_structural_landscape(
    inputs: FoundationsLandscapeInput,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> FoundationsLandscapeResult:
    """Run the fixed 256-subset landscape through one oracle session.

    This function is intentionally live-capable but is not invoked by the offline
    test suite.  It creates no persistent repository or evaluation cache.
    """

    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    configurations = enumerate_structural_configurations()
    run = _run_oracle_configurations(
        inputs,
        tuple(configuration.membership for configuration in configurations),
        services=services,
        lifecycle_factory=lifecycle_factory,
        binding_validator=_validate_structural_binding,
    )
    evaluations = tuple(
        StructuralLandscapeEvaluation(configuration.mask, configuration.membership, evaluation)
        for configuration, evaluation in zip(configurations, run.evaluations, strict=True)
    )
    return FoundationsLandscapeResult(
        run.database_name,
        run.query_ids,
        evaluations,
        dict(run.sandbox_verification),
    )
