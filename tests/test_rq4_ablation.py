from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.provenance import write_json
from extstats_advisor_research.rq4_ablation import (
    FIXED_K,
    METHOD_IDS,
    ConfigurationEvaluation,
    EvaluationBudget,
    RQ4ValidationError,
    build_eligible_universe,
    build_synthetic_rq4_artifact,
    run_rq4_ablation,
    validate_rq4_artifact,
)
from extstats_advisor_research.system_freeze import load_system_freeze


def test_eligible_universe_excludes_non_native_or_missing_payload_without_outcomes() -> None:
    candidates = {
        "format_version": "candidate-universe-test-v1",
        "source_snapshot_semantic_digest": "a" * 64,
        "candidates": [
            {
                "candidate_id": "eligible",
                "relation_id": "r",
                "kind": "postgresql.mcv",
                "column_ordinals": [1, 2],
                "column_names": ["a", "b"],
                "static_precedence_rank": 1,
                "singleton_qerror": 1.0,
            },
            {
                "candidate_id": "unsupported",
                "relation_id": "r",
                "kind": "postgresql.ndistinct",
                "column_ordinals": [1, 2],
                "column_names": ["a", "b"],
                "static_precedence_rank": 2,
                "singleton_qerror": 1.0,
            },
            {
                "candidate_id": "no-payload",
                "relation_id": "r",
                "kind": "postgresql.dependencies",
                "column_ordinals": [2, 3],
                "column_names": ["b", "c"],
                "static_precedence_rank": 3,
                "singleton_qerror": 0.1,
            },
        ],
    }
    result = build_eligible_universe(
        candidates,
        {"eligible": {"available": True}, "unsupported": {"available": True}},
    )
    assert [item["candidate_id"] for item in result["eligible_candidates"]] == ["eligible"]
    assert set(result["eligible_candidates"][0]) == {
        "candidate_id",
        "relation_id",
        "kind",
        "column_ordinals",
        "column_names",
        "static_precedence_rank",
        "canonical_deployment_position",
    }
    assert "singleton_qerror" not in result["eligible_candidates"][0]
    exclusions = {
        item["candidate"]["candidate_id"]: item["reasons"] for item in result["excluded_candidates"]
    }
    assert exclusions["unsupported"] == ["unsupported-native-statistics-kind"]
    assert exclusions["no-payload"] == ["sample-built-native-payload-unavailable"]
    assert "singleton q-error improvement" in result["eligibility_rule"]["forbidden_signals"]


def test_synthetic_rq4_gate_validates_all_methods_and_tiny_exhaustive_space(
    tmp_path: Path,
) -> None:
    artifact = build_synthetic_rq4_artifact(
        system_freeze=load_system_freeze(), research_commit_sha="a" * 40
    )
    assert artifact["semantic_digest"]
    assert artifact["k_policy"]["fixed_k"] == FIXED_K
    assert artifact["k_policy"]["independent_of_advisor_recommendation"] is True
    assert artifact["validation_outcome"]["exhaustive_combination_count"] == 20
    assert artifact["validation_outcome"]["greedy_optimality_gap"] == 0.0
    assert artifact["formal_confirmatory_experiment"] is False
    assert set(artifact["comparison"]["methods"]) == set(METHOD_IDS)
    assert artifact["comparison"]["method_order"] == list(METHOD_IDS)
    assert artifact["exhaustive_tiny_universe"]["method"]["method"] == ("exhaustive-tiny-universe")
    for method in ("random-k", "workload-frequency-top-k", "dependency-correlation-top-k"):
        result = artifact["comparison"]["methods"][method]
        assert result["selection_planner_evaluations"] == 0
        assert result["information_access_policy"]["ground_truth_during_selection"] is False
    output = tmp_path / "rq4-synthetic.json"
    write_json(output, artifact)
    assert validate_rq4_artifact(output)["status"] == "valid"


def test_rq4_artifact_validator_rejects_policy_drift(tmp_path: Path) -> None:
    source = build_synthetic_rq4_artifact(
        system_freeze=load_system_freeze(), research_commit_sha="a" * 40
    )
    broken = copy.deepcopy(source)
    broken["comparison"]["methods"]["random-k"]["information_access_policy"][
        "ground_truth_during_selection"
    ] = True
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(broken), encoding="utf-8")
    with pytest.raises(RQ4ValidationError, match="semantic digest"):
        validate_rq4_artifact(path)


class _AccountingBackend:
    def evaluate(self, ordered_candidate_ids: tuple[str, ...], *, purpose: str):
        del purpose
        return ConfigurationEvaluation(
            100.0 - float(len(ordered_candidate_ids)),
            planner_query_calls=7,
            wall_clock_seconds=0.25,
        )


def _small_universe() -> dict:
    candidates = [
        {
            "candidate_id": f"c{index}",
            "relation_id": "r",
            "kind": "postgresql.mcv",
            "column_ordinals": [index + 1, index + 2],
            "column_names": [f"c{index + 1}", f"c{index + 2}"],
            "static_precedence_rank": index + 1,
        }
        for index in range(4)
    ]
    return build_eligible_universe(
        {"source_snapshot_semantic_digest": "a" * 64, "candidates": candidates},
        {candidate["candidate_id"]: {"available": True} for candidate in candidates},
    )


def test_rq4_accounting_distinguishes_configuration_and_query_evaluations() -> None:
    universe = _small_universe()
    ids = [candidate["candidate_id"] for candidate in universe["eligible_candidates"]]
    result = run_rq4_ablation(
        eligible_universe=universe,
        backend=_AccountingBackend(),
        fixed_k=FIXED_K,
        mode="fixed_k_quality",
        budget=EvaluationBudget(20, 30.0),
        random_seed=123,
        workload_frequency={candidate_id: 1.0 for candidate_id in ids},
        dependency_correlation={candidate_id: 1.0 for candidate_id in ids},
        singleton_utility={candidate_id: 1.0 for candidate_id in ids},
        singleton_profile_accounting={
            "source": "test-backend",
            "configuration_objective_evaluations": 5,
            "postgresql_planner_query_calls": 35,
            "backend_wall_clock_seconds": 1.25,
        },
    )
    random_result = result["methods"]["random-k"]
    assert random_result["accounting"]["final"]["configuration_objective_evaluations"] == 1
    assert random_result["accounting"]["final"]["postgresql_planner_query_calls"] == 7
    assert random_result["accounting"]["total"]["configuration_objective_evaluations"] == 1
    assert random_result["accounting"]["total"]["postgresql_planner_query_calls"] == 7
    singleton_result = result["methods"]["singleton-utility-top-k"]
    assert singleton_result["accounting"]["selection"]["postgresql_planner_query_calls"] == 35
    assert singleton_result["accounting"]["final"]["postgresql_planner_query_calls"] == 7
    assert singleton_result["accounting"]["total"]["configuration_objective_evaluations"] == 6
    assert singleton_result["accounting"]["total"]["postgresql_planner_query_calls"] == 42


def test_greedy_local_optimum_is_not_infeasible() -> None:
    class NoImprovementBackend:
        def evaluate(self, ordered_candidate_ids: tuple[str, ...], *, purpose: str) -> float:
            del purpose
            return 1.0 + len(ordered_candidate_ids)

    universe = _small_universe()
    ids = [candidate["candidate_id"] for candidate in universe["eligible_candidates"]]
    result = run_rq4_ablation(
        eligible_universe=universe,
        backend=NoImprovementBackend(),
        fixed_k=FIXED_K,
        mode="fixed_k_quality",
        budget=EvaluationBudget(20, 30.0),
        random_seed=123,
        workload_frequency={candidate_id: 1.0 for candidate_id in ids},
        dependency_correlation={candidate_id: 1.0 for candidate_id in ids},
        singleton_utility={candidate_id: 1.0 for candidate_id in ids},
        singleton_profile_accounting={
            "source": "test-backend",
            "configuration_objective_evaluations": 0,
            "postgresql_planner_query_calls": 0,
            "backend_wall_clock_seconds": 0.0,
        },
    )
    greedy = result["methods"]["greedy-ADD"]
    assert greedy["status"] == "at-most-k-local-optimum"
    assert greedy["status"] != "genuinely-infeasible"


def test_singleton_profile_accounting_is_required() -> None:
    universe = _small_universe()
    ids = [candidate["candidate_id"] for candidate in universe["eligible_candidates"]]
    with pytest.raises(RQ4ValidationError, match="singleton utility selection"):
        run_rq4_ablation(
            eligible_universe=universe,
            backend=_AccountingBackend(),
            fixed_k=FIXED_K,
            mode="fixed_k_quality",
            budget=EvaluationBudget(20, 30.0),
            random_seed=123,
            workload_frequency={candidate_id: 1.0 for candidate_id in ids},
            dependency_correlation={candidate_id: 1.0 for candidate_id in ids},
            singleton_utility={candidate_id: 1.0 for candidate_id in ids},
        )


def test_fixed_evaluation_budget_mode_is_explicitly_implementation_needed() -> None:
    universe = _small_universe()
    ids = [candidate["candidate_id"] for candidate in universe["eligible_candidates"]]
    with pytest.raises(RQ4ValidationError, match="implementation-needed"):
        run_rq4_ablation(
            eligible_universe=universe,
            backend=_AccountingBackend(),
            fixed_k=FIXED_K,
            mode="fixed_evaluation_budget",
            budget=EvaluationBudget(20, 30.0),
            random_seed=123,
            workload_frequency={candidate_id: 1.0 for candidate_id in ids},
            dependency_correlation={candidate_id: 1.0 for candidate_id in ids},
            singleton_utility={candidate_id: 1.0 for candidate_id in ids},
            singleton_profile_accounting={
                "source": "test-backend",
                "configuration_objective_evaluations": 0,
                "postgresql_planner_query_calls": 0,
                "backend_wall_clock_seconds": 0.0,
            },
        )
