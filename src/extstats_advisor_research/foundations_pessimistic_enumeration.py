"""Exact enumeration for the pessimistic maximal-matching model.

This module is deliberately independent of PostgreSQL and Advisor.  It is a
correctness-first reference for the finite theoretical model: local
pessimistic loss tables are built by enumerating *maximal* matchings, and all
budget-feasible global creation sets are then enumerated exhaustively.

The canonical order used here is ascending candidate ID.  It is an output and
tie-breaking convention only; it is not a PostgreSQL statistic creation or
activation-order policy.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

OBJECTIVE_TOLERANCE = 1e-12


class PessimisticModelError(ValueError):
    """Raised when an explicit pessimistic-model input is invalid."""


@dataclass(frozen=True, slots=True)
class CandidateModelSpec:
    """One two-column candidate edge and its explicit storage cost."""

    candidate_id: str
    columns: tuple[str, str]
    cost: float

    def __post_init__(self) -> None:
        if not isinstance(self.candidate_id, str) or not self.candidate_id:
            raise PessimisticModelError("candidate_id must be non-empty")
        if len(self.columns) != 2 or any(
            not isinstance(column, str) or not column for column in self.columns
        ):
            raise PessimisticModelError("every candidate must have two non-empty columns")
        if self.columns[0] == self.columns[1]:
            raise PessimisticModelError("candidate columns must be distinct")
        if not math.isfinite(self.cost) or self.cost <= 0:
            raise PessimisticModelError("candidate costs must be finite and positive")


@dataclass(frozen=True, slots=True)
class QueryModel:
    """One query's loss coefficients and structurally applicable edges."""

    query_id: str
    weight: float
    alpha: float
    applicable_candidate_ids: tuple[str, ...]
    corrections: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.query_id, str) or not self.query_id:
            raise PessimisticModelError("query_id must be non-empty")
        if not math.isfinite(self.weight) or self.weight <= 0:
            raise PessimisticModelError("query weights must be finite and positive")
        if not math.isfinite(self.alpha):
            raise PessimisticModelError("alpha must be finite")
        applicable = tuple(self.applicable_candidate_ids)
        if len(applicable) != len(set(applicable)):
            raise PessimisticModelError("query applicability contains duplicate candidates")
        correction_ids = tuple(candidate_id for candidate_id, _ in self.corrections)
        if set(correction_ids) != set(applicable) or len(correction_ids) != len(applicable):
            raise PessimisticModelError(
                "each applicable candidate must have exactly one correction"
            )
        if any(not math.isfinite(value) for _, value in self.corrections):
            raise PessimisticModelError("corrections must be finite")

    @property
    def correction_map(self) -> dict[str, float]:
        return dict(self.corrections)


@dataclass(frozen=True, slots=True)
class PessimisticInstance:
    """Validated finite input for exact pessimistic enumeration."""

    candidates: tuple[CandidateModelSpec, ...]
    queries: tuple[QueryModel, ...]
    budget: float

    def __post_init__(self) -> None:
        candidate_ids = tuple(candidate.candidate_id for candidate in self.candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise PessimisticModelError("candidate IDs must be unique")
        query_ids = tuple(query.query_id for query in self.queries)
        if len(query_ids) != len(set(query_ids)):
            raise PessimisticModelError("query IDs must be unique")
        if not math.isfinite(self.budget) or self.budget < 0:
            raise PessimisticModelError("budget must be finite and nonnegative")
        candidate_id_set = set(candidate_ids)
        for query in self.queries:
            if not set(query.applicable_candidate_ids) <= candidate_id_set:
                raise PessimisticModelError(
                    f"query {query.query_id} refers to an unknown candidate"
                )

    @property
    def canonical_candidate_ids(self) -> tuple[str, ...]:
        return tuple(sorted(candidate.candidate_id for candidate in self.candidates))

    @property
    def candidate_by_id(self) -> dict[str, CandidateModelSpec]:
        return {candidate.candidate_id: candidate for candidate in self.candidates}


@dataclass(frozen=True, slots=True)
class LocalLossEntry:
    loss: float
    worst_matchings: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class EnumerationStats:
    global_subsets_checked: int
    budget_feasible_subsets: int
    query_local_subsets_evaluated: int
    maximal_matchings_enumerated: int
    maximum_local_table_size: int
    elapsed_ns: int


@dataclass(frozen=True, slots=True)
class PessimisticEnumerationResult:
    method: str
    objective: float
    representative_membership: tuple[str, ...]
    optimal_memberships: tuple[tuple[str, ...], ...] | None
    optimal_set_count: int
    representative_storage_cost: float
    per_query_losses: tuple[tuple[str, float], ...]
    worst_matchings: tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]
    stats: EnumerationStats

    @property
    def per_query_loss_map(self) -> dict[str, float]:
        return dict(self.per_query_losses)

    @property
    def worst_matching_map(self) -> dict[str, tuple[tuple[str, ...], ...]]:
        return dict(self.worst_matchings)


def _column_sets(
    candidate_ids: Iterable[str], columns_by_candidate: Mapping[str, Sequence[str]]
) -> dict[str, frozenset[str]]:
    result: dict[str, frozenset[str]] = {}
    for candidate_id in sorted(candidate_ids):
        try:
            columns = tuple(columns_by_candidate[candidate_id])
        except KeyError as error:
            raise PessimisticModelError(f"missing columns for candidate {candidate_id}") from error
        if (
            len(columns) != 2
            or len(set(columns)) != 2
            or any(not isinstance(column, str) or not column for column in columns)
        ):
            raise PessimisticModelError(
                f"candidate {candidate_id} must have two distinct non-empty columns"
            )
        result[candidate_id] = frozenset(columns)
    return result


def _matching_conflict(left: str, right: str, columns: Mapping[str, frozenset[str]]) -> bool:
    return bool(columns[left] & columns[right])


def enumerate_maximal_matchings(
    candidate_ids: Iterable[str], columns_by_candidate: Mapping[str, Sequence[str]]
) -> tuple[tuple[str, ...], ...]:
    """Enumerate every maximal, not merely maximum-cardinality, matching."""

    ids = tuple(sorted(candidate_ids))
    columns = _column_sets(ids, columns_by_candidate)
    matchings: list[tuple[str, ...]] = []
    for mask in range(1 << len(ids)):
        selected = tuple(ids[index] for index in range(len(ids)) if mask & (1 << index))
        if any(
            _matching_conflict(left, right, columns)
            for index, left in enumerate(selected)
            for right in selected[index + 1 :]
        ):
            continue
        omitted = (candidate_id for candidate_id in ids if candidate_id not in selected)
        if any(
            not any(_matching_conflict(candidate_id, chosen, columns) for chosen in selected)
            for candidate_id in omitted
        ):
            continue
        matchings.append(selected)
    return tuple(sorted(matchings))


def _local_loss(
    query: QueryModel,
    local_membership: tuple[str, ...],
    columns_by_candidate: Mapping[str, frozenset[str]],
) -> tuple[LocalLossEntry, int]:
    corrections = query.correction_map
    matchings = enumerate_maximal_matchings(local_membership, columns_by_candidate)
    losses = tuple(
        abs(query.alpha + sum(corrections[candidate_id] for candidate_id in matching))
        for matching in matchings
    )
    loss = max(losses)
    witnesses = tuple(
        matching for matching, value in zip(matchings, losses, strict=True) if value == loss
    )
    return LocalLossEntry(loss, witnesses), len(matchings)


def build_local_loss_tables(
    instance: PessimisticInstance,
) -> tuple[dict[str, dict[tuple[str, ...], LocalLossEntry]], int, int, int]:
    """Return local tables and (subset, matching, maximum-table) counts."""

    columns = {
        candidate_id: frozenset(spec.columns)
        for candidate_id, spec in instance.candidate_by_id.items()
    }
    tables: dict[str, dict[tuple[str, ...], LocalLossEntry]] = {}
    local_subset_count = 0
    matching_count = 0
    maximum_table_size = 0
    for query in instance.queries:
        local_ids = tuple(sorted(query.applicable_candidate_ids))
        table: dict[tuple[str, ...], LocalLossEntry] = {}
        for mask in range(1 << len(local_ids)):
            subset = tuple(
                candidate_id for index, candidate_id in enumerate(local_ids) if mask & (1 << index)
            )
            entry, count = _local_loss(query, subset, columns)
            table[subset] = entry
            local_subset_count += 1
            matching_count += count
        tables[query.query_id] = table
        maximum_table_size = max(maximum_table_size, len(table))
    return tables, local_subset_count, matching_count, maximum_table_size


def build_pessimistic_instance(
    *,
    candidate_ids: Sequence[str],
    candidate_columns: Mapping[str, Sequence[str]],
    candidate_costs: Mapping[str, float],
    query_ids: Sequence[str],
    empty_estimates: Mapping[str, float],
    singleton_estimates: Mapping[str, Mapping[str, float]],
    truths: Mapping[str, float],
    weights: Mapping[str, float],
    applicable_candidates: Mapping[str, Iterable[str]],
    budget: float,
) -> PessimisticInstance:
    """Build model coefficients from strictly positive raw estimates.

    The true-cardinality floor is applied only in ``alpha``.  Empty and
    singleton Planner estimates remain raw and must be finite and strictly
    positive, so missing factors cannot be silently interpreted as zero.
    """

    ids = tuple(candidate_ids)
    if len(ids) != len(set(ids)) or not ids:
        raise PessimisticModelError("candidate_ids must be non-empty and unique")
    _column_sets(ids, candidate_columns)
    candidates: list[CandidateModelSpec] = []
    for candidate_id in ids:
        if candidate_id not in candidate_costs:
            raise PessimisticModelError(f"missing cost for candidate {candidate_id}")
        candidates.append(
            CandidateModelSpec(
                candidate_id,
                tuple(candidate_columns[candidate_id]),
                float(candidate_costs[candidate_id]),
            )
        )
    query_models: list[QueryModel] = []
    for query_id in query_ids:
        if query_id not in empty_estimates:
            raise PessimisticModelError(f"missing empty estimate for query {query_id}")
        if query_id not in truths or query_id not in weights:
            raise PessimisticModelError(f"missing truth or weight for query {query_id}")
        empty = float(empty_estimates[query_id])
        truth = float(truths[query_id])
        weight = float(weights[query_id])
        if not math.isfinite(empty) or empty <= 0:
            raise PessimisticModelError("raw estimates must be finite and strictly positive")
        if not math.isfinite(truth) or truth < 0:
            raise PessimisticModelError("truths must be finite and nonnegative")
        applicable = tuple(sorted(set(applicable_candidates.get(query_id, ()))))
        correction_items: list[tuple[str, float]] = []
        for candidate_id in applicable:
            if candidate_id not in ids:
                raise PessimisticModelError(
                    f"query {query_id} refers to unknown candidate {candidate_id}"
                )
            if candidate_id not in singleton_estimates:
                raise PessimisticModelError(f"missing singleton estimates for {candidate_id}")
            if query_id not in singleton_estimates[candidate_id]:
                raise PessimisticModelError(
                    f"missing singleton estimate for {candidate_id}/{query_id}"
                )
            singleton = float(singleton_estimates[candidate_id][query_id])
            if not math.isfinite(singleton) or singleton <= 0:
                raise PessimisticModelError(
                    "raw singleton estimates must be finite and strictly positive"
                )
            correction_items.append((candidate_id, math.log(singleton) - math.log(empty)))
        query_models.append(
            QueryModel(
                query_id,
                weight,
                math.log(empty) - math.log(max(1.0, truth)),
                applicable,
                tuple(correction_items),
            )
        )
    return PessimisticInstance(tuple(candidates), tuple(query_models), float(budget))


def _global_subsets(instance: PessimisticInstance):
    ids = instance.canonical_candidate_ids
    costs = {candidate.candidate_id: candidate.cost for candidate in instance.candidates}
    for mask in range(1 << len(ids)):
        membership = tuple(
            candidate_id for index, candidate_id in enumerate(ids) if mask & (1 << index)
        )
        cost = sum(costs[candidate_id] for candidate_id in membership)
        yield membership, cost


def _record_result(
    *,
    method: str,
    instance: PessimisticInstance,
    evaluations: Iterable[tuple[tuple[str, ...], float, dict[str, LocalLossEntry]]],
    global_subsets_checked: int,
    budget_feasible_subsets: int,
    query_local_subsets_evaluated: int,
    maximal_matchings_enumerated: int,
    maximum_local_table_size: int,
    started_ns: int,
    max_tied_sets: int,
) -> PessimisticEnumerationResult:
    best_objective = math.inf
    representative: tuple[str, ...] | None = None
    representative_entries: dict[str, LocalLossEntry] = {}
    representative_cost = math.inf
    stored: list[tuple[str, ...]] = []
    optimal_set_count = 0
    for membership, objective, entries in evaluations:
        if objective < best_objective - OBJECTIVE_TOLERANCE:
            best_objective = objective
            representative = membership
            representative_entries = entries
            representative_cost = sum(
                candidate.cost
                for candidate in instance.candidates
                if candidate.candidate_id in membership
            )
            stored = [membership]
            optimal_set_count = 1
        elif abs(objective - best_objective) <= OBJECTIVE_TOLERANCE:
            optimal_set_count += 1
            if len(stored) <= max_tied_sets:
                stored.append(membership)
                if len(stored) > max_tied_sets:
                    stored = []
    if representative is None:
        raise PessimisticModelError("no budget-feasible creation set exists")
    optimal_memberships = tuple(stored) if optimal_set_count <= max_tied_sets else None
    per_query_losses = tuple(
        (query.query_id, representative_entries[query.query_id].loss) for query in instance.queries
    )
    worst_matchings = tuple(
        (query.query_id, representative_entries[query.query_id].worst_matchings)
        for query in instance.queries
    )
    stats = EnumerationStats(
        global_subsets_checked,
        budget_feasible_subsets,
        query_local_subsets_evaluated,
        maximal_matchings_enumerated,
        maximum_local_table_size,
        time.perf_counter_ns() - started_ns,
    )
    return PessimisticEnumerationResult(
        method,
        best_objective,
        representative,
        optimal_memberships,
        optimal_set_count,
        representative_cost,
        per_query_losses,
        worst_matchings,
        stats,
    )


def solve_direct(
    instance: PessimisticInstance, *, max_tied_sets: int = 10_000
) -> PessimisticEnumerationResult:
    """Algorithm 0: direct global enumeration and local matching enumeration."""

    if max_tied_sets < 1:
        raise PessimisticModelError("max_tied_sets must be positive")
    started_ns = time.perf_counter_ns()
    columns = {
        candidate.candidate_id: frozenset(candidate.columns) for candidate in instance.candidates
    }
    evaluations = []
    global_checked = feasible = matching_count = 0
    for membership, cost in _global_subsets(instance):
        global_checked += 1
        if cost > instance.budget:
            continue
        feasible += 1
        entries: dict[str, LocalLossEntry] = {}
        objective = 0.0
        membership_set = set(membership)
        for query in instance.queries:
            local = tuple(sorted(membership_set & set(query.applicable_candidate_ids)))
            entry, count = _local_loss(query, local, columns)
            matching_count += count
            entries[query.query_id] = entry
            objective += query.weight * entry.loss
        evaluations.append((membership, objective, entries))
    return _record_result(
        method="direct-exhaustive",
        instance=instance,
        evaluations=evaluations,
        global_subsets_checked=global_checked,
        budget_feasible_subsets=feasible,
        query_local_subsets_evaluated=0,
        maximal_matchings_enumerated=matching_count,
        maximum_local_table_size=0,
        started_ns=started_ns,
        max_tied_sets=max_tied_sets,
    )


def solve_local_tables(
    instance: PessimisticInstance, *, max_tied_sets: int = 10_000
) -> PessimisticEnumerationResult:
    """Algorithm 1: local loss tables followed by global enumeration."""

    if max_tied_sets < 1:
        raise PessimisticModelError("max_tied_sets must be positive")
    started_ns = time.perf_counter_ns()
    tables, local_count, matching_count, maximum_table_size = build_local_loss_tables(instance)
    evaluations = []
    global_checked = feasible = 0
    for membership, cost in _global_subsets(instance):
        global_checked += 1
        if cost > instance.budget:
            continue
        feasible += 1
        membership_set = set(membership)
        entries: dict[str, LocalLossEntry] = {}
        objective = 0.0
        for query in instance.queries:
            local = tuple(sorted(membership_set & set(query.applicable_candidate_ids)))
            entry = tables[query.query_id][local]
            entries[query.query_id] = entry
            objective += query.weight * entry.loss
        evaluations.append((membership, objective, entries))
    return _record_result(
        method="local-table-exhaustive",
        instance=instance,
        evaluations=evaluations,
        global_subsets_checked=global_checked,
        budget_feasible_subsets=feasible,
        query_local_subsets_evaluated=local_count,
        maximal_matchings_enumerated=matching_count,
        maximum_local_table_size=maximum_table_size,
        started_ns=started_ns,
        max_tied_sets=max_tied_sets,
    )
