"""Offline set-function tooling for Foundations Interaction Landscape v1.

The module deliberately contains no PostgreSQL or Advisor imports.  It validates and
analyzes observations produced by the existing black-box oracle boundary; live oracle
execution remains an adapter concern and is not performed here.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class LandscapeValidationError(ValueError):
    """Raised when a finite landscape cannot be treated as complete evidence."""


def _ordered_unique(values: Iterable[str], *, label: str) -> tuple[str, ...]:
    result = tuple(values)
    if any(not isinstance(value, str) or not value for value in result):
        raise LandscapeValidationError(f"{label} must contain non-empty strings")
    if len(result) != len(set(result)):
        raise LandscapeValidationError(f"{label} must not contain duplicates")
    return result


def canonical_membership(candidate_order: Sequence[str], members: Iterable[str]) -> tuple[str, ...]:
    """Return a membership in the declared order, rejecting unknown IDs."""

    universe = _ordered_unique(candidate_order, label="candidate_order")
    requested = _ordered_unique(members, label="members")
    unknown = sorted(set(requested) - set(universe))
    if unknown:
        raise LandscapeValidationError(f"membership contains unknown candidates: {unknown}")
    requested_set = set(requested)
    return tuple(candidate_id for candidate_id in universe if candidate_id in requested_set)


def configuration_identity(membership: Sequence[str]) -> str:
    """Create a collision-resistant identity for an ordered membership."""

    ordered = _ordered_unique(membership, label="membership")
    encoded = json.dumps(list(ordered), ensure_ascii=False, separators=(",", ":")).encode()
    return f"cfg-{hashlib.sha256(encoded).hexdigest()}"


def enumerate_subsets(candidate_order: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Enumerate all subsets in cardinality-then-declared-order order."""

    universe = _ordered_unique(candidate_order, label="candidate_order")
    return tuple(
        subset
        for size in range(len(universe) + 1)
        for subset in itertools.combinations(universe, size)
    )


@dataclass(frozen=True)
class ConfigurationObservation:
    """One immutable black-box configuration result or preserved failure."""

    configuration_id: str
    membership: tuple[str, ...]
    status: str
    objective: float | None
    elapsed_ns: int
    oracle_invocations: int
    planner_query_count: int
    error: str | None = None

    def __post_init__(self) -> None:
        if self.configuration_id != configuration_identity(self.membership):
            raise LandscapeValidationError("configuration identity does not match membership")
        if self.status not in {"complete", "failed"}:
            raise LandscapeValidationError("observation status must be complete or failed")
        if self.elapsed_ns < 0 or self.oracle_invocations < 0 or self.planner_query_count < 0:
            raise LandscapeValidationError(
                "observation counts and elapsed time must be non-negative"
            )
        if self.status == "complete":
            if self.objective is None or not math.isfinite(self.objective):
                raise LandscapeValidationError("complete observations require a finite objective")
            if self.error is not None:
                raise LandscapeValidationError("complete observations cannot carry an error")
        elif not self.error:
            raise LandscapeValidationError("failed observations must preserve an error")

    @classmethod
    def complete(
        cls,
        membership: Sequence[str],
        objective: float,
        *,
        elapsed_ns: int,
        oracle_invocations: int = 1,
        planner_query_count: int = 0,
    ) -> ConfigurationObservation:
        ordered = tuple(membership)
        return cls(
            configuration_identity(ordered),
            ordered,
            "complete",
            float(objective),
            elapsed_ns,
            oracle_invocations,
            planner_query_count,
        )

    @classmethod
    def failed(
        cls,
        membership: Sequence[str],
        error: str,
        *,
        elapsed_ns: int = 0,
        oracle_invocations: int = 0,
        planner_query_count: int = 0,
    ) -> ConfigurationObservation:
        ordered = tuple(membership)
        return cls(
            configuration_identity(ordered),
            ordered,
            "failed",
            None,
            elapsed_ns,
            oracle_invocations,
            planner_query_count,
            error,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "membership": list(self.membership),
            "status": self.status,
            "objective": self.objective,
            "elapsed_ns": self.elapsed_ns,
            "oracle_invocations": self.oracle_invocations,
            "planner_query_count": self.planner_query_count,
            "error": self.error,
        }


def validate_complete_landscape(
    observations: Iterable[ConfigurationObservation], candidate_order: Sequence[str]
) -> dict[tuple[str, ...], ConfigurationObservation]:
    """Validate exact finite coverage and return observations by canonical membership."""

    universe = _ordered_unique(candidate_order, label="candidate_order")
    expected = set(enumerate_subsets(universe))
    by_membership: dict[tuple[str, ...], ConfigurationObservation] = {}
    for observation in observations:
        membership = canonical_membership(universe, observation.membership)
        if membership != observation.membership:
            raise LandscapeValidationError("observation membership is not in canonical order")
        if membership in by_membership:
            raise LandscapeValidationError(f"duplicate configuration: {membership}")
        by_membership[membership] = observation
        if observation.status != "complete":
            raise LandscapeValidationError(
                f"failed configuration prevents complete landscape: {membership}"
            )
    missing = expected - set(by_membership)
    extra = set(by_membership) - expected
    if missing or extra:
        raise LandscapeValidationError(
            f"landscape coverage mismatch; missing={sorted(missing)!r}, extra={sorted(extra)!r}"
        )
    return by_membership


def improvement_values(
    observations: Mapping[tuple[str, ...], ConfigurationObservation],
) -> dict[tuple[str, ...], float]:
    """Convert lower-is-better objective values to F(M) improvement values."""

    baseline = observations[()].objective
    if baseline is None:
        raise LandscapeValidationError("empty configuration objective is required")
    return {
        membership: float(baseline - observation.objective)
        for membership, observation in observations.items()
    }


def _sign(value: float, tolerance: float) -> str:
    if value > tolerance:
        return "positive"
    if value < -tolerance:
        return "negative"
    return "zero-within-tolerance"


def monotonicity_report(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    universe = _ordered_unique(candidate_order, label="candidate_order")
    violations = []
    edges = 0
    for membership in enumerate_subsets(universe):
        members = set(membership)
        for candidate_id in universe:
            if candidate_id in members:
                continue
            successor = canonical_membership(universe, (*membership, candidate_id))
            delta = improvements[successor] - improvements[membership]
            edges += 1
            if delta < 0:
                violations.append(
                    {
                        "from": list(membership),
                        "candidate_id": candidate_id,
                        "to": list(successor),
                        "marginal_gain": delta,
                    }
                )
    return {
        "edge_count": edges,
        "violation_count": len(violations),
        "violation_fraction": len(violations) / edges if edges else 0.0,
        "tolerance": tolerance,
        "violations": violations,
        "tolerance_violation_count": sum(
            1 for item in violations if item["marginal_gain"] < -tolerance
        ),
    }


def pairwise_interaction_report(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    universe = _ordered_unique(candidate_order, label="candidate_order")
    records = []
    for first, second in itertools.combinations(universe, 2):
        pair = (first, second)
        remaining = tuple(candidate_id for candidate_id in universe if candidate_id not in pair)
        for size in range(len(remaining) + 1):
            for context in itertools.combinations(remaining, size):
                plus_first = canonical_membership(universe, (*context, first))
                plus_second = canonical_membership(universe, (*context, second))
                plus_pair = canonical_membership(universe, (*context, first, second))
                value = (
                    improvements[plus_pair]
                    - improvements[plus_first]
                    - improvements[plus_second]
                    + improvements[context]
                )
                records.append(
                    {
                        "candidate_ids": [first, second],
                        "context": list(context),
                        "interaction": value,
                        "sign": _sign(value, tolerance),
                    }
                )
    counts = {
        sign: sum(item["sign"] == sign for item in records)
        for sign in ("positive", "negative", "zero-within-tolerance")
    }
    return {
        "record_count": len(records),
        "tolerance": tolerance,
        "sign_counts": counts,
        "records": records,
    }


def submodularity_report(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    *,
    tolerance: float = 1e-12,
) -> dict[str, Any]:
    universe = _ordered_unique(candidate_order, label="candidate_order")
    subsets = enumerate_subsets(universe)
    violations = []
    checks = 0
    for small in subsets:
        small_set = set(small)
        for large in subsets:
            large_set = set(large)
            if not small_set.issubset(large_set):
                continue
            for candidate_id in universe:
                if candidate_id in large_set:
                    continue
                small_plus = canonical_membership(universe, (*small, candidate_id))
                large_plus = canonical_membership(universe, (*large, candidate_id))
                difference = (
                    improvements[small_plus]
                    - improvements[small]
                    - improvements[large_plus]
                    + improvements[large]
                )
                checks += 1
                if difference < 0:
                    violations.append(
                        {
                            "A": list(small),
                            "B": list(large),
                            "candidate_id": candidate_id,
                            "diminishing_returns_margin": difference,
                        }
                    )
    return {
        "check_count": checks,
        "violation_count": len(violations),
        "violation_fraction": len(violations) / checks if checks else 0.0,
        "tolerance": tolerance,
        "tolerance_violation_count": sum(
            1 for item in violations if item["diminishing_returns_margin"] < -tolerance
        ),
        "violations": violations,
    }


def complementarity_witnesses(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    *,
    singleton_threshold: float = 1e-12,
    conditional_threshold: float = 1e-12,
) -> list[dict[str, Any]]:
    universe = _ordered_unique(candidate_order, label="candidate_order")
    weak = [
        candidate_id
        for candidate_id in universe
        if improvements[(candidate_id,)] <= singleton_threshold
    ]
    witnesses = []
    for candidate_id in weak:
        remaining = tuple(value for value in universe if value != candidate_id)
        for size in range(len(remaining) + 1):
            for context in itertools.combinations(remaining, size):
                context_with = canonical_membership(universe, (*context, candidate_id))
                marginal = improvements[context_with] - improvements[context]
                if marginal > conditional_threshold:
                    witnesses.append(
                        {
                            "candidate_id": candidate_id,
                            "context": list(context),
                            "singleton_gain": improvements[(candidate_id,)],
                            "conditional_marginal_gain": marginal,
                        }
                    )
    return witnesses


def exact_optima_by_budget(
    improvements: Mapping[tuple[str, ...], float], candidate_order: Sequence[str]
) -> dict[str, Any]:
    universe = _ordered_unique(candidate_order, label="candidate_order")
    exact: dict[str, Any] = {}
    at_most: dict[str, Any] = {}
    best_so_far: tuple[tuple[str, ...], float] | None = None
    for size in range(len(universe) + 1):
        members = [membership for membership in improvements if len(membership) == size]
        chosen = max(
            members,
            key=lambda membership: (
                improvements[membership],
                tuple(-universe.index(x) for x in membership),
            ),
        )
        exact[str(size)] = {"membership": list(chosen), "improvement": improvements[chosen]}
        if best_so_far is None or improvements[chosen] > best_so_far[1]:
            best_so_far = (chosen, improvements[chosen])
        assert best_so_far is not None
        at_most[str(size)] = {"membership": list(best_so_far[0]), "improvement": best_so_far[1]}
    unrestricted = max(
        improvements, key=lambda membership: (improvements[membership], -len(membership))
    )
    return {
        "exact_k": exact,
        "at_most_k": at_most,
        "unrestricted_within_universe": {
            "membership": list(unrestricted),
            "improvement": improvements[unrestricted],
        },
    }


def _trace_result(
    *,
    method: str,
    budget: int,
    evaluated: list[tuple[str, ...]],
    best: tuple[str, ...],
    improvements: Mapping[tuple[str, ...], float],
    status: str,
) -> dict[str, Any]:
    return {
        "method": method,
        "budget": budget,
        "distinct_evaluations": len(evaluated),
        "evaluated_memberships": [list(membership) for membership in evaluated],
        "best_membership": list(best),
        "best_improvement": improvements[best],
        "status": status,
    }


def greedy_add_trace(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    evaluation_budget: int,
) -> dict[str, Any]:
    """Replay Greedy ADD from a complete landscape; label it oracle-assisted."""

    if evaluation_budget < 1:
        raise ValueError("evaluation_budget must include the empty baseline")
    universe = _ordered_unique(candidate_order, label="candidate_order")
    evaluated: list[tuple[str, ...]] = []
    cache: set[tuple[str, ...]] = set()

    def observe(membership: tuple[str, ...]) -> bool:
        if membership in cache:
            return True
        if len(evaluated) >= evaluation_budget:
            return False
        cache.add(membership)
        evaluated.append(membership)
        return True

    observe(())
    current = ()
    if len(evaluated) < evaluation_budget:
        for candidate_id in universe:
            if not observe((candidate_id,)):
                break
        best_singleton = max(
            (membership for membership in evaluated if len(membership) == 1),
            key=lambda membership: (
                improvements[membership],
                tuple(-universe.index(x) for x in membership),
            ),
            default=(),
        )
        if best_singleton and improvements[best_singleton] > improvements[current]:
            current = best_singleton
    while len(evaluated) < evaluation_budget:
        proposals = [
            canonical_membership(universe, (*current, candidate_id))
            for candidate_id in universe
            if candidate_id not in current
        ]
        unseen = [proposal for proposal in proposals if proposal not in cache]
        if not unseen:
            break
        for proposal in unseen:
            if not observe(proposal):
                break
        best = max(
            (proposal for proposal in unseen if proposal in cache),
            key=lambda membership: (
                improvements[membership],
                tuple(-universe.index(x) for x in membership),
            ),
            default=current,
        )
        if improvements[best] <= improvements[current]:
            break
        current = best
    status = (
        "complete"
        if len(evaluated) < evaluation_budget or current == max(improvements, key=improvements.get)
        else "budget-censored"
    )
    return _trace_result(
        method="greedy-add-oracle-assisted-replay",
        budget=evaluation_budget,
        evaluated=evaluated,
        best=current,
        improvements=improvements,
        status=status,
    )


def random_search_trace(
    improvements: Mapping[tuple[str, ...], float],
    candidate_order: Sequence[str],
    evaluation_budget: int,
    *,
    seed: int,
) -> dict[str, Any]:
    """Replay deterministic Random Search using complete-landscape lookups."""

    if evaluation_budget < 1:
        raise ValueError("evaluation_budget must include the empty baseline")
    candidates = list(enumerate_subsets(candidate_order))
    candidates.remove(())
    random.Random(seed).shuffle(candidates)
    evaluated = [()]
    evaluated.extend(candidates[: max(0, evaluation_budget - 1)])
    best = max(evaluated, key=lambda membership: (improvements[membership], -len(membership)))
    return _trace_result(
        method=f"random-search-oracle-assisted-replay-seed-{seed}",
        budget=evaluation_budget,
        evaluated=evaluated,
        best=best,
        improvements=improvements,
        status="budget-censored" if len(evaluated) == evaluation_budget else "complete",
    )


def structural_interaction_summary(
    interaction_records: Iterable[Mapping[str, Any]],
    candidate_metadata: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Group observed interactions by descriptive candidate-structure features."""

    groups: dict[str, list[float]] = defaultdict(list)
    for record in interaction_records:
        first, second = record["candidate_ids"]
        left = candidate_metadata[first]
        right = candidate_metadata[second]
        left_columns = set(left.get("column_names", ()))
        right_columns = set(right.get("column_names", ()))
        left_queries = set(left.get("query_ids", ()))
        right_queries = set(right.get("query_ids", ()))
        column_union = left_columns | right_columns
        query_union = left_queries | right_queries
        column_jaccard = (
            len(left_columns & right_columns) / len(column_union) if column_union else 0.0
        )
        query_jaccard = len(left_queries & right_queries) / len(query_union) if query_union else 0.0
        features = {
            "same_kind": f"{left.get('kind')}=={right.get('kind')}",
            "shared_columns": str(bool(left_columns & right_columns)),
            "query_incidence_overlap": "none"
            if query_jaccard == 0
            else ("partial" if query_jaccard < 1 else "identical"),
            "column_jaccard": f"{column_jaccard:.12g}",
            "query_jaccard": f"{query_jaccard:.12g}",
        }
        for name, value in features.items():
            groups[f"{name}={value}"].append(float(record["interaction"]))
    return {
        key: {
            "record_count": len(values),
            "mean_interaction": sum(values) / len(values),
            "mean_absolute_interaction": sum(abs(value) for value in values) / len(values),
        }
        for key, values in sorted(groups.items())
    }


def summarize_counts(observations: Iterable[ConfigurationObservation]) -> dict[str, int]:
    """Summarize configuration and planner-call counts without inferring missing work."""

    records = tuple(observations)
    return {
        "observation_count": len(records),
        "complete_count": sum(item.status == "complete" for item in records),
        "failed_count": sum(item.status == "failed" for item in records),
        "oracle_invocation_count": sum(item.oracle_invocations for item in records),
        "planner_query_count": sum(item.planner_query_count for item in records),
    }
