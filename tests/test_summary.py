from __future__ import annotations

import json

from extstats_advisor_research.analysis.summary import extract_summary
from extstats_advisor_research.runs.layout import create_layout


def test_summary_extracts_production_artifact_fields(tmp_path) -> None:
    layout = create_layout(tmp_path, {"benchmark_id": "fixture"})
    paths = layout.artifacts()
    payloads = {
        "candidate_universe": {"candidates": [{"candidate_id": "cand_a"}]},
        "singleton_profile": {
            "profiles": [{"state": "PRESENT"}],
            "best_singleton": {"id": "cand_a"},
        },
        "optimization_plan": {"candidate_limit": 1, "screened_candidate_count": 1},
        "search_result": {
            "final_objective": 0.5,
            "termination_reason": "fixed_point",
            "selected_candidate_ids": ["cand_a"],
        },
        "recommendation": {"decision": "recommend", "deployment_order": ["cand_a"]},
    }
    for name, value in payloads.items():
        paths[name].write_text(json.dumps(value), encoding="utf-8")
    paths["snapshot"].mkdir()
    (paths["snapshot"] / "manifest.json").write_text(
        json.dumps(
            {
                "sample_row_counts": {"public.census13": 10},
                "population": [{"relation_id": "public_census13", "row_count": 100}],
            }
        ),
        encoding="utf-8",
    )
    summary = extract_summary(
        layout,
        benchmark_id="fixture",
        workload={"workload_id": "fixture-test", "queries": [{"query_id": "q1"}]},
    )
    assert summary["query_count"] == 1
    assert summary["recommendation"]["order"] == ["cand_a"]
    assert summary["candidate_states"]["PRESENT"] == 1
