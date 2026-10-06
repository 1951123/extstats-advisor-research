from __future__ import annotations

from pathlib import Path

import pytest

from extstats_advisor_research.provenance import write_json
from extstats_advisor_research.rq4_determinism import (
    build_design_determinism_smoke_artifact,
    validate_design_determinism_smoke,
)
from extstats_advisor_research.rq4_physical import (
    union_selected_memberships,
    validate_method_union,
)
from extstats_advisor_research.system_freeze import load_system_freeze


def _comparison(runtime: float = 0.1) -> dict:
    return {
        "method_order": ["random-k"],
        "fixed_k": 1,
        "methods": {
            "random-k": {
                "selected_membership": ["c1"],
                "evaluation_order": ["c1"],
                "deployment_order": ["c1"],
                "status": "complete",
                "selection_trace": [],
                "evaluation": {"status": "complete", "sandbox_objective": 2.0},
                "accounting": {
                    "configuration_objective_evaluations": 1,
                    "postgresql_planner_query_calls": 3,
                    "backend_wall_clock_seconds": runtime,
                },
                "selection_preprocessing": {"wall_clock_seconds": runtime},
                "trace": [{"planner_estimates": {"q1": 3}, "objective": 2.0}],
            }
        },
    }


def test_design_replay_ignores_runtime_but_compares_planner_evidence(tmp_path: Path) -> None:
    calls = 0

    def run_once() -> dict:
        nonlocal calls
        calls += 1
        return _comparison(float(calls))

    artifact = build_design_determinism_smoke_artifact(
        run_once=run_once,
        candidate_universe_digest="a" * 64,
        system_freeze=load_system_freeze(),
        research_commit_sha="b" * 40,
        fixture={"query_count": 1},
    )
    path = tmp_path / "replay.json"
    write_json(path, artifact)
    assert validate_design_determinism_smoke(path)["status"] == "valid"
    assert artifact["replay_semantic_digests"][0] == artifact["replay_semantic_digests"][1]

    compressed_path = tmp_path / "replay.json.gz"
    write_json(compressed_path, artifact)
    assert validate_design_determinism_smoke(compressed_path)["status"] == "valid"


def test_design_replay_rejects_planner_estimate_drift() -> None:
    calls = 0

    def run_once() -> dict:
        nonlocal calls
        calls += 1
        result = _comparison(float(calls))
        result["methods"]["random-k"]["trace"][0]["planner_estimates"]["q1"] = calls
        return result

    with pytest.raises(ValueError, match="deterministic replay mismatch"):
        build_design_determinism_smoke_artifact(
            run_once=run_once,
            candidate_universe_digest="a" * 64,
            system_freeze=load_system_freeze(),
            research_commit_sha="b" * 40,
            fixture={"query_count": 1},
        )


def test_shared_union_is_method_order_independent_and_covers_all_memberships() -> None:
    selected = {"random-k": ["c2", "c1"], "greedy-ADD": ["c3"]}
    assert union_selected_memberships(selected) == ("c1", "c2", "c3")
    validate_method_union(selected, ("c1", "c2", "c3"))
    with pytest.raises(ValueError, match="union"):
        validate_method_union(selected, ("c1", "c2"))
