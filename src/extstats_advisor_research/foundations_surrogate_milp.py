"""Bounded singleton-factor surrogate prototype for disjoint statistics.

The module deliberately keeps the optimization layer separate from Advisor and
PostgreSQL.  Candidate selection uses only sealed metadata and incidence;
singleton factors are recovered from Oracle observations; the MILP is solved
only when an installed exact backend is available.  The small enumerator is a
reference implementation, not a replacement for a MILP solve.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .foundations_landscape import FoundationsLandscapeInput
from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
    _validate_bound_inputs,
)
from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

SURROGATE_TOLERANCE = 1e-10
DEFAULT_BUDGETS = (0, 1, 2, 3, 4)
MODEL_WARNING_THRESHOLD = 1e-3


class SurrogatePrototypeError(ValueError):
    """Raised when the bounded surrogate binding is invalid."""


class SurrogateSolverUnavailable(RuntimeError):
    """Raised when no supported exact MILP backend is installed."""


@dataclass(frozen=True, slots=True)
class SurrogateCoefficients:
    candidate_ids: tuple[str, ...]
    query_ids: tuple[str, ...]
    weights: tuple[float, ...]
    empty_estimates: tuple[float, ...]
    truths: tuple[float, ...]
    alpha: tuple[float, ...]
    beta: tuple[tuple[float, ...], ...]

    def __post_init__(self) -> None:
        n = len(self.candidate_ids)
        q = len(self.query_ids)
        if n == 0 or q == 0 or len(set(self.candidate_ids)) != n:
            raise SurrogatePrototypeError("coefficients require unique candidates and queries")
        if len(self.weights) != q or len(self.empty_estimates) != q or len(self.truths) != q:
            raise SurrogatePrototypeError("coefficient query vectors have inconsistent lengths")
        if len(self.alpha) != q or len(self.beta) != n:
            raise SurrogatePrototypeError("coefficient matrix has inconsistent dimensions")
        if any(len(row) != q for row in self.beta):
            raise SurrogatePrototypeError("coefficient matrix rows have inconsistent lengths")
        if (
            any(not math.isfinite(value) or value < 0 for value in self.weights)
            or sum(self.weights) <= 0
        ):
            raise SurrogatePrototypeError("weights must be finite, nonnegative, and nonzero")
        if any(not math.isfinite(value) or value < 0 for value in self.truths):
            raise SurrogatePrototypeError("truths must be finite and nonnegative")

    @property
    def total_weight(self) -> float:
        return sum(self.weights)

    def log_prediction(self, mask: int) -> tuple[float, ...]:
        return tuple(
            raw_log - math.log(max(1.0, truth))
            for raw_log, truth in zip(self.log_raw_prediction(mask), self.truths, strict=True)
        )

    def log_raw_prediction(self, mask: int) -> tuple[float, ...]:
        _validate_mask(mask, len(self.candidate_ids))
        return tuple(
            math.log(self.empty_estimates[query_index])
            + sum(
                self.beta[candidate_index][query_index]
                for candidate_index in range(len(self.candidate_ids))
                if mask & (1 << candidate_index)
            )
            for query_index in range(len(self.query_ids))
        )

    def predicted_estimates(self, mask: int) -> dict[str, float]:
        return {
            query_id: math.exp(raw_log)
            for query_id, raw_log in zip(self.query_ids, self.log_raw_prediction(mask), strict=True)
        }

    def objective(self, mask: int) -> float:
        return (
            sum(
                weight * abs(value)
                for weight, value in zip(self.weights, self.log_prediction(mask), strict=True)
            )
            / self.total_weight
        )


@dataclass(frozen=True, slots=True)
class SurrogateSolveResult:
    budget: int
    mask: int
    objective: float
    status: str
    solve_time_ns: int
    mip_gap: float | None = None
    best_bound: float | None = None
    certified_optimal: bool = False
    method: str = "milp"


@dataclass(frozen=True, slots=True)
class GreedyStep:
    step: int
    current_mask: int
    selected_candidate_id: str
    marginal_improvement: float
    resulting_mask: int
    resulting_objective: float


@dataclass(frozen=True, slots=True)
class SurrogateGreedyResult:
    budget: int
    mask: int
    objective: float
    stopped_early: bool
    steps: tuple[GreedyStep, ...]


@dataclass(frozen=True, slots=True)
class SurrogateMILPFormulation:
    """Solver-neutral coefficient form for the absolute-log-error MILP."""

    variable_names: tuple[str, ...]
    objective: tuple[float, ...]
    integrality: tuple[int, ...]
    lower_bounds: tuple[float, ...]
    upper_bounds: tuple[float, ...]
    constraints: tuple[tuple[tuple[float, ...], float, float], ...]


def _validate_mask(mask: int, candidate_count: int) -> None:
    if isinstance(mask, bool) or not isinstance(mask, int) or mask < 0:
        raise SurrogatePrototypeError("mask must be a nonnegative integer")
    if mask >= 1 << candidate_count:
        raise SurrogatePrototypeError("mask contains an unknown candidate bit")


def _candidate_columns(candidate: Any) -> frozenset[tuple[str, int]]:
    relation = getattr(candidate, "relation_id", None)
    ordinals = tuple(getattr(candidate, "column_ordinals", ()))
    if not isinstance(relation, str) or not relation or len(ordinals) != 2:
        raise SurrogatePrototypeError("candidate lacks a valid relation/column identity")
    return frozenset((relation, ordinal) for ordinal in ordinals)


def select_disjoint_surrogate_candidates(
    universe: Any, *, max_candidates: int = 8
) -> tuple[Any, ...]:
    """Select a deterministic disjoint packing from sealed metadata only.

    Candidates are ranked by positive supported incidence, then incidence
    weight, kind, static precedence, and opaque ID.  No Oracle result or
    historical singleton profile enters this rule.
    """

    if (
        isinstance(max_candidates, bool)
        or not isinstance(max_candidates, int)
        or max_candidates < 1
    ):
        raise SurrogatePrototypeError("max_candidates must be a positive integer")
    profiles = {
        profile.query_id: float(profile.weight)
        for profile in universe.query_profiles
        if profile.analysis_status == "supported" and profile.weight > 0
    }
    incidence: dict[str, set[str]] = {
        candidate.candidate_id: set() for candidate in universe.candidates
    }
    for item in universe.incidence:
        if item.query_id in profiles and item.candidate_id in incidence:
            incidence[item.candidate_id].add(item.query_id)
    ranked = sorted(
        universe.candidates,
        key=lambda candidate: (
            -len(incidence[candidate.candidate_id]),
            -sum(profiles[query_id] for query_id in incidence[candidate.candidate_id]),
            candidate.kind,
            candidate.static_precedence_rank,
            candidate.candidate_id,
        ),
    )
    selected: list[Any] = []
    occupied: set[tuple[str, int]] = set()
    candidates_by_kind: dict[str, list[Any]] = {}
    for candidate in ranked:
        candidates_by_kind.setdefault(candidate.kind, []).append(candidate)
    kind_order = sorted(
        candidates_by_kind,
        key=lambda kind: (
            -len(incidence[candidates_by_kind[kind][0].candidate_id]),
            kind,
        ),
    )
    # Seed one candidate from each available kind when the columns permit it;
    # then fill the remaining disjoint slots by the same frozen ranking.
    for kind in kind_order:
        for candidate in candidates_by_kind[kind]:
            columns = _candidate_columns(candidate)
            if not columns & occupied:
                selected.append(candidate)
                occupied.update(columns)
                break
    for candidate in ranked:
        if len(selected) == max_candidates:
            break
        if candidate in selected:
            continue
        columns = _candidate_columns(candidate)
        if columns & occupied:
            continue
        selected.append(candidate)
        occupied.update(columns)
    if not selected:
        raise SurrogatePrototypeError("no disjoint candidate is available")
    return tuple(selected)


def recover_surrogate_coefficients(
    *,
    candidate_ids: Sequence[str],
    query_ids: Sequence[str],
    empty_estimates: Mapping[str, float],
    singleton_estimates: Mapping[str, Mapping[str, float]],
    truths: Mapping[str, float],
    weights: Mapping[str, float],
) -> SurrogateCoefficients:
    """Recover raw factors with a target-only floor for the log-loss objective.

    Planner estimates and singleton factors are never floored.  Only the truth
    denominator is mapped to ``max(1, truth)``; this is the declared convention
    represented exactly by the linear absolute-log MILP.
    """

    candidates = tuple(candidate_ids)
    queries = tuple(query_ids)
    if len(candidates) != len(set(candidates)) or len(queries) != len(set(queries)):
        raise SurrogatePrototypeError("candidate and query IDs must be unique")
    missing = {
        "empty": set(queries) - set(empty_estimates),
        "truth": set(queries) - set(truths),
        "weights": set(queries) - set(weights),
        "singletons": set(candidates) - set(singleton_estimates),
    }
    if any(missing.values()):
        raise SurrogatePrototypeError(f"incomplete coefficient inputs: {missing!r}")

    def positive(value: Any, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SurrogatePrototypeError(f"{label} is not numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise SurrogatePrototypeError(f"{label} must be finite and strictly positive")
        return numeric

    empty = tuple(positive(empty_estimates[q], f"empty[{q}]") for q in queries)

    def nonnegative(value: Any, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SurrogatePrototypeError(f"{label} is not numeric")
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0:
            raise SurrogatePrototypeError(f"{label} must be finite and nonnegative")
        return numeric

    true_values = tuple(nonnegative(truths[q], f"truth[{q}]") for q in queries)
    query_weights = tuple(positive(weights[q], f"weight[{q}]") for q in queries)
    alpha = tuple(math.log(e / max(1.0, t)) for e, t in zip(empty, true_values, strict=True))
    beta = tuple(
        tuple(
            math.log(
                positive(singleton_estimates[candidate_id][q], f"singleton[{candidate_id}][{q}]")
                / e
            )
            for q, e in zip(queries, empty, strict=True)
        )
        for candidate_id in candidates
    )
    return SurrogateCoefficients(
        candidates, queries, query_weights, empty, true_values, alpha, beta
    )


def build_surrogate_milp_formulation(
    coefficients: SurrogateCoefficients, *, budget: int, costs: Sequence[float] | None = None
) -> SurrogateMILPFormulation:
    """Build the exact linear constraints for the absolute-log-error model."""

    n = len(coefficients.candidate_ids)
    q = len(coefficients.query_ids)
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        raise SurrogatePrototypeError("budget must be a nonnegative integer")
    candidate_costs = tuple(float(cost) for cost in (costs or (1.0,) * n))
    if len(candidate_costs) != n or any(
        not math.isfinite(cost) or cost < 0 for cost in candidate_costs
    ):
        raise SurrogatePrototypeError("costs must be finite, nonnegative, and match candidates")
    names = tuple(f"x_{candidate_id}" for candidate_id in coefficients.candidate_ids) + tuple(
        f"t_{query_id}" for query_id in coefficients.query_ids
    )
    objective = (0.0,) * n + tuple(
        weight / coefficients.total_weight for weight in coefficients.weights
    )
    integrality = (1,) * n + (0,) * q
    lower = (0.0,) * (n + q)
    upper = (1.0,) * n + (math.inf,) * q
    constraints: list[tuple[tuple[float, ...], float, float]] = []
    constraints.append((candidate_costs + (0.0,) * q, -math.inf, float(budget)))
    for query_index in range(q):
        beta_column = tuple(
            coefficients.beta[candidate_index][query_index] for candidate_index in range(n)
        )
        t = tuple(-1.0 if index == query_index else 0.0 for index in range(q))
        constraints.append((beta_column + t, -math.inf, -coefficients.alpha[query_index]))
        constraints.append(
            (tuple(-value for value in beta_column) + t, -math.inf, coefficients.alpha[query_index])
        )
    return SurrogateMILPFormulation(names, objective, integrality, lower, upper, tuple(constraints))


def enumerate_surrogate_optimum(
    coefficients: SurrogateCoefficients,
    *,
    budget: int,
    costs: Sequence[float] | None = None,
) -> SurrogateSolveResult:
    """Enumerate the small surrogate exactly as an independent reference."""

    n = len(coefficients.candidate_ids)
    candidate_costs = tuple(float(cost) for cost in (costs or (1.0,) * n))
    if len(candidate_costs) != n:
        raise SurrogatePrototypeError("costs must match candidates")
    started = time.perf_counter_ns()
    best_mask: int | None = None
    best_objective = math.inf
    for mask in range(1 << n):
        if sum(candidate_costs[index] for index in range(n) if mask & (1 << index)) > budget:
            continue
        objective = coefficients.objective(mask)
        if objective < best_objective - SURROGATE_TOLERANCE or (
            abs(objective - best_objective) <= SURROGATE_TOLERANCE
            and (best_mask is None or mask < best_mask)
        ):
            best_mask, best_objective = mask, objective
    if best_mask is None:
        raise SurrogatePrototypeError("no feasible surrogate configuration")
    return SurrogateSolveResult(
        budget,
        best_mask,
        best_objective,
        "optimal-enumeration-reference",
        time.perf_counter_ns() - started,
        certified_optimal=True,
        method="enumeration-reference",
    )


def milp_backend_version() -> str | None:
    try:
        import scipy
        from scipy.optimize import milp as _milp  # noqa: F401
    except ImportError:
        return None
    return str(scipy.__version__)


def solve_surrogate_milp(
    coefficients: SurrogateCoefficients,
    *,
    budget: int,
    costs: Sequence[float] | None = None,
) -> SurrogateSolveResult:
    """Solve with installed SciPy/HiGHS, never silently falling back."""

    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError as exc:
        raise SurrogateSolverUnavailable(
            "SciPy/HiGHS is required for the MILP prototype and is not installed"
        ) from exc
    formulation = build_surrogate_milp_formulation(coefficients, budget=budget, costs=costs)
    matrix = np.asarray([row for row, _lower, _upper in formulation.constraints], dtype=float)
    lower = np.asarray([lower for _row, lower, _upper in formulation.constraints], dtype=float)
    upper = np.asarray([upper for _row, _lower, upper in formulation.constraints], dtype=float)
    started = time.perf_counter_ns()
    result = milp(
        c=np.asarray(formulation.objective, dtype=float),
        integrality=np.asarray(formulation.integrality, dtype=int),
        bounds=Bounds(np.asarray(formulation.lower_bounds), np.asarray(formulation.upper_bounds)),
        constraints=LinearConstraint(matrix, lower, upper),
    )
    if not result.success or result.x is None:
        raise SurrogatePrototypeError(
            f"MILP solver did not find an optimal solution: {result.message}"
        )
    n = len(coefficients.candidate_ids)
    mask = sum((1 << index) for index, value in enumerate(result.x[:n]) if value >= 0.5)
    return SurrogateSolveResult(
        budget,
        mask,
        coefficients.objective(mask),
        str(result.message),
        time.perf_counter_ns() - started,
        getattr(result, "mip_gap", None),
        getattr(result, "mip_dual_bound", None),
        True,
        "scipy-milp-highs",
    )


def surrogate_greedy(
    coefficients: SurrogateCoefficients,
    *,
    budget: int,
    tolerance: float = SURROGATE_TOLERANCE,
) -> SurrogateGreedyResult:
    """Run deterministic early-stop Greedy on the surrogate objective."""

    if tolerance < 0 or not math.isfinite(tolerance):
        raise SurrogatePrototypeError("tolerance must be finite and nonnegative")
    n = len(coefficients.candidate_ids)
    current = 0
    steps: list[GreedyStep] = []
    stopped_early = False
    for step in range(min(budget, n)):
        current_objective = coefficients.objective(current)
        candidates = [index for index in range(n) if not current & (1 << index)]
        choices = [
            (current_objective - coefficients.objective(current | (1 << index)), index)
            for index in candidates
        ]
        best_gain, best_index = max(choices, key=lambda item: (item[0], -item[1]))
        if best_gain <= tolerance:
            stopped_early = True
            break
        next_mask = current | (1 << best_index)
        steps.append(
            GreedyStep(
                step + 1,
                current,
                coefficients.candidate_ids[best_index],
                best_gain,
                next_mask,
                coefficients.objective(next_mask),
            )
        )
        current = next_mask
    return SurrogateGreedyResult(
        budget, current, coefficients.objective(current), stopped_early, tuple(steps)
    )


def qerror(estimate: float, truth: float) -> float:
    if not math.isfinite(estimate) or not math.isfinite(truth):
        raise SurrogatePrototypeError("q-error inputs must be finite")
    return max(estimate, truth, 1.0) / min(max(estimate, 1.0), max(truth, 1.0))


def weighted_log_error(
    estimates: Mapping[str, float], truths: Mapping[str, float], weights: Mapping[str, float]
) -> float:
    total = sum(float(weights[q]) for q in weights)
    if total <= 0:
        raise SurrogatePrototypeError("weighted objective requires positive total weight")
    return (
        sum(
            float(weights[q]) * abs(math.log(float(estimates[q]) / max(1.0, float(truths[q]))))
            for q in weights
        )
        / total
    )


def weighted_qerror(
    estimates: Mapping[str, float], truths: Mapping[str, float], weights: Mapping[str, float]
) -> float:
    total = sum(float(weights[q]) for q in weights)
    if total <= 0:
        raise SurrogatePrototypeError("weighted objective requires positive total weight")
    return (
        sum(float(weights[q]) * qerror(float(estimates[q]), float(truths[q])) for q in weights)
        / total
    )


def prediction_residuals(
    predicted: Mapping[str, float], actual: Mapping[str, float]
) -> tuple[float, ...]:
    return tuple(abs(math.log(float(actual[q]) / float(predicted[q]))) for q in predicted)


def write_surrogate_result_json(payload: Mapping[str, Any], destination: Path) -> None:
    """Publish final observations without overwriting an existing result."""

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def run_foundations_surrogate_prototype(
    inputs: FoundationsLandscapeInput,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
    budgets: Sequence[int] = DEFAULT_BUDGETS,
) -> Mapping[str, Any]:
    """Run one bounded real prototype, gated on a genuine MILP backend.

    The backend gate occurs before database creation.  If the solver is absent,
    this function raises ``SurrogateSolverUnavailable`` and performs no live
    experiment; callers may still use the pure functions and exact enumerator.
    """

    if milp_backend_version() is None:
        raise SurrogateSolverUnavailable(
            "no installed exact MILP backend; live prototype is intentionally blocked"
        )
    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    snapshot, universe, ground_truth = _validate_bound_inputs(inputs, services)
    selected = select_disjoint_surrogate_candidates(universe)
    candidate_ids = tuple(candidate.candidate_id for candidate in selected)
    query_ids = tuple(inputs.query_ids)
    profile_weights = {
        profile.query_id: float(profile.weight) for profile in universe.query_profiles
    }
    truths = {
        truth.query_id: float(truth.cardinality)
        for truth in ground_truth.truths
        if truth.query_id in query_ids
    }
    missing_truth = set(query_ids) - set(truths)
    if missing_truth:
        raise SurrogatePrototypeError(f"ground truth is missing queries: {sorted(missing_truth)}")
    weights = {query_id: profile_weights[query_id] for query_id in query_ids}
    spec = ExperimentDatabaseSpec(
        admin_dsn=inputs.admin_dsn,
        name_prefix="exp_foundations_surrogate",
        sample_rows=inputs.sample_rows,
    )
    lifecycle = lifecycle_factory(spec)
    result_payload: Mapping[str, Any] | None = None
    with lifecycle as database:
        session: Any | None = None
        oracle: BlackBoxMembershipOracle | None = None
        sandbox_prepared = False
        primary: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        try:
            repository = services.materialize_repository(
                database.database_dsn,
                snapshot,
                universe,
                inputs.statistics_target,
                inputs.native_batch_size,
            )
            services.prepare_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
                inputs.native_batch_size,
            )
            sandbox_prepared = True
            verification = services.verify_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
            )
            session = services.planner_session(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
            )
            session.open()
            oracle = BlackBoxMembershipOracle(
                session=session,
                snapshot=snapshot,
                candidate_universe=universe,
                global_precedence=inputs.global_precedence,
                utility_provider=services.make_utility(snapshot, ground_truth, query_ids),
                native_repository=repository,
                query_ids=query_ids,
                expected_snapshot_digest=inputs.expected_snapshot_digest,
                expected_candidate_universe_digest=inputs.expected_candidate_universe_digest,
            )
            observations: dict[int, OracleEvaluation] = {0: oracle.evaluate_precise(())}
            for index, candidate_id in enumerate(candidate_ids):
                observations[1 << index] = oracle.evaluate_precise((candidate_id,))
            if any(item.diagnostics.status != "complete" for item in observations.values()):
                raise SurrogatePrototypeError("empty or singleton Oracle evaluation failed")
            coefficients = recover_surrogate_coefficients(
                candidate_ids=candidate_ids,
                query_ids=query_ids,
                empty_estimates=observations[0].estimates or {},
                singleton_estimates={
                    candidate_id: observations[1 << index].estimates or {}
                    for index, candidate_id in enumerate(candidate_ids)
                },
                truths=truths,
                weights=weights,
            )
            normalized_budgets = tuple(dict.fromkeys(int(budget) for budget in budgets))
            enumeration_results = tuple(
                enumerate_surrogate_optimum(coefficients, budget=budget)
                for budget in normalized_budgets
            )
            milp_results = tuple(
                solve_surrogate_milp(coefficients, budget=budget) for budget in normalized_budgets
            )
            for enumeration, milp in zip(enumeration_results, milp_results, strict=True):
                if abs(enumeration.objective - milp.objective) > SURROGATE_TOLERANCE:
                    raise SurrogatePrototypeError(
                        "MILP objective disagrees with exact surrogate enumeration for "
                        f"budget {milp.budget}: {milp.objective} != {enumeration.objective}"
                    )
            greedy_results = tuple(
                surrogate_greedy(coefficients, budget=budget) for budget in normalized_budgets
            )
            recommended_masks = {result.mask for result in milp_results}
            recommended_masks.update(result.mask for result in greedy_results)
            for mask in sorted(recommended_masks):
                if mask not in observations:
                    observations[mask] = oracle.evaluate_precise(
                        tuple(
                            candidate_ids[index]
                            for index in range(len(candidate_ids))
                            if mask & (1 << index)
                        )
                    )
            if any(item.diagnostics.status != "complete" for item in observations.values()):
                raise SurrogatePrototypeError("a recommended Oracle verification failed")
            actual = {}
            for mask, observation in sorted(observations.items()):
                actual_estimates = dict(observation.estimates or {})
                predicted = coefficients.predicted_estimates(mask)
                residuals = prediction_residuals(predicted, actual_estimates)
                actual[str(mask)] = {
                    "membership": [
                        candidate_ids[index]
                        for index in range(len(candidate_ids))
                        if mask & (1 << index)
                    ],
                    "activation_membership": list(observation.diagnostics.membership),
                    "estimates": actual_estimates,
                    "formatted_estimates": dict(observation.formatted_estimates or {}),
                    "diagnostics": observation.diagnostics.as_dict(),
                    "predicted_estimates": predicted,
                    "prediction_residuals": dict(zip(query_ids, residuals, strict=True)),
                    "max_prediction_residual": max(residuals),
                    "mean_prediction_residual": sum(residuals) / len(residuals),
                    "warning_count": sum(value > MODEL_WARNING_THRESHOLD for value in residuals),
                    "objective_log_error": weighted_log_error(actual_estimates, truths, weights),
                    "objective_qerror": observation.objective,
                }
            result_payload = {
                "experiment_id": "foundations-disjoint-surrogate-milp-v1",
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
                "database_name": database.database_name,
                "dataset_id": inputs.dataset_id,
                "relation_id": inputs.relation_id,
                "relation_schema": inputs.relation_schema,
                "relation_name": inputs.relation_name,
                "workload_id": inputs.workload_id,
                "sample_rows": inputs.sample_rows,
                "statistics_target": inputs.statistics_target,
                "global_precedence": list(inputs.global_precedence),
                "candidate_ids": list(candidate_ids),
                "query_ids": list(query_ids),
                "candidate_metadata": [candidate.to_dict() for candidate in selected],
                "sandbox_verification": verification,
                "coefficients": {
                    "empty_estimates": dict(
                        zip(query_ids, coefficients.empty_estimates, strict=True)
                    ),
                    "truths": dict(zip(query_ids, coefficients.truths, strict=True)),
                    "alpha": dict(zip(query_ids, coefficients.alpha, strict=True)),
                    "beta": {
                        candidate_id: dict(zip(query_ids, row, strict=True))
                        for candidate_id, row in zip(candidate_ids, coefficients.beta, strict=True)
                    },
                },
                "budgets": list(normalized_budgets),
                "enumeration_results": [asdict(result) for result in enumeration_results],
                "milp_results": [asdict(result) for result in milp_results],
                "greedy_results": [asdict(result) for result in greedy_results],
                "budget_comparisons": [
                    {
                        "budget": budget,
                        "milp": {
                            **asdict(milp_result),
                            "actual": actual[str(milp_result.mask)],
                        },
                        "greedy": {
                            **asdict(greedy_result),
                            "actual": actual[str(greedy_result.mask)],
                        },
                    }
                    for budget, milp_result, greedy_result in zip(
                        normalized_budgets, milp_results, greedy_results, strict=True
                    )
                ],
                "observations": actual,
                "distinct_configuration_evaluations": len(observations),
                "primary_planner_query_count": sum(
                    observation.diagnostics.planner_query_successes
                    for observation in observations.values()
                ),
                "local_milp_solve_count": len(milp_results),
                "local_enumeration_evaluation_count": sum(
                    sum(
                        math.comb(len(candidate_ids), size)
                        for size in range(min(budget, len(candidate_ids)) + 1)
                    )
                    for budget in normalized_budgets
                ),
                "lifecycle_completed": True,
            }
        except BaseException as exc:  # noqa: BLE001 - preserve the experiment failure
            primary = exc
        finally:
            if oracle is not None:
                try:
                    oracle.close()
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            elif session is not None:
                try:
                    session.close()
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            if sandbox_prepared:
                try:
                    services.destroy_sandbox(database.database_dsn)
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            _raise_with_cleanup(primary, cleanup_errors)
    if result_payload is None:  # pragma: no cover - defensive invariant
        raise SurrogatePrototypeError("prototype produced no result")
    return result_payload
