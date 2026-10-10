from pathlib import Path

from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq4_time_budget_audit import (
    FORMAT_VERSION,
    build_audit_artifact,
    validate_audit_artifact,
)

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "experiments/rq4-time-budget-fairness-audit-v1.json"


def test_rq4_time_budget_audit_is_independently_bound_to_committed_sources():
    result = validate_audit_artifact(AUDIT, ROOT)
    assert result["status"] == "valid"
    value = build_audit_artifact(ROOT)
    assert value["format_version"] == FORMAT_VERSION
    assert value["semantic_digest"] == semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    assert value["comparison_assessment"]["end_to_end_selection_cost_fairness"] == (
        "not-established"
    )
    assert value["fixed_evaluation_budget_recommendation"]["classification"] == (
        "Conditionally useful"
    )
    assert value["ks_sensitivity_evidence"]["screening_widths"] == [4, 8, 16, 32, "all"]
    assert value["ks_sensitivity_evidence"]["fixed_B"] == 4


def test_rq4_time_budget_audit_does_not_turn_unmeasured_time_into_zero():
    value = build_audit_artifact(ROOT)
    for dataset in ("arecel-census13", "arecel-power7", "arecel-dmv11"):
        methods = value["datasets"][dataset]["methods"]
        cheap = next(item for item in methods if item["method_id"] == "random-k-seed-1")
        singleton = next(item for item in methods if item["method_id"] == "singleton-utility-top-k")
        assert cheap["selection"]["wall_clock_seconds"] is None
        assert singleton["selection"]["wall_clock_seconds"] is None


def test_rq4_time_budget_audit_preserves_forest10_historical_budget_exception():
    value = build_audit_artifact(ROOT)
    assert value["datasets"]["arecel-forest10"]["selection_budget"]["wall_clock_seconds"] == 3600.0
    assert (
        value["datasets"]["arecel-forest10"]["selection_budget"]["final_evaluation_excluded"]
        is None
    )
    greedy = next(
        item
        for item in value["datasets"]["arecel-forest10"]["methods"]
        if item["method_id"] == "greedy-ADD"
    )
    assert greedy["status"] == "complete"
    assert greedy["selected_k"] == 4
