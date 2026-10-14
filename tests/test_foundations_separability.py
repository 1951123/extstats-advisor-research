from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_separability import (
    SeparabilitySelectionError,
    activation_order_for_pair,
    multiplicative_quadruple,
    rank_disjoint_candidate_pairs,
    require_disjoint_candidates,
    select_disjoint_candidate_pairs,
)


def _candidate(
    candidate_id: str,
    ordinals: tuple[int, int],
    rank: int,
    kind: str = "postgresql.mcv",
) -> SimpleNamespace:
    return SimpleNamespace(
        candidate_id=candidate_id,
        relation_id="rel",
        kind=kind,
        column_ordinals=ordinals,
        column_names=tuple(f"c{ordinal}" for ordinal in ordinals),
        static_precedence_rank=rank,
    )


def _universe() -> SimpleNamespace:
    candidates = (
        _candidate("a", (1, 2), 1),
        _candidate("b", (3, 4), 2),
        _candidate("c", (2, 5), 3),
        _candidate("d", (6, 7), 4, "postgresql.dependencies"),
    )
    profiles = tuple(
        SimpleNamespace(
            query_id=query_id,
            analysis_status="supported",
            weight=1.0,
        )
        for query_id in ("q1", "q2", "q3")
    )
    incidence = tuple(
        SimpleNamespace(query_id=query_id, candidate_id=candidate_id)
        for candidate_id, query_id in (
            ("a", "q1"),
            ("b", "q1"),
            ("b", "q2"),
            ("c", "q2"),
            ("d", "q3"),
        )
    )
    return SimpleNamespace(candidates=candidates, query_profiles=profiles, incidence=incidence)


def test_disjoint_pairs_use_actual_columns_and_deterministic_joint_relevance() -> None:
    ranked = rank_disjoint_candidate_pairs(_universe())
    assert all(set(pair.candidate_ids) != {"a", "c"} for pair in ranked)
    assert ranked[0].candidate_ids == ("a", "b")
    assert ranked[0].joint_query_ids == ("q1",)
    selected = select_disjoint_candidate_pairs(ranked, max_pairs=3)
    assert len(selected) == 2
    assert selected == select_disjoint_candidate_pairs(ranked, max_pairs=3)


def test_overlapping_pair_is_rejected() -> None:
    candidates = _universe().candidates
    with pytest.raises(SeparabilitySelectionError, match="overlapping"):
        require_disjoint_candidates(candidates[0], candidates[2])


def test_global_precedence_is_used_for_activation_order() -> None:
    assert activation_order_for_pair(("b", "a"), ("a", "c", "b")) == ("a", "b")
    with pytest.raises(SeparabilitySelectionError, match="not covered"):
        activation_order_for_pair(("a", "x"), ("a", "b"))
    with pytest.raises(SeparabilitySelectionError, match="duplicates"):
        activation_order_for_pair(("a", "b"), ("a", "b", "b"))


def test_exact_multiplicative_quadruple_and_categories() -> None:
    exact = multiplicative_quadruple(
        "q1", estimate_empty=2, estimate_a=3, estimate_b=4, estimate_ab=6
    )
    assert exact.valid
    assert exact.category == "two-sided-informative"
    assert exact.delta == pytest.approx(0.0)
    assert exact.rho == pytest.approx(1.0)
    assert exact.passes(1e-12)

    one_sided = multiplicative_quadruple(
        "q1", estimate_empty=2, estimate_a=3, estimate_b=2, estimate_ab=6
    )
    assert one_sided.category == "one-sided"

    unchanged = multiplicative_quadruple(
        "q1", estimate_empty=2, estimate_a=2, estimate_b=2, estimate_ab=2
    )
    assert unchanged.category == "non-informative"


def test_multiplicative_violation_tolerance_and_invalid_estimates() -> None:
    violation = multiplicative_quadruple(
        "q1", estimate_empty=2, estimate_a=3, estimate_b=4, estimate_ab=7
    )
    assert violation.valid
    assert violation.absolute_log_residual == pytest.approx(abs(math.log(7 / 6)))
    assert not violation.passes(1e-10)

    for values in ((0, 3, 4, 6), (2, float("nan"), 4, 6), (2, 3, float("inf"), 6)):
        invalid = multiplicative_quadruple(
            "q1",
            estimate_empty=values[0],
            estimate_a=values[1],
            estimate_b=values[2],
            estimate_ab=values[3],
        )
        assert not invalid.valid
        assert invalid.category == "invalid"


def test_singleton_change_classification_uses_explicit_tolerance() -> None:
    nearly_unchanged = multiplicative_quadruple(
        "q1",
        estimate_empty=10.0,
        estimate_a=10.0 + 5e-13,
        estimate_b=20.0,
        estimate_ab=20.0,
        singleton_change_tolerance=1e-12,
    )
    assert nearly_unchanged.category == "one-sided"
    changed = multiplicative_quadruple(
        "q1",
        estimate_empty=10.0,
        estimate_a=10.0 + 5e-13,
        estimate_b=20.0,
        estimate_ab=20.0,
        singleton_change_tolerance=1e-13,
    )
    assert changed.category == "two-sided-informative"
    assert changed.singleton_change_tolerance == pytest.approx(1e-13)
