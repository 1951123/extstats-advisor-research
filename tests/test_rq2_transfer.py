from __future__ import annotations

import json

import pytest

from extstats_advisor_research.full_data_transfer import build_transfer_records, summarize_transfer
from extstats_advisor_research.rq2_transfer import (
    K_S,
    RQ2_DATASETS,
    SETSEED_SQL,
    B,
    _spearman,
    _transfer_metrics,
    preflight_rq2,
    validate_rq2_artifact,
)
from extstats_advisor_research.system_freeze_v2 import formal_system_freeze_v2_identity


def test_rq2_protocol_uses_frozen_parameters_and_float_setseed() -> None:
    assert RQ2_DATASETS == (
        "arecel-census13",
        "arecel-forest10",
        "arecel-power7",
        "arecel-dmv11",
    )
    assert K_S == 8
    assert B == 8
    assert SETSEED_SQL == "SELECT setseed(1.0 / 123)"
    assert "1/123" not in SETSEED_SQL


def test_rq2_preflight_rejects_unknown_dataset() -> None:
    with pytest.raises(ValueError, match="unsupported RQ2 dataset"):
        preflight_rq2("not-a-dataset")


def test_spearman_is_rank_based_and_handles_constant_series() -> None:
    assert _spearman([1.0, 2.0, 3.0], [30.0, 20.0, 10.0]) == pytest.approx(-1.0)
    assert _spearman([1.0, 1.0], [2.0, 3.0]) is None


def test_rq2_transfer_metrics_keep_sample_and_full_effects_distinct() -> None:
    truth = [
        {"query_id": "q1", "cardinality": 10},
        {"query_id": "q2", "cardinality": 20},
        {"query_id": "q3", "cardinality": 30},
    ]
    sample = {
        "q1": {
            "baseline_estimate": 1,
            "final_estimate": 10,
            "baseline_qerror": 10.0,
            "final_qerror": 1.0,
        },
        "q2": {
            "baseline_estimate": 20,
            "final_estimate": 20,
            "baseline_qerror": 1.0,
            "final_qerror": 1.0,
        },
        "q3": {
            "baseline_estimate": 60,
            "final_estimate": 90,
            "baseline_qerror": 2.0,
            "final_qerror": 3.0,
        },
    }
    estimates = {
        "q1": {"p0": 1, "p1": 10, "p2": 1},
        "q2": {"p0": 20, "p1": 20, "p2": 20},
        "q3": {"p0": 30, "p1": 30, "p2": 60},
    }
    records = build_transfer_records(truth, sample, estimates)
    summary = summarize_transfer(records)
    metrics = _transfer_metrics(records, summary)
    assert metrics["sample"]["J_S_empty"] != metrics["full_data"]["J_D_P0"]
    assert metrics["full_data"]["J_D_P2"] != metrics["full_data"]["J_D_P1"]
    assert set(metrics["transfer"]["contingency_3x3"]) == {
        "improved",
        "unchanged",
        "worsened",
    }


def test_rq2_validator_rejects_non_formal_or_wrong_freeze(tmp_path) -> None:
    path = tmp_path / "artifact.json"
    path.write_text(json.dumps({"format_version": "arecel-full-data-transfer-v1"}))
    with pytest.raises(ValueError, match="unsupported RQ2 formal artifact"):
        validate_rq2_artifact(path)

    base = {
        "format_version": "rq2-transfer-v1",
        "formal_experiment": "rq2-formal-sample-to-full-transfer-v1",
        "execution_status": "blocked",
        "system_freeze": formal_system_freeze_v2_identity(),
        "producer_research_sha": "a" * 40,
        "advisor_sha": "b" * 40,
        "patched_postgres_sha": "c" * 40,
        "stock_postgres_sha": "d" * 40,
        "truth_source_kind": "production-exact-execution",
    }
    path.write_text(json.dumps(base))
    with pytest.raises(ValueError, match="Advisor SHA"):
        validate_rq2_artifact(path)
