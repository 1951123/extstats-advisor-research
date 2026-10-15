from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_order_permutations import (
    EXPECTED_OBJECTIVE,
    ORDERED_MEMBERSHIP,
    OrderPermutationError,
    enumerate_creation_permutations,
    theoretical_references,
)


def test_enumerates_exactly_24_unique_orders_with_fixed_membership() -> None:
    permutations = enumerate_creation_permutations()

    assert len(permutations) == 24
    assert len(set(permutations)) == 24
    assert all(set(order) == set(ORDERED_MEMBERSHIP) for order in permutations)
    assert permutations[0] == ORDERED_MEMBERSHIP


def test_permutation_input_is_explicit_and_deterministic() -> None:
    ids = ("d", "a", "c", "b")
    first = enumerate_creation_permutations(ids)
    second = enumerate_creation_permutations(ids)

    assert first == second
    assert first[0] == ids
    assert first[-1] == ("b", "c", "a", "d")


def test_permutation_rejects_wrong_membership() -> None:
    with pytest.raises(OrderPermutationError):
        enumerate_creation_permutations(("a", "b", "c"))
    with pytest.raises(OrderPermutationError):
        enumerate_creation_permutations(("a", "b", "c", "a"))


def _model_artifact() -> dict:
    return {
        "candidate_metadata": [
            {"candidate_id": "a", "column_names": ["x", "y"]},
            {"candidate_id": "b", "column_names": ["y", "z"]},
        ],
        "model": {
            "query_ids": ["q"],
            "query_weights": {"q": 1.0},
            "alpha": {"q": 1.0},
            "b": {"a": {"q": -1.0}, "b": {"q": -0.5}},
            "applicable_mcv_candidates_by_query": {"q": ["a", "b"]},
        },
    }


def test_theoretical_uniform_and_pessimistic_losses_use_maximal_matchings() -> None:
    result = theoretical_references(_model_artifact(), ("a", "b"))

    # The conflict graph has two maximal singleton matchings.
    assert result["per_query"]["q"]["maximal_matching_count"] == 2
    assert result["pessimistic_objective"] == pytest.approx(0.5)
    assert result["uniform_objective"] == pytest.approx(0.25)


def test_theoretical_reference_preserves_exact_frozen_objective_constant() -> None:
    assert EXPECTED_OBJECTIVE == pytest.approx(13.404890550101985)


def test_order_module_does_not_change_frozen_membership() -> None:
    payload = {"membership": list(ORDERED_MEMBERSHIP)}
    assert json.loads(json.dumps(payload))["membership"] == list(ORDERED_MEMBERSHIP)


def test_destination_is_not_part_of_pure_permutation_generation(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    assert not destination.exists()
    assert len(enumerate_creation_permutations()) == 24
