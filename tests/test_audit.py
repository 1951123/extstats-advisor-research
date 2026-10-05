from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from extstats_advisor_research.analysis.audit import (
    QERROR_CONTRACT_VERSION,
    _source_artifact_digests,
    build_per_query_records,
    contribution_summary,
    distribution,
    resolve_selected_candidates,
    search_trace,
    singleton_landscape,
    weighted_objective,
)
from extstats_advisor_research.provenance import semantic_digest


def _utility(estimate: float, truth: int, query_id: str = "q1") -> SimpleNamespace:
    estimate_floor = max(estimate, 1.0)
    truth_floor = max(truth, 1)
    loss = max(estimate_floor / truth_floor, truth_floor / estimate_floor)
    item = SimpleNamespace(
        query_id=query_id,
        weight=1.0,
        estimate=estimate,
        truth=truth,
        loss=loss,
    )
    return SimpleNamespace(loss_contract=QERROR_CONTRACT_VERSION, per_query=(item,))


def test_query_audit_uses_floor_one_and_reproduces_weighted_mean() -> None:
    baseline = _utility(0, 0)
    final = _utility(2, 0)
    records = build_per_query_records(baseline, final)
    assert records[0]["baseline_qerror"] == 1.0
    assert records[0]["final_qerror"] == 2.0
    assert records[0]["classification"] == "worsened"
    assert weighted_objective(records, "baseline_qerror") == 1.0
    assert weighted_objective(records, "final_qerror") == 2.0
    assert contribution_summary(records)["top_1"]["baseline_objective_fraction"] == 1.0


def test_contribution_summary_supports_final_qerror_field() -> None:
    records = [
        {"weight": 1.0, "baseline_qerror": 10.0, "final_qerror": 2.0},
        {"weight": 1.0, "baseline_qerror": 1.0, "final_qerror": 8.0},
    ]
    assert (
        contribution_summary(records, "final_qerror")["top_1"]["baseline_objective_fraction"] == 0.8
    )


def test_distribution_and_classification_helpers() -> None:
    baseline = SimpleNamespace(
        loss_contract=QERROR_CONTRACT_VERSION,
        per_query=(
            SimpleNamespace(query_id="q1", weight=1.0, estimate=1, truth=1, loss=1.0),
            SimpleNamespace(query_id="q2", weight=1.0, estimate=4, truth=1, loss=4.0),
            SimpleNamespace(query_id="q3", weight=1.0, estimate=1, truth=1, loss=1.0),
        ),
    )
    final = SimpleNamespace(
        loss_contract=QERROR_CONTRACT_VERSION,
        per_query=(
            SimpleNamespace(query_id="q1", weight=1.0, estimate=2, truth=1, loss=2.0),
            SimpleNamespace(query_id="q2", weight=1.0, estimate=2, truth=1, loss=2.0),
            SimpleNamespace(query_id="q3", weight=1.0, estimate=1, truth=1, loss=1.0),
        ),
    )
    records = build_per_query_records(baseline, final)
    assert [item["classification"] for item in records] == ["worsened", "improved", "unchanged"]
    assert (
        distribution(records, "baseline_qerror", weighted_objective(records, "baseline_qerror"))[
            "p50"
        ]
        == 1.0
    )


def test_selected_candidates_and_singleton_ranking() -> None:
    candidate_universe = {
        "candidates": [
            {
                "candidate_id": "a",
                "kind": "postgresql.mcv",
                "relation_id": "rel",
                "column_ordinals": [1, 2],
                "column_names": ["x", "y"],
                "static_precedence_rank": 1,
            },
            {
                "candidate_id": "b",
                "kind": "postgresql.dependencies",
                "relation_id": "rel",
                "column_ordinals": [2, 3],
                "column_names": ["y", "z"],
                "static_precedence_rank": 2,
            },
            {
                "candidate_id": "c",
                "kind": "postgresql.mcv",
                "relation_id": "rel",
                "column_ordinals": [3, 4],
                "column_names": ["z", "w"],
                "static_precedence_rank": 3,
            },
        ]
    }
    native = {"candidates": [{"candidate_id": x, "state": "present"} for x in ("a", "b", "c")]}
    profile = {
        "candidate_profiles": [
            {
                "candidate_id": "a",
                "native_state": "present",
                "frozen_precedence_rank": 2,
                "singleton_objective": 8.0,
                "improvement": 2.0,
            },
            {
                "candidate_id": "b",
                "native_state": "present",
                "frozen_precedence_rank": 1,
                "singleton_objective": 9.0,
                "improvement": 1.0,
            },
            {
                "candidate_id": "c",
                "native_state": "present",
                "frozen_precedence_rank": 3,
                "singleton_objective": 10.0,
                "improvement": 0.0,
            },
        ]
    }
    recommendation = {
        "selected_candidates": [
            {
                "candidate_id": "a",
                "statistics_target": 100,
                "statistics_object": {"name": "extstats_a"},
                "deployment_order_position": 1,
            },
            {
                "candidate_id": "b",
                "statistics_target": 100,
                "statistics_object": {"name": "extstats_b"},
                "deployment_order_position": 2,
            },
        ]
    }
    selected = resolve_selected_candidates(
        ["a", "b"],
        candidate_universe,
        native,
        profile,
        recommendation,
        {"schema": "public", "name": "t"},
    )
    assert selected[0]["kind"] == "postgresql.mcv"
    assert selected[1]["recommendation_object_name"] == "extstats_b"
    landscape = singleton_landscape(
        profile,
        candidate_universe,
        {
            "screened_candidates": [{"candidate_id": "a"}, {"candidate_id": "b"}],
            "screened_candidate_ids": ["a", "b"],
        },
    )
    assert landscape["present_candidate_count"] == 3
    assert landscape["improving_singleton_count"] == 2
    assert landscape["neutral_singleton_count"] == 1
    assert [x["candidate_id"] for x in landscape["top_screened_candidates"]] == ["a", "b"]


def test_search_trace_preserves_recorded_incomplete_round() -> None:
    plan = {
        "screened_candidate_ids": ["a", "b", "c", "d"],
        "budget": {"wall_clock_seconds": 30.0},
    }
    search = {
        "baseline_objective": 10.0,
        "first_round_source": "singleton-profile-cache",
        "first_round_evaluations": [{"candidate_id": x} for x in ("a", "b")],
        "accepted_moves": [
            {
                "round_index": 1,
                "added_candidate_id": "a",
                "objective_before": 10.0,
                "objective_after": 8.0,
                "improvement": 2.0,
            },
            {
                "round_index": 2,
                "added_candidate_id": "b",
                "objective_before": 8.0,
                "objective_after": 7.0,
                "improvement": 1.0,
            },
        ],
        "completed_rounds": [
            {"round_index": 2, "evaluations": [{"candidate_id": "b"}, {"candidate_id": "c"}]}
        ],
        "final_ordered_candidate_ids": ["a", "b"],
        "runtime_metadata": {
            "completed_round_count": 1,
            "cached_singleton_configuration_count": 2,
            "live_configuration_evaluation_count": 3,
            "planner_query_estimate_count": 30,
            "elapsed_search_seconds": 30.0,
            "partial_final_round_evaluation_count": 1,
        },
        "termination_reason": "budget-expired-incomplete-round",
    }
    trace = search_trace(plan, search)
    assert trace["rounds"][0]["source"] == "singleton-profile-cache"
    assert trace["rounds"][1]["candidate_configurations_evaluated"] == 2
    assert trace["attempted_final_round"]["remaining_candidates"] == ["c", "d"]
    assert trace["attempted_final_round"]["evaluations_not_completed"] == 1


def test_source_artifact_provenance_binding(tmp_path) -> None:
    artifact = {"semantic_digest": semantic_digest({"value": 1}), "value": 1}
    (tmp_path / "artifact.json").write_text(json.dumps(artifact), encoding="utf-8")
    manifest = {
        "artifacts": {
            "artifact": {"path": "artifact.json", "semantic_digest": artifact["semantic_digest"]}
        }
    }
    assert _source_artifact_digests(tmp_path, manifest) == {"artifact": artifact["semantic_digest"]}
    manifest["artifacts"]["artifact"]["semantic_digest"] = "0" * 64
    with pytest.raises(ValueError, match="digest mismatch"):
        _source_artifact_digests(tmp_path, manifest)
