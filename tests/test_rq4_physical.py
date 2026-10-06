from __future__ import annotations

from pathlib import Path

import pytest

from extstats_advisor_research.paper_spec import load_paper_spec
from extstats_advisor_research.provenance import semantic_digest, write_json
from extstats_advisor_research.rq4_physical import (
    PHYSICAL_FORMAT,
    _runtime_free,
    union_selected_memberships,
    validate_shared_stock_realization,
)


def _physical_artifact() -> dict:
    artifact = {
        "format_version": PHYSICAL_FORMAT,
        "experiment_id": "rq4-stock-physical-evaluation-smoke-v1",
        "formal_confirmatory_experiment": False,
        "shared_realization": {
            "realization_id": "r" * 64,
            "analyze_count": 1,
            "fresh_state_verified": True,
            "setseed_is_not_analyze_reproducibility_proof": True,
        },
        "workload": {"query_count": 1},
        "methods": {
            "random-k": {
                "shared_analyze_realization_id": "r" * 64,
                "payloads_exactly_preserved_after_drop": True,
                "analyze_after_drop": False,
                "physical_evaluation_source": "stock-postgresql-explain",
                "metrics": {"query_count": 1, "mean": 2.0},
                "costs": {
                    "design_search": "not measured by stock physical executor",
                    "native_realization": {"analyze_count": 1},
                    "evaluation": {"postgresql_planner_query_calls": 1},
                },
            }
        },
    }
    artifact["semantic_digest"] = semantic_digest(_runtime_free(artifact))
    return artifact


def _reseal(artifact: dict) -> dict:
    payload = {key: value for key, value in artifact.items() if key != "semantic_digest"}
    return payload | {"semantic_digest": semantic_digest(_runtime_free(payload))}


def test_physical_validator_requires_explicit_realization_and_separate_costs(
    tmp_path: Path,
) -> None:
    path = tmp_path / "physical.json"
    write_json(path, _physical_artifact())
    assert validate_shared_stock_realization(path)["status"] == "valid"


def test_physical_validator_rejects_sandbox_metric_substitution(tmp_path: Path) -> None:
    artifact = _physical_artifact()
    artifact["methods"]["random-k"]["metrics"]["sandbox_objective"] = 1.0
    path = tmp_path / "broken.json"
    write_json(path, _reseal(artifact))
    with pytest.raises(ValueError, match="sandbox objective"):
        validate_shared_stock_realization(path)


def test_physical_validator_rejects_post_drop_analyze(tmp_path: Path) -> None:
    artifact = _physical_artifact()
    artifact["methods"]["random-k"]["analyze_after_drop"] = True
    path = tmp_path / "broken.json"
    write_json(path, _reseal(artifact))
    with pytest.raises(ValueError, match="ANALYZE"):
        validate_shared_stock_realization(path)


def test_spec_preregisters_stability_replicates_and_keeps_budget_unimplemented() -> None:
    spec = load_paper_spec()
    experiments = {item["experiment_id"]: item for item in spec["experiments"]}
    assert experiments["rq4-native-analyze-stability"]["realizations"] == 5
    assert experiments["rq4-native-analyze-stability"]["status"] == "planned"
    assert experiments["rq4-fixed-evaluation-budget-ablations"]["status"] == (
        "implementation-needed"
    )
    assert union_selected_memberships({"random-k": ["b", "a"]}) == ("a", "b")
