from __future__ import annotations

import math

import pytest

from extstats_advisor_research.foundations_pessimistic_enumeration import (
    CandidateModelSpec,
    PessimisticInstance,
    PessimisticModelError,
    QueryModel,
    build_pessimistic_instance,
    enumerate_maximal_matchings,
    solve_direct,
    solve_local_tables,
)


def _instance(
    *,
    candidate_specs=None,
    queries=None,
    budget=3.0,
):
    if candidate_specs is None:
        candidate_specs = (
            CandidateModelSpec("a", ("x", "y"), 1.0),
            CandidateModelSpec("b", ("y", "z"), 1.0),
            CandidateModelSpec("c", ("z", "w"), 1.0),
        )
    if queries is None:
        queries = (
            QueryModel(
                "q",
                1.0,
                1.0,
                ("a", "b", "c"),
                (("a", -1.0), ("b", 0.0), ("c", -1.0)),
            ),
        )
    return PessimisticInstance(tuple(candidate_specs), tuple(queries), budget)


def test_maximal_matching_cases_distinguish_maximal_from_maximum() -> None:
    columns = {
        "a": ("x", "y"),
        "b": ("y", "z"),
        "c": ("z", "w"),
    }
    assert enumerate_maximal_matchings((), columns) == ((),)
    assert enumerate_maximal_matchings(("a",), columns) == (("a",),)
    assert enumerate_maximal_matchings(("a", "b"), columns) == (("a",), ("b",))
    assert enumerate_maximal_matchings(("a", "c"), columns) == (("a", "c"),)
    assert enumerate_maximal_matchings(("a", "b", "c"), columns) == (("a", "c"), ("b",))


def test_triangle_has_three_singleton_maximal_matchings() -> None:
    columns = {"a": ("x", "y"), "b": ("y", "z"), "c": ("x", "z")}
    assert enumerate_maximal_matchings(("a", "b", "c"), columns) == (
        ("a",),
        ("b",),
        ("c",),
    )


def test_empty_universe_and_empty_local_graph_are_valid() -> None:
    instance = PessimisticInstance(
        (),
        (QueryModel("q", 2.0, -3.0, (), ()),),
        0.0,
    )
    direct = solve_direct(instance)
    local = solve_local_tables(instance)
    assert direct.representative_membership == ()
    assert direct.objective == pytest.approx(6.0)
    assert local.objective == direct.objective
    assert local.stats.query_local_subsets_evaluated == 1


def test_direct_and_local_enumeration_agree_with_witnesses() -> None:
    instance = _instance()
    direct = solve_direct(instance)
    local = solve_local_tables(instance)
    assert local.objective == pytest.approx(direct.objective)
    assert local.representative_membership == direct.representative_membership
    assert local.optimal_memberships == direct.optimal_memberships
    assert local.per_query_loss_map == direct.per_query_loss_map
    assert local.worst_matching_map == direct.worst_matching_map
    assert local.stats.global_subsets_checked == 8
    assert local.stats.budget_feasible_subsets == 8
    assert local.stats.query_local_subsets_evaluated == 8
    assert local.stats.maximal_matchings_enumerated == 11


def test_budget_zero_and_nonuniform_costs_are_at_most_budget() -> None:
    specs = (
        CandidateModelSpec("a", ("x", "y"), 2.0),
        CandidateModelSpec("b", ("z", "w"), 1.0),
    )
    queries = (QueryModel("q", 1.0, 1.0, ("a", "b"), (("a", -1.0), ("b", -1.0))),)
    zero = solve_local_tables(PessimisticInstance(specs, queries, 0.0))
    one = solve_local_tables(PessimisticInstance(specs, queries, 1.0))
    two = solve_local_tables(PessimisticInstance(specs, queries, 2.0))
    assert zero.representative_membership == ()
    assert one.representative_membership == ("b",)
    assert two.representative_membership in (("a",), ("b",))
    assert all(two.representative_storage_cost <= 2.0 for _ in (0,))


def test_multiple_optima_are_retained_and_canonical() -> None:
    instance = PessimisticInstance(
        (
            CandidateModelSpec("b", ("x", "y"), 1.0),
            CandidateModelSpec("a", ("z", "w"), 1.0),
        ),
        (QueryModel("q", 1.0, 0.0, ("a", "b"), (("a", 0.0), ("b", 0.0))),),
        1.0,
    )
    result = solve_local_tables(instance)
    assert result.optimal_set_count == 3
    assert result.optimal_memberships == ((), ("a",), ("b",))
    assert result.representative_membership == ()


def test_overlapping_candidates_can_be_preferred_for_different_queries() -> None:
    specs = (
        CandidateModelSpec("a", ("x", "y"), 1.0),
        CandidateModelSpec("b", ("y", "z"), 1.0),
    )
    queries = (
        QueryModel("qa", 1.0, 1.0, ("a",), (("a", -1.0),)),
        QueryModel("qb", 1.0, 1.0, ("b",), (("b", -1.0),)),
    )
    result = solve_local_tables(PessimisticInstance(specs, queries, 2.0))
    assert result.representative_membership == ("a", "b")
    assert result.objective == pytest.approx(0.0)


def test_builder_recovers_target_floor_and_strictly_positive_raw_factors() -> None:
    instance = build_pessimistic_instance(
        candidate_ids=("a",),
        candidate_columns={"a": ("x", "y")},
        candidate_costs={"a": 1.0},
        query_ids=("q",),
        empty_estimates={"q": 2.0},
        singleton_estimates={"a": {"q": 1.0}},
        truths={"q": 0.0},
        weights={"q": 3.0},
        applicable_candidates={"q": ("a",)},
        budget=1.0,
    )
    assert instance.queries[0].alpha == pytest.approx(math.log(2.0))
    assert instance.queries[0].correction_map["a"] == pytest.approx(-math.log(2.0))
    assert solve_local_tables(instance).objective == pytest.approx(0.0)


@pytest.mark.parametrize(
    "field,value",
    [
        ("empty", 0.0),
        ("singleton", 0.0),
        ("truth", -1.0),
        ("weight", 0.0),
        ("cost", 0.0),
    ],
)
def test_builder_rejects_invalid_model_inputs(field: str, value: float) -> None:
    empty = {"q": 2.0}
    singleton = {"a": {"q": 1.0}}
    truths = {"q": 1.0}
    weights = {"q": 1.0}
    costs = {"a": 1.0}
    if field == "empty":
        empty["q"] = value
    elif field == "singleton":
        singleton["a"]["q"] = value
    elif field == "truth":
        truths["q"] = value
    elif field == "weight":
        weights["q"] = value
    else:
        costs["a"] = value
    with pytest.raises(PessimisticModelError):
        build_pessimistic_instance(
            candidate_ids=("a",),
            candidate_columns={"a": ("x", "y")},
            candidate_costs=costs,
            query_ids=("q",),
            empty_estimates=empty,
            singleton_estimates=singleton,
            truths=truths,
            weights=weights,
            applicable_candidates={"q": ("a",)},
            budget=1.0,
        )


def test_missing_singleton_factor_is_explicit() -> None:
    with pytest.raises(PessimisticModelError, match="missing singleton"):
        build_pessimistic_instance(
            candidate_ids=("a",),
            candidate_columns={"a": ("x", "y")},
            candidate_costs={"a": 1.0},
            query_ids=("q",),
            empty_estimates={"q": 2.0},
            singleton_estimates={},
            truths={"q": 1.0},
            weights={"q": 1.0},
            applicable_candidates={"q": ("a",)},
            budget=1.0,
        )
