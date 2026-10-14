"""Bounded disjoint-candidate multiplicative-separability experiment.

This module owns only pair selection, raw quadruple diagnostics, and the small
composition needed to reuse the existing Foundations oracle.  It does not
inspect PostgreSQL estimator formulas and it does not persist intermediate
Advisor artifacts.
"""

from __future__ import annotations

import itertools
import json
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .foundations_landscape import STRUCTURAL_QUERY_IDS
from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _FoundationsOracleRun,
    _run_oracle_configurations,
)
from .foundations_oracle import OracleEvaluation
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

DEFAULT_MAX_PAIRS = 6
DEFAULT_STATISTICS_TARGET = 100
DEFAULT_SAMPLE_ROWS = 30_000
DEFAULT_RESIDUAL_TOLERANCES = (1e-8, 1e-10, 1e-12)
DEFAULT_SINGLETON_CHANGE_TOLERANCE = 1e-12


class SeparabilityInputError(ValueError):
    """Raised when the bounded separability binding is incomplete."""


class SeparabilitySelectionError(ValueError):
    """Raised when a requested candidate pair is not eligible."""


def _candidate_id(candidate: Any) -> str:
    value = getattr(candidate, "candidate_id", None)
    if not isinstance(value, str) or not value:
        raise SeparabilitySelectionError("candidate has no non-empty candidate_id")
    return value


def _column_keys(candidate: Any) -> frozenset[tuple[str, int]]:
    relation_id = getattr(candidate, "relation_id", None)
    ordinals = tuple(getattr(candidate, "column_ordinals", ()))
    if not isinstance(relation_id, str) or not relation_id or not ordinals:
        raise SeparabilitySelectionError("candidate has no valid relation/column identity")
    return frozenset((relation_id, ordinal) for ordinal in ordinals)


def require_disjoint_candidates(candidate_a: Any, candidate_b: Any) -> None:
    """Reject a pair unless its actual relation/column identifiers are disjoint."""

    if _candidate_id(candidate_a) == _candidate_id(candidate_b):
        raise SeparabilitySelectionError("a candidate cannot be paired with itself")
    if _column_keys(candidate_a) & _column_keys(candidate_b):
        raise SeparabilitySelectionError("candidate pair has overlapping columns")


def activation_order_for_pair(
    candidate_ids: Sequence[str], global_precedence: Sequence[str]
) -> tuple[str, ...]:
    """Return the pair membership in the declared complete precedence order."""

    selected = tuple(candidate_ids)
    if len(selected) != 2 or len(set(selected)) != 2:
        raise SeparabilitySelectionError("a pair must contain two distinct candidates")
    precedence = tuple(global_precedence)
    if len(precedence) != len(set(precedence)):
        raise SeparabilitySelectionError("global precedence must not contain duplicates")
    if not set(selected).issubset(precedence):
        raise SeparabilitySelectionError("pair is not covered by global precedence")
    return tuple(candidate_id for candidate_id in precedence if candidate_id in set(selected))


@dataclass(frozen=True, slots=True)
class DisjointCandidatePair:
    candidate_a_id: str
    candidate_b_id: str
    kind_pair: tuple[str, str]
    columns_a: tuple[str, ...]
    columns_b: tuple[str, ...]
    joint_query_ids: tuple[str, ...]
    candidate_a_rank: int
    candidate_b_rank: int

    @property
    def candidate_ids(self) -> tuple[str, str]:
        return self.candidate_a_id, self.candidate_b_id

    @property
    def joint_query_count(self) -> int:
        return len(self.joint_query_ids)

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_a_id": self.candidate_a_id,
            "candidate_b_id": self.candidate_b_id,
            "kind_pair": list(self.kind_pair),
            "columns_a": list(self.columns_a),
            "columns_b": list(self.columns_b),
            "joint_query_ids": list(self.joint_query_ids),
            "joint_query_count": self.joint_query_count,
            "candidate_a_rank": self.candidate_a_rank,
            "candidate_b_rank": self.candidate_b_rank,
        }


def _incidence_by_candidate(universe: Any) -> dict[str, frozenset[str]]:
    result: dict[str, set[str]] = {}
    for item in universe.incidence:
        result.setdefault(item.candidate_id, set()).add(item.query_id)
    return {candidate_id: frozenset(query_ids) for candidate_id, query_ids in result.items()}


def rank_disjoint_candidate_pairs(universe: Any) -> tuple[DisjointCandidatePair, ...]:
    """Enumerate all disjoint pairs and rank by structural joint relevance.

    Query order follows the sealed query-profile order.  Ties are resolved by
    statistic-kind combination, static candidate precedence ranks, and IDs.
    """

    candidates = tuple(universe.candidates)
    profiles = tuple(
        profile
        for profile in universe.query_profiles
        if profile.analysis_status == "supported" and profile.weight > 0
    )
    supported_order = tuple(profile.query_id for profile in profiles)
    incidence = _incidence_by_candidate(universe)
    ranked: list[DisjointCandidatePair] = []
    for candidate_a, candidate_b in itertools.combinations(candidates, 2):
        if _column_keys(candidate_a) & _column_keys(candidate_b):
            continue
        joint = incidence.get(_candidate_id(candidate_a), frozenset()) & incidence.get(
            _candidate_id(candidate_b), frozenset()
        )
        joint_ids = tuple(query_id for query_id in supported_order if query_id in joint)
        kinds = tuple(sorted((candidate_a.kind, candidate_b.kind)))
        ranks = tuple(
            sorted((candidate_a.static_precedence_rank, candidate_b.static_precedence_rank))
        )
        ranked.append(
            DisjointCandidatePair(
                _candidate_id(candidate_a),
                _candidate_id(candidate_b),
                kinds,
                tuple(candidate_a.column_names),
                tuple(candidate_b.column_names),
                joint_ids,
                ranks[0],
                ranks[1],
            )
        )
    ranked.sort(
        key=lambda pair: (
            -pair.joint_query_count,
            pair.kind_pair,
            pair.candidate_a_rank,
            pair.candidate_b_rank,
            pair.candidate_a_id,
            pair.candidate_b_id,
        )
    )
    return tuple(ranked)


def select_disjoint_candidate_pairs(
    ranked_pairs: Sequence[DisjointCandidatePair],
    *,
    max_pairs: int = DEFAULT_MAX_PAIRS,
    require_joint_relevance: bool = True,
) -> tuple[DisjointCandidatePair, ...]:
    """Select a deterministic, kind-stratified prefix of the ranked pairs."""

    if isinstance(max_pairs, bool) or not isinstance(max_pairs, int) or not 1 <= max_pairs:
        raise SeparabilitySelectionError("max_pairs must be a positive integer")
    eligible = tuple(
        pair for pair in ranked_pairs if not require_joint_relevance or pair.joint_query_count > 0
    )
    groups: dict[tuple[str, str], list[DisjointCandidatePair]] = {}
    for pair in eligible:
        groups.setdefault(pair.kind_pair, []).append(pair)
    selected: list[DisjointCandidatePair] = []
    for offset in itertools.count():
        progressed = False
        for kind_pair in sorted(groups):
            group = groups[kind_pair]
            if offset < len(group) and len(selected) < max_pairs:
                selected.append(group[offset])
                progressed = True
        if len(selected) >= max_pairs or not progressed:
            break
    return tuple(selected[:max_pairs])


@dataclass(frozen=True, slots=True)
class MultiplicativeQuadruple:
    query_id: str
    estimates: Mapping[str, float]
    category: str
    valid: bool
    delta: float | None
    rho: float | None
    absolute_log_residual: float | None
    relative_deviation: float | None
    invalid_reason: str | None = None
    singleton_change_tolerance: float = DEFAULT_SINGLETON_CHANGE_TOLERANCE

    def passes(self, tolerance: float) -> bool:
        return (
            self.valid
            and self.absolute_log_residual is not None
            and self.absolute_log_residual <= tolerance
        )

    def as_dict(self, tolerances: Sequence[float] = DEFAULT_RESIDUAL_TOLERANCES) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "estimates": dict(self.estimates),
            "category": self.category,
            "valid": self.valid,
            "delta": self.delta,
            "rho": self.rho,
            "absolute_log_residual": self.absolute_log_residual,
            "relative_deviation": self.relative_deviation,
            "invalid_reason": self.invalid_reason,
            "singleton_change_tolerance": self.singleton_change_tolerance,
            "passes_by_tolerance": {
                str(tolerance): self.passes(tolerance) for tolerance in tolerances
            },
        }


def multiplicative_quadruple(
    query_id: str,
    *,
    estimate_empty: Any,
    estimate_a: Any,
    estimate_b: Any,
    estimate_ab: Any,
    singleton_change_tolerance: float = DEFAULT_SINGLETON_CHANGE_TOLERANCE,
) -> MultiplicativeQuadruple:
    if (
        isinstance(singleton_change_tolerance, bool)
        or not isinstance(singleton_change_tolerance, (int, float))
        or not math.isfinite(float(singleton_change_tolerance))
        or singleton_change_tolerance < 0
    ):
        raise SeparabilityInputError("singleton_change_tolerance must be finite and non-negative")
    change_tolerance = float(singleton_change_tolerance)
    estimates = {
        "empty": estimate_empty,
        "a": estimate_a,
        "b": estimate_b,
        "ab": estimate_ab,
    }
    numeric: dict[str, float] = {}
    for label, value in estimates.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return MultiplicativeQuadruple(
                query_id,
                estimates,
                "invalid",
                False,
                None,
                None,
                None,
                None,
                f"{label} estimate is not numeric",
                change_tolerance,
            )
        numeric[label] = float(value)
        if not math.isfinite(numeric[label]):
            return MultiplicativeQuadruple(
                query_id,
                estimates,
                "invalid",
                False,
                None,
                None,
                None,
                None,
                f"{label} estimate is not finite",
                change_tolerance,
            )
        if numeric[label] <= 0:
            return MultiplicativeQuadruple(
                query_id,
                estimates,
                "invalid",
                False,
                None,
                None,
                None,
                None,
                f"{label} estimate is not strictly positive",
                change_tolerance,
            )
    a_changed = abs(numeric["a"] - numeric["empty"]) > change_tolerance
    b_changed = abs(numeric["b"] - numeric["empty"]) > change_tolerance
    category = (
        "two-sided-informative"
        if a_changed and b_changed
        else "one-sided"
        if a_changed or b_changed
        else "non-informative"
    )
    delta = (
        math.log(numeric["ab"])
        - math.log(numeric["a"])
        - math.log(numeric["b"])
        + math.log(numeric["empty"])
    )
    if not math.isfinite(delta):
        return MultiplicativeQuadruple(
            query_id,
            estimates,
            category,
            False,
            None,
            None,
            None,
            None,
            "log residual is not finite",
            change_tolerance,
        )
    try:
        rho = math.exp(delta)
        relative_deviation = abs(math.expm1(delta))
    except OverflowError:
        return MultiplicativeQuadruple(
            query_id,
            estimates,
            category,
            False,
            delta,
            None,
            abs(delta),
            None,
            "ratio overflows finite representation",
            change_tolerance,
        )
    return MultiplicativeQuadruple(
        query_id,
        estimates,
        category,
        True,
        delta,
        rho,
        abs(delta),
        relative_deviation,
        None,
        change_tolerance,
    )


def _estimate_for(evaluation: OracleEvaluation, query_id: str) -> float:
    if evaluation.estimates is None or query_id not in evaluation.estimates:
        raise SeparabilityInputError(f"missing estimate for query {query_id}")
    return evaluation.estimates[query_id]


def diagnose_pair(
    pair: DisjointCandidatePair,
    evaluations: Sequence[OracleEvaluation],
    query_ids: Sequence[str],
    *,
    singleton_change_tolerance: float = DEFAULT_SINGLETON_CHANGE_TOLERANCE,
) -> tuple[MultiplicativeQuadruple, ...]:
    """Build quadruple diagnostics from the four fixed pair evaluations."""

    by_membership = {
        frozenset(evaluation.diagnostics.membership): evaluation for evaluation in evaluations
    }
    expected = {
        frozenset(),
        frozenset({pair.candidate_a_id}),
        frozenset({pair.candidate_b_id}),
        frozenset(pair.candidate_ids),
    }
    if set(by_membership) != expected:
        raise SeparabilityInputError("pair evaluations do not cover exactly the four memberships")
    empty = by_membership[frozenset()]
    singleton_a = by_membership[frozenset({pair.candidate_a_id})]
    singleton_b = by_membership[frozenset({pair.candidate_b_id})]
    both = by_membership[frozenset(pair.candidate_ids)]
    return tuple(
        multiplicative_quadruple(
            query_id,
            estimate_empty=_estimate_for(empty, query_id),
            estimate_a=_estimate_for(singleton_a, query_id),
            estimate_b=_estimate_for(singleton_b, query_id),
            estimate_ab=_estimate_for(both, query_id),
            singleton_change_tolerance=singleton_change_tolerance,
        )
        for query_id in query_ids
    )


@dataclass(frozen=True, slots=True)
class DisjointSeparabilityInput:
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
    research_commit_sha: str
    patched_postgres_commit_sha: str
    query_ids: tuple[str, ...] = STRUCTURAL_QUERY_IDS
    statistics_target: int = DEFAULT_STATISTICS_TARGET
    sample_rows: int = DEFAULT_SAMPLE_ROWS
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations_sep"

    def __post_init__(self) -> None:
        for field in (
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
            "research_commit_sha",
            "patched_postgres_commit_sha",
        ):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise SeparabilityInputError(f"{field} must be a non-empty string")
        if tuple(self.query_ids) != STRUCTURAL_QUERY_IDS:
            raise SeparabilityInputError(
                "separability experiment requires the fixed 32-query workload"
            )
        if any(
            not isinstance(candidate_id, str) or not candidate_id
            for candidate_id in self.global_precedence
        ):
            raise SeparabilityInputError("global precedence must contain non-empty candidate IDs")
        if len(self.global_precedence) != len(set(self.global_precedence)):
            raise SeparabilityInputError("global precedence must not contain duplicates")
        if self.sample_rows != DEFAULT_SAMPLE_ROWS:
            raise SeparabilityInputError(f"sample_rows must be {DEFAULT_SAMPLE_ROWS}")
        if self.statistics_target != DEFAULT_STATISTICS_TARGET:
            raise SeparabilityInputError(f"statistics_target must be {DEFAULT_STATISTICS_TARGET}")


@dataclass(frozen=True, slots=True)
class DisjointPairRun:
    pair: DisjointCandidatePair
    database_name: str
    evaluations: tuple[OracleEvaluation, ...]
    sandbox_verification: Mapping[str, Any]
    lifecycle_completed: bool = True

    @property
    def complete(self) -> bool:
        return self.lifecycle_completed and all(
            evaluation.diagnostics.status == "complete" for evaluation in self.evaluations
        )


@dataclass(frozen=True, slots=True)
class DisjointSeparabilityResult:
    inputs: DisjointSeparabilityInput
    eligible_disjoint_pair_count: int
    ranked_pairs: tuple[DisjointCandidatePair, ...]
    selected_pairs: tuple[DisjointCandidatePair, ...]
    pair_runs: tuple[DisjointPairRun, ...]

    @property
    def jointly_relevant_pair_count(self) -> int:
        return sum(pair.joint_query_count > 0 for pair in self.ranked_pairs)

    @property
    def primary_planner_query_count(self) -> int:
        return sum(
            evaluation.diagnostics.planner_query_successes
            for pair_run in self.pair_runs
            for evaluation in pair_run.evaluations
        )

    @property
    def complete(self) -> bool:
        return len(self.pair_runs) == len(self.selected_pairs) and all(
            pair_run.complete for pair_run in self.pair_runs
        )


def run_disjoint_separability_experiment(
    inputs: DisjointSeparabilityInput,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
    max_pairs: int = DEFAULT_MAX_PAIRS,
) -> DisjointSeparabilityResult:
    """Run up to six selected pairs, using a fresh database for every pair."""

    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    snapshot = services.load_snapshot(Path(inputs.snapshot_path))
    universe = services.load_candidate_universe(Path(inputs.candidate_universe_path), snapshot)
    services.load_ground_truth(Path(inputs.ground_truth_path), snapshot)
    ranked = rank_disjoint_candidate_pairs(universe)
    selected = select_disjoint_candidate_pairs(ranked, max_pairs=max_pairs)
    if not selected:
        raise SeparabilitySelectionError("no jointly relevant disjoint candidate pair is available")
    pair_runs: list[DisjointPairRun] = []
    for pair in selected:
        memberships = (
            (),
            (pair.candidate_a_id,),
            (pair.candidate_b_id,),
            pair.candidate_ids,
        )
        run: _FoundationsOracleRun = _run_oracle_configurations(
            inputs,
            memberships,
            services=services,
            lifecycle_factory=lifecycle_factory,
        )
        pair_runs.append(
            DisjointPairRun(
                pair,
                run.database_name,
                run.evaluations,
                run.sandbox_verification,
            )
        )
    return DisjointSeparabilityResult(
        inputs,
        len(ranked),
        ranked,
        selected,
        tuple(pair_runs),
    )


def summarize_quadruples(
    result: DisjointSeparabilityResult,
    *,
    tolerances: Sequence[float] = DEFAULT_RESIDUAL_TOLERANCES,
    singleton_change_tolerance: float = DEFAULT_SINGLETON_CHANGE_TOLERANCE,
) -> dict[str, Any]:
    records = []
    for pair_run in result.pair_runs:
        records.extend(
            diagnose_pair(
                pair_run.pair,
                pair_run.evaluations,
                result.inputs.query_ids,
                singleton_change_tolerance=singleton_change_tolerance,
            )
        )
    summary: dict[str, Any] = {
        "total_quadruples": len(records),
        "valid_quadruples": sum(record.valid for record in records),
        "category_counts": {
            category: sum(record.category == category for record in records)
            for category in ("non-informative", "one-sided", "two-sided-informative", "invalid")
        },
        "tolerance_pass_counts": {},
        "two_sided_tolerance_pass_counts": {},
    }
    for tolerance in tolerances:
        key = str(tolerance)
        summary["tolerance_pass_counts"][key] = sum(record.passes(tolerance) for record in records)
        summary["two_sided_tolerance_pass_counts"][key] = sum(
            record.category == "two-sided-informative" and record.passes(tolerance)
            for record in records
        )
    return summary


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value cannot be serialized")
    return value


def _pair_run_payload(
    pair_run: DisjointPairRun,
    query_ids: Sequence[str],
    tolerances: Sequence[float],
    singleton_change_tolerance: float,
) -> dict[str, Any]:
    observations = []
    for evaluation in pair_run.evaluations:
        diagnostics = evaluation.diagnostics
        observations.append(
            {
                "activation_membership": list(diagnostics.membership),
                "status": diagnostics.status,
                "objective": evaluation.objective,
                "estimates": dict(evaluation.estimates or {}),
                "diagnostics": diagnostics.as_dict(),
            }
        )
    quadruples = diagnose_pair(
        pair_run.pair,
        pair_run.evaluations,
        query_ids,
        singleton_change_tolerance=singleton_change_tolerance,
    )
    return {
        "pair": pair_run.pair.as_dict(),
        "database_name": pair_run.database_name,
        "lifecycle_completed": pair_run.lifecycle_completed,
        "sandbox_verification": dict(pair_run.sandbox_verification),
        "configurations": observations,
        "quadruples": [record.as_dict(tolerances) for record in quadruples],
    }


def write_disjoint_separability_result_json(
    result: DisjointSeparabilityResult,
    destination: Path,
    *,
    tolerances: Sequence[float] = DEFAULT_RESIDUAL_TOLERANCES,
    singleton_change_tolerance: float = DEFAULT_SINGLETON_CHANGE_TOLERANCE,
) -> None:
    """Publish one final observation file atomically without overwriting."""

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    inputs = result.inputs
    payload = {
        "classification": "foundations-disjoint-multiplicative-separability",
        "experiment_id": "foundations-disjoint-multiplicative-separability-v1",
        "source_bindings": {
            "research_commit_sha": inputs.research_commit_sha,
            "advisor_commit_sha": inputs.expected_advisor_sha,
            "patched_postgres_commit_sha": inputs.patched_postgres_commit_sha,
        },
        "artifact_bindings": {
            "snapshot_semantic_digest": inputs.expected_snapshot_digest,
            "candidate_universe_semantic_digest": inputs.expected_candidate_universe_digest,
            "ground_truth_semantic_digest": inputs.expected_ground_truth_digest,
        },
        "experiment_inputs": {
            "dataset_id": inputs.dataset_id,
            "relation_id": inputs.relation_id,
            "relation_schema": inputs.relation_schema,
            "relation_name": inputs.relation_name,
            "workload_id": inputs.workload_id,
            "sample_rows": inputs.sample_rows,
            "statistics_target": inputs.statistics_target,
            "query_ids": list(inputs.query_ids),
            "global_precedence": list(inputs.global_precedence),
        },
        "eligible_disjoint_pair_count": result.eligible_disjoint_pair_count,
        "jointly_relevant_pair_count": result.jointly_relevant_pair_count,
        "selected_pairs": [pair.as_dict() for pair in result.selected_pairs],
        "primary_planner_query_count": result.primary_planner_query_count,
        "complete": result.complete,
        "pair_runs": [
            _pair_run_payload(
                pair_run,
                inputs.query_ids,
                tolerances,
                singleton_change_tolerance,
            )
            for pair_run in result.pair_runs
        ],
        "quadruple_summary": summarize_quadruples(
            result,
            tolerances=tolerances,
            singleton_change_tolerance=singleton_change_tolerance,
        ),
    }
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=f".{uuid.uuid4().hex}.tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
