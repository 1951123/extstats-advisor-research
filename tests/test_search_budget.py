from __future__ import annotations

import pytest

from extstats_advisor_research import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from extstats_advisor_research.search_budget import (
    EXPECTED_SOURCE_RESEARCH_SHA,
    _assert_compact_tree,
    classify_termination,
    first_completed_budget,
    resolve_selected_definitions,
    validate_budgets,
    validate_objective_monotonicity,
    validate_source_binding,
)


def _result(budget: int, objective: float, termination: str = "local-optimum") -> dict:
    return {
        "budget_seconds": budget,
        "final_objective": objective,
        "termination_reason": termination,
    }


def test_budget_ordering_and_completion_classification() -> None:
    assert validate_budgets([60, 120, 180]) == (60, 120, 180)
    assert classify_termination("local-optimum") == "completed"
    assert classify_termination("budget-expired-incomplete-round") == "budget-limited"
    assert (
        first_completed_budget(
            [_result(60, 4.0, "budget-expired-incomplete-round"), _result(120, 3.0)]
        )
        == 120
    )
    with pytest.raises(ValueError):
        validate_budgets([120, 60])


def test_objective_monotonicity_is_validated() -> None:
    assert validate_objective_monotonicity(5.0, [_result(60, 4.0), _result(120, 3.0)])
    with pytest.raises(ValueError, match="worsened"):
        validate_objective_monotonicity(5.0, [_result(60, 4.0), _result(120, 4.1)])


def test_source_binding_requires_corrected_canonical_run() -> None:
    source = {
        "run_id": "bf7fda28d90b3da88e7a14e4",
        "status": "complete",
        "research_commit_sha": EXPECTED_SOURCE_RESEARCH_SHA,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
    }
    assert validate_source_binding(source)["run_id"] == source["run_id"]
    with pytest.raises(ValueError):
        validate_source_binding({**source, "research_commit_sha": "0" * 40})


def test_selected_candidate_resolution_keeps_singleton_and_marginal_values() -> None:
    candidate = {
        "candidate_id": "cand-a",
        "kind": "postgresql.mcv",
        "column_names": ["education", "education_num"],
    }
    profile = {
        "candidate_id": "cand-a",
        "frozen_precedence_rank": 1,
        "singleton_objective": 3.0,
        "improvement": 2.0,
    }
    result = resolve_selected_definitions(
        ["cand-a"],
        {"candidates": [candidate]},
        {"candidate_profiles": [profile]},
        {"accepted_moves": [{"added_candidate_id": "cand-a", "improvement": 2.0}]},
    )
    assert result == [
        {
            "candidate_id": "cand-a",
            "kind": "postgresql.mcv",
            "column_names": ["education", "education_num"],
            "frozen_precedence_rank": 1,
            "singleton_objective": 3.0,
            "singleton_improvement": 2.0,
            "marginal_add_improvement": 2.0,
        }
    ]


def test_calibration_tree_rejects_bulk_source_artifacts(tmp_path) -> None:
    _assert_compact_tree(tmp_path)
    (tmp_path / "candidate-universe.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="bulk source artifact"):
        _assert_compact_tree(tmp_path)
