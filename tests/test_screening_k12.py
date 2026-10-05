from __future__ import annotations

import pytest

from extstats_advisor_research.screening_k12 import (
    COMPLETED_TERMINATIONS,
    EXPECTED_SOURCE_DIGESTS,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
    K8_SELECTED,
    SOURCE_RESEARCH_SHA,
    SOURCE_RUN_ID,
    TRANSFER_SOURCE_ADVISOR_SHA,
    classify_screening,
    rank_definitions,
    rank_outcomes,
    validate_artifact_source_binding,
    validate_completed_termination,
)


def test_screening_classification_allows_non_nested_membership() -> None:
    result = classify_screening(10.0, 9.0, ["a", "b"], ["a", "c"])
    assert result["decision"] == "k8-screen-limited"
    assert result["k8_only_selected_ids"] == ["b"]
    assert result["k12_only_selected_ids"] == ["c"]


def test_screening_classification_requires_same_membership_for_sufficiency() -> None:
    result = classify_screening(10.0, 10.0, ["a", "b"], ["a", "c"])
    assert result["objective_equal_within_tolerance"] is True
    assert result["decision"] == "k8-screen-limited"


def test_rank_definitions_extracts_9_to_12_and_native_state() -> None:
    universe = {
        "candidates": [
            {"candidate_id": f"c{rank}", "kind": "postgresql.mcv", "column_names": [f"x{rank}"]}
            for rank in range(9, 13)
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
            for rank in range(9, 13)
        ]
    }
    native = {
        "candidates": [{"candidate_id": f"c{rank}", "state": "present"} for rank in range(9, 13)]
    }
    result = rank_definitions(universe, profile, native)
    assert [item["frozen_singleton_rank"] for item in result] == [9, 10, 11, 12]
    assert all(item["native_state"] == "present" for item in result)


def test_rank_outcomes_distinguish_accepted_rejected_and_never_reached() -> None:
    definitions = [
        {"candidate_id": "accepted", "frozen_singleton_rank": 9},
        {"candidate_id": "rejected", "frozen_singleton_rank": 10},
        {"candidate_id": "never", "frozen_singleton_rank": 11},
    ]
    search = {
        "accepted_moves": [{"added_candidate_id": "accepted"}],
        "completed_rounds": [{"evaluations": [{"candidate_id": "rejected"}]}],
    }
    outcomes = rank_outcomes(definitions, search)
    assert [item["outcome"] for item in outcomes] == [
        "accepted",
        "rejected-in-completed-round",
        "never-reached-because-search-terminated-earlier",
    ]


def test_completed_termination_requirement() -> None:
    for reason in COMPLETED_TERMINATIONS:
        assert validate_completed_termination(reason) is True
    with pytest.raises(ValueError, match="completed"):
        validate_completed_termination("budget-expired-incomplete-round")


def test_artifact_source_binding_preserves_source_execution_bridge() -> None:
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
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        },
    }
    assert validate_artifact_source_binding(artifact) is True


def test_artifact_source_binding_rejects_digest_drift() -> None:
    artifact = {
        "source": {
            "run_id": SOURCE_RUN_ID,
            "research_commit_sha": SOURCE_RESEARCH_SHA,
            "advisor_commit_sha": TRANSFER_SOURCE_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "artifact_digests": {**EXPECTED_SOURCE_DIGESTS, "snapshot": "drift"},
        },
        "execution_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        },
    }
    with pytest.raises(ValueError, match="source digests"):
        validate_artifact_source_binding(artifact)


def test_frozen_k8_membership_is_seven_and_ordered() -> None:
    assert len(K8_SELECTED) == 7
    assert K8_SELECTED[0] == "cand_cb55f02882e2d0ef75203787"
