from __future__ import annotations

import pytest

from extstats_advisor_research.screening_k16 import (
    COMPLETED_TERMINATIONS,
    EXPECTED_SOURCE_DIGESTS,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
    K12_SELECTED,
    SOURCE_RESEARCH_SHA,
    SOURCE_RUN_ID,
    TRANSFER_SOURCE_ADVISOR_SHA,
    accepted_move_curve,
    classify_screening,
    membership_comparison,
    parsimony_metrics,
    rank_definitions,
    rank_outcomes,
    validate_artifact_source_binding,
    validate_completed_termination,
)


def test_screening_decision_requires_completed_non_nested_comparison() -> None:
    result = classify_screening(
        9.0, 8.0, ["a", "b"], ["a", "c"], termination_reason="local-optimum"
    )
    assert result["decision"] == "k12-screen-limited"
    assert result["k12_only_selected_ids"] == ["b"]
    assert result["k16_only_selected_ids"] == ["c"]
    incomplete = classify_screening(
        9.0, 8.0, ["a"], ["a"], termination_reason="budget-expired-incomplete-round"
    )
    assert incomplete["decision"] == "k16-incomplete"


def test_screening_decision_can_prove_k12_sufficient() -> None:
    result = classify_screening(
        9.0, 9.0, ["a", "b"], ["a", "b"], termination_reason="local-optimum"
    )
    assert result["decision"] == "k12-sufficient-within-k16"


def test_membership_comparison_reports_non_nested_sets() -> None:
    result = membership_comparison(["a", "b"], ["a", "c"], ["a", "d"])
    assert result["k8_intersection_k12"] == ["a"]
    assert result["k12_intersection_k16"] == ["a"]
    assert result["k8_only"] == ["b"]
    assert result["k12_only_vs_k8"] == ["c"]
    assert result["k16_only_vs_k12"] == ["d"]
    assert result["k12_selected_not_k16"] == ["c"]


def test_rank_definitions_extracts_13_to_16_and_native_state() -> None:
    universe = {
        "candidates": [
            {"candidate_id": f"c{rank}", "kind": "postgresql.mcv", "column_names": [f"x{rank}"]}
            for rank in range(13, 17)
        ]
    }
    profile = {
        "candidate_profiles": [
            {
                "candidate_id": f"c{rank}",
                "frozen_precedence_rank": rank,
                "singleton_objective": float(rank),
                "improvement": 1.0 / rank,
            }
            for rank in range(13, 17)
        ]
    }
    native = {
        "candidates": [{"candidate_id": f"c{rank}", "state": "present"} for rank in range(13, 17)]
    }
    result = rank_definitions(universe, profile, native)
    assert [item["frozen_singleton_rank"] for item in result] == [13, 14, 15, 16]
    assert all(item["native_state"] == "present" for item in result)


def test_rank_outcomes_report_rejection_round_and_acceptance() -> None:
    definitions = [
        {"candidate_id": "accepted", "frozen_singleton_rank": 13},
        {"candidate_id": "rejected", "frozen_singleton_rank": 14},
        {"candidate_id": "never", "frozen_singleton_rank": 15},
    ]
    search = {
        "accepted_moves": [{"added_candidate_id": "accepted"}],
        "completed_rounds": [{"round_index": 3, "evaluations": [{"candidate_id": "rejected"}]}],
    }
    outcomes = rank_outcomes(definitions, search)
    assert outcomes[0]["accepted"] is True
    assert outcomes[1]["rejection_completed_round"] == 3
    assert outcomes[2]["outcome"] == "never-reached-because-search-terminated-earlier"


def test_parsimony_and_fraction_metrics() -> None:
    result = parsimony_metrics(10.0, 8.0, 7.0, 6.0, 7, 10, 12)
    assert result["additional_objects_k8_to_k12"] == 3
    assert result["additional_objects_k12_to_k16"] == 2
    assert result["objective_gain_per_added_object_k8_to_k12"] == pytest.approx(1 / 3)
    assert result["objective_gain_per_added_object_k12_to_k16"] == pytest.approx(1 / 2)
    assert result["r8_fraction_of_k16_total_improvement"] == pytest.approx(0.5)
    assert result["r12_fraction_of_k16_total_improvement"] == pytest.approx(0.75)


def test_accepted_move_curve_keeps_marginal_distinct() -> None:
    curve = accepted_move_curve(
        {
            "baseline_objective": 10.0,
            "accepted_moves": [
                {"objective_after": 7.0, "improvement": 3.0},
                {"objective_after": 6.5, "improvement": 0.5},
            ],
        }
    )
    assert curve[1]["cumulative_improvement_from_baseline"] == 3.0
    assert curve[2]["marginal_improvement_of_latest_accepted_object"] == 0.5


def test_completed_termination_is_required_for_completion() -> None:
    for reason in COMPLETED_TERMINATIONS:
        assert validate_completed_termination(reason) is True
    with pytest.raises(ValueError, match="completed"):
        validate_completed_termination("budget-expired-incomplete-round")


def test_artifact_source_binding_and_credential_rejection() -> None:
    artifact = {
        "source": {
            "run_id": SOURCE_RUN_ID,
            "research_commit_sha": SOURCE_RESEARCH_SHA,
            "advisor_commit_sha": TRANSFER_SOURCE_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "artifact_digests": EXPECTED_SOURCE_DIGESTS,
        },
        "execution_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "advisor_commit_sha": "bb4d58d46e734981a4542de4bcf59441d3effb98",
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        },
    }
    assert validate_artifact_source_binding(artifact) is True
    with pytest.raises(ValueError, match="credentials"):
        from extstats_advisor_research.provenance import reject_credentials

        reject_credentials({"planner_dsn": "postgresql://not-written"})


def test_k12_reference_membership_is_frozen() -> None:
    assert len(K12_SELECTED) == 10
    assert K12_SELECTED[-1] == "cand_2c9419ea6e50d1a0b1b5b706"
