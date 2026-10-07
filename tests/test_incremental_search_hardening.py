from __future__ import annotations

import json

from extstats_advisor_research.incremental_search_hardening import (
    FORMAT_VERSION,
    validate_artifact,
)
from extstats_advisor_research.provenance import semantic_digest


def test_hardening_validator_requires_budgets_empty_incidence_and_deadline(tmp_path) -> None:
    runtime = {
        field: 0
        for field in (
            "baseline_materialization_planner_calls",
            "winner_materialization_planner_calls",
            "proposal_planner_query_calls",
            "audit_planner_query_calls",
            "actual_search_planner_calls",
            "actual_search_planner_calls_including_audit",
            "reference_search_planner_calls",
            "saved_search_planner_calls",
            "search_budget_seconds",
            "elapsed_search_seconds",
            "proposal_only_call_reduction",
            "end_to_end_search_call_reduction",
            "incumbent_estimate_count",
            "peak_proposal_cache_entries",
            "peak_cached_estimate_entries",
        )
    }
    value = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
        "formal_experiment": False,
        "plan_policy": {"tested_budgets": [3, 2, 1]},
        "cases": {
            str(budget): {
                "semantic_equivalence": {"trace": True},
                "incremental": {"runtime_metadata": runtime},
            }
            for budget in (3, 2, 1)
        },
        "explicit_v1_reference": {"semantic_equal_to_bounded_v2_B3": {"trace": True}},
        "empty_incidence_add": {"passed": True, "objective_unchanged": True},
        "deadline_incomplete_round": {"termination_reason": "budget-expired-incomplete-round"},
    }
    value["artifact_digest"] = semantic_digest(value)
    path = tmp_path / "hardening.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert validate_artifact(path)["status"] == "valid"
