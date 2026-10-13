from __future__ import annotations

import pytest

from extstats_advisor_research.foundations_interaction import (
    ConfigurationObservation,
    LandscapeValidationError,
    canonical_membership,
    complementarity_witnesses,
    configuration_identity,
    enumerate_subsets,
    exact_optima_by_budget,
    greedy_add_trace,
    improvement_values,
    monotonicity_report,
    pairwise_interaction_report,
    random_search_trace,
    submodularity_report,
    validate_complete_landscape,
)


def _landscape(values: dict[tuple[str, ...], float], universe: tuple[str, ...] = ("a", "b", "c")):
    return [
        ConfigurationObservation.complete(
            membership,
            objective,
            elapsed_ns=100 + index,
            planner_query_count=3,
        )
        for index, (membership, objective) in enumerate(values.items())
    ], universe


def test_subset_order_and_identity_are_deterministic() -> None:
    assert canonical_membership(("b", "a", "c"), ("c", "b")) == ("b", "c")
    subsets = enumerate_subsets(("a", "b", "c"))
    assert subsets[:4] == ((), ("a",), ("b",), ("c",))
    assert len(subsets) == 8
    assert configuration_identity(("a",)) == configuration_identity(("a",))
    assert configuration_identity(("a",)) != configuration_identity(("b",))


def test_complete_validation_rejects_missing_duplicate_and_failure() -> None:
    observations, universe = _landscape(
        {membership: 0.0 for membership in enumerate_subsets(("a", "b", "c"))}
    )
    assert len(validate_complete_landscape(observations, universe)) == 8
    with pytest.raises(LandscapeValidationError, match="coverage mismatch"):
        validate_complete_landscape(observations[:-1], universe)
    with pytest.raises(LandscapeValidationError, match="duplicate"):
        validate_complete_landscape([*observations, observations[0]], universe)
    failed = ConfigurationObservation.failed(("a",), "planner failed")
    with pytest.raises(LandscapeValidationError, match="failed configuration"):
        validate_complete_landscape([failed], ("a",))


def test_monotonicity_and_complementarity_witnesses() -> None:
    values = {
        (): 10.0,
        ("a",): 9.0,
        ("b",): 10.0,
        ("c",): 9.5,
        ("a", "b"): 7.0,
        ("a", "c"): 8.0,
        ("b", "c"): 9.0,
        ("a", "b", "c"): 6.0,
    }
    observations, universe = _landscape(values)
    landscape = validate_complete_landscape(observations, universe)
    improvements = improvement_values(landscape)
    report = monotonicity_report(improvements, universe)
    assert report["violation_count"] == 0
    witnesses = complementarity_witnesses(improvements, universe)
    assert any(item["candidate_id"] == "b" and item["context"] == ["a"] for item in witnesses)


def test_interaction_and_submodularity_reports_have_expected_signs() -> None:
    values = {
        (): 10.0,
        ("a",): 9.0,
        ("b",): 9.0,
        ("c",): 9.0,
        ("a", "b"): 7.0,
        ("a", "c"): 8.5,
        ("b", "c"): 8.5,
        ("a", "b", "c"): 6.0,
    }
    observations, universe = _landscape(values)
    improvements = improvement_values(validate_complete_landscape(observations, universe))
    interactions = pairwise_interaction_report(improvements, universe)
    assert interactions["sign_counts"]["positive"] > 0
    submodularity = submodularity_report(improvements, universe)
    assert submodularity["check_count"] > 0
    assert submodularity["violation_count"] >= 0


def test_optimum_and_budget_traces_are_explicitly_finite_universe() -> None:
    values = {
        (): 10.0,
        ("a",): 8.0,
        ("b",): 9.0,
        ("c",): 9.5,
        ("a", "b"): 7.0,
        ("a", "c"): 7.5,
        ("b", "c"): 8.5,
        ("a", "b", "c"): 6.0,
    }
    observations, universe = _landscape(values)
    improvements = improvement_values(validate_complete_landscape(observations, universe))
    optima = exact_optima_by_budget(improvements, universe)
    assert optima["exact_k"]["2"]["membership"] == ["a", "b"]
    greedy = greedy_add_trace(improvements, universe, 4)
    assert greedy["distinct_evaluations"] == 4
    assert greedy["method"].endswith("oracle-assisted-replay")
    random_trace = random_search_trace(improvements, universe, 4, seed=7)
    assert random_trace["distinct_evaluations"] == 4
    assert random_trace["evaluated_memberships"][0] == []


def test_failed_observation_preserves_diagnostics() -> None:
    observation = ConfigurationObservation.failed(
        ("a",), "native planner error", elapsed_ns=12, oracle_invocations=1
    )
    assert observation.objective is None
    assert observation.error == "native planner error"
    with pytest.raises(LandscapeValidationError):
        ConfigurationObservation("wrong", ("a",), "complete", 1.0, 0, 1, 0)
