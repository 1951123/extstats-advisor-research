from __future__ import annotations

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.forest_transfer import (
    _decision,
    validate_audited_truth_binding,
)


def _summary(p0: float, p1: float, p2: float) -> dict:
    return {
        "p0": {"weighted_objective": p0},
        "p1": {"weighted_objective": p1},
        "p2": {"weighted_objective": p2},
    }


def test_forest_transfer_decision_rules() -> None:
    assert _decision(_summary(7.0, 6.0, 6.5)) == "forest-k8-transfer-success"
    assert _decision(_summary(7.0, 6.0, 6.0)) == "forest-k8-transfer-neutral"
    assert _decision(_summary(7.0, 7.0, 6.0)) == "forest-k8-transfer-regression"


def test_forest_truth_binding_requires_all_audited_labels() -> None:
    truth = {f"q{i:05d}": i for i in range(10_000)}
    audit = {query_id: {"true_rows": rows} for query_id, rows in truth.items()}
    assert validate_audited_truth_binding(truth, audit) == {
        "query_count": 10_000,
        "matched": 10_000,
        "mismatched": 0,
    }
    audit["q00042"]["true_rows"] = -1
    with pytest.raises(ValueError, match="labels differ"):
        validate_audited_truth_binding(truth, audit)


def test_forest_truth_binding_rejects_missing_query() -> None:
    truth = {f"q{i:05d}": i for i in range(10_000)}
    audit = {query_id: {"true_rows": rows} for query_id, rows in truth.items()}
    audit.pop("q00042")
    with pytest.raises(ValueError, match="query IDs"):
        validate_audited_truth_binding(truth, audit)


def test_full_data_transfer_cli_accepts_forest_without_budget_directory() -> None:
    args = _parser().parse_args(
        [
            "validate",
            "full-data-transfer",
            "runs/3a8737b6c3184ae2037100df",
            "--production-dsn",
            "postgresql://not-a-real-credential",
        ]
    )
    assert args.budget_directory is None
    assert args.planner_dsn is None
