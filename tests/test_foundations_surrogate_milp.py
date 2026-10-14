from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_surrogate_milp import (
    SurrogatePrototypeError,
    SurrogateSolverUnavailable,
    build_surrogate_milp_formulation,
    enumerate_surrogate_optimum,
    milp_backend_version,
    recover_surrogate_coefficients,
    select_disjoint_surrogate_candidates,
    solve_surrogate_milp,
    surrogate_greedy,
    weighted_log_error,
    weighted_qerror,
    write_surrogate_result_json,
)


def _candidate(candidate_id: str, ordinals: tuple[int, int], rank: int, kind: str = "mcv"):
    return SimpleNamespace(
        candidate_id=candidate_id,
        relation_id="rel",
        column_ordinals=ordinals,
        column_names=tuple(f"c{ordinal}" for ordinal in ordinals),
        static_precedence_rank=rank,
        kind=kind,
        to_dict=lambda: {"candidate_id": candidate_id, "column_ordinals": list(ordinals)},
    )


def _universe():
    candidates = (
        _candidate("a", (1, 2), 1),
        _candidate("b", (3, 4), 2, "dependencies"),
        _candidate("c", (2, 5), 3),
        _candidate("d", (6, 7), 4),
    )
    profiles = tuple(
        SimpleNamespace(query_id=query_id, analysis_status="supported", weight=1.0)
        for query_id in ("q1", "q2")
    )
    incidence = tuple(
        SimpleNamespace(query_id=query_id, candidate_id=candidate_id)
        for candidate_id, query_id in (("a", "q1"), ("b", "q2"), ("c", "q1"), ("d", "q2"))
    )
    return SimpleNamespace(candidates=candidates, query_profiles=profiles, incidence=incidence)


def _coefficients():
    return recover_surrogate_coefficients(
        candidate_ids=("a", "b"),
        query_ids=("q1", "q2"),
        empty_estimates={"q1": 10.0, "q2": 20.0},
        singleton_estimates={
            "a": {"q1": 5.0, "q2": 20.0},
            "b": {"q1": 10.0, "q2": 10.0},
        },
        truths={"q1": 5.0, "q2": 10.0},
        weights={"q1": 1.0, "q2": 1.0},
    )


def test_selection_is_disjoint_deterministic_and_metadata_only() -> None:
    first = select_disjoint_surrogate_candidates(_universe(), max_candidates=3)
    second = select_disjoint_surrogate_candidates(_universe(), max_candidates=3)
    assert tuple(candidate.candidate_id for candidate in first) == tuple(
        candidate.candidate_id for candidate in second
    )
    occupied = set()
    for candidate in first:
        assert not occupied.intersection(candidate.column_ordinals)
        occupied.update(candidate.column_ordinals)


def test_coefficient_recovery_and_exact_multiplicative_prediction() -> None:
    coefficients = _coefficients()
    assert coefficients.alpha == pytest.approx((0.6931471805599453, 0.6931471805599453))
    assert coefficients.predicted_estimates(3) == pytest.approx({"q1": 5.0, "q2": 10.0})
    assert coefficients.objective(3) == pytest.approx(0.0)


def test_zero_truth_uses_target_only_floor_and_preserves_raw_prediction() -> None:
    coefficients = recover_surrogate_coefficients(
        candidate_ids=("a",),
        query_ids=("q1",),
        empty_estimates={"q1": 2.0},
        singleton_estimates={"a": {"q1": 0.5}},
        truths={"q1": 0.0},
        weights={"q1": 1.0},
    )
    assert coefficients.predicted_estimates(1) == pytest.approx({"q1": 0.5})
    assert coefficients.objective(1) == pytest.approx(abs(math.log(0.5)))
    assert weighted_log_error({"q1": 0.5}, {"q1": 0.0}, {"q1": 1.0}) == pytest.approx(
        abs(math.log(0.5))
    )
    assert weighted_qerror({"q1": 0.5}, {"q1": 0.0}, {"q1": 1.0}) == pytest.approx(1.0)


def test_nonpositive_raw_estimates_are_rejected() -> None:
    with pytest.raises(SurrogatePrototypeError, match="strictly positive"):
        recover_surrogate_coefficients(
            candidate_ids=("a",),
            query_ids=("q1",),
            empty_estimates={"q1": 0.0},
            singleton_estimates={"a": {"q1": 1.0}},
            truths={"q1": 1.0},
            weights={"q1": 1.0},
        )


def test_formulation_has_budget_and_two_absolute_value_rows_per_query() -> None:
    formulation = build_surrogate_milp_formulation(_coefficients(), budget=1)
    assert formulation.variable_names == ("x_a", "x_b", "t_q1", "t_q2")
    assert formulation.integrality == (1, 1, 0, 0)
    assert len(formulation.constraints) == 1 + 2 * 2
    assert formulation.constraints[0][2] == 1.0


def test_enumeration_is_at_most_budget_and_deterministically_tied() -> None:
    coefficients = _coefficients()
    result = enumerate_surrogate_optimum(coefficients, budget=1)
    assert result.method == "enumeration-reference"
    assert result.certified_optimal
    assert result.mask == 1


def test_greedy_stops_when_no_positive_surrogate_improvement_remains() -> None:
    coefficients = recover_surrogate_coefficients(
        candidate_ids=("a", "b"),
        query_ids=("q1",),
        empty_estimates={"q1": 10.0},
        singleton_estimates={"a": {"q1": 5.0}, "b": {"q1": 10.0}},
        truths={"q1": 5.0},
        weights={"q1": 1.0},
    )
    result = surrogate_greedy(coefficients, budget=2)
    assert result.mask == 1
    assert result.stopped_early
    assert len(result.steps) == 1


def test_milp_solver_is_available_and_matches_enumeration_for_all_budgets() -> None:
    assert milp_backend_version() is not None
    coefficients = _coefficients()
    for budget in range(3):
        expected = enumerate_surrogate_optimum(coefficients, budget=budget)
        actual = solve_surrogate_milp(coefficients, budget=budget)
        assert actual.certified_optimal
        assert actual.objective == pytest.approx(expected.objective, abs=1e-10)
        assert sum(1 for index in range(2) if actual.mask & (1 << index)) <= budget


def test_result_writer_is_atomic_no_overwrite_and_standard_json(tmp_path) -> None:
    destination = tmp_path / "result.json"
    write_surrogate_result_json({"value": 1.25, "objective": None}, destination)
    assert json.loads(destination.read_text()) == {"value": 1.25, "objective": None}
    with pytest.raises(FileExistsError):
        write_surrogate_result_json({"value": 2}, destination)


def test_solver_unavailable_exception_is_distinct() -> None:
    assert issubclass(SurrogateSolverUnavailable, RuntimeError)
