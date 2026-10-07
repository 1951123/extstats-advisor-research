from __future__ import annotations

import json

import pytest

from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.singleton_equivalence import (
    FORMAT_VERSION,
    HISTORICAL_INCREMENTAL_FORMAT_VERSION,
    _summary,
    validate_artifact,
    validate_historical_incremental_artifact,
)


def test_preflight_incidence_summary_is_deterministic() -> None:
    assert _summary((1, 2, 3, 4)) == {
        "min": 1,
        "mean": 2.5,
        "median": 2.5,
        "p95": 4.0,
        "max": 4,
    }


def test_singleton_equivalence_validator_requires_all_semantic_gates(tmp_path) -> None:
    value = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
        "dataset_id": "fixture",
        "equivalence": {
            "semantic_output_equal": True,
            "candidate_profiles_equal": True,
            "frozen_order_equal": True,
            "baseline_objective_equal": True,
            "audit": {"all_nonincident_estimates_unchanged": True},
        },
        "incremental": {"runtime_metadata": {"planner_query_estimate_count": 1}},
    }
    value["artifact_digest"] = semantic_digest(value)
    path = tmp_path / "equivalence.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert validate_artifact(path)["status"] == "valid"

    tampered = dict(value)
    tampered["equivalence"] = dict(value["equivalence"])
    tampered["equivalence"]["frozen_order_equal"] = False
    tampered_path = tmp_path / "tampered.json"
    tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_artifact(tampered_path)


def test_historical_incremental_validator_requires_all_current_gates(tmp_path) -> None:
    value = {
        "format_version": HISTORICAL_INCREMENTAL_FORMAT_VERSION,
        "status": "complete",
        "dataset_id": "arecel-forest10",
        "equivalence": {
            "historical_identity_validated": True,
            "historical_profile_oracle_digest_equal": True,
            "semantic_output_equal": True,
            "candidate_profiles_equal": True,
            "frozen_order_equal": True,
            "baseline_objective_equal": True,
            "runtime_incremental_call_count_equal": True,
            "audit": {"all_nonincident_estimates_unchanged": True},
        },
    }
    value["artifact_digest"] = semantic_digest(value)
    path = tmp_path / "historical-incremental.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    assert validate_historical_incremental_artifact(path)["status"] == "valid"
