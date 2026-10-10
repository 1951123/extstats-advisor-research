from pathlib import Path

from extstats_advisor_research.oid_order_review import build_review

ROOT = Path(__file__).parents[1]


def test_oid_order_review_recomputes_all_committed_treatments() -> None:
    review = build_review(ROOT, producer_sha="review-test-producer")

    assert review["invocation"]["dataset_treatment_count"] == 56
    assert review["invocation"]["total_explain_count"] == 560010
    assert review["independent_validation"]["discrepancies"] == []
    assert review["synthetic_witness"]["physical_hypothetical_exact_match_count"] == 5
    assert review["synthetic_witness"]["root_plan_rows"]["a-then-b"] == 303
    assert review["synthetic_witness"]["root_plan_rows"]["b-then-a"] == 281

    by_dataset = {item["dataset_id"]: item for item in review["datasets"]}
    assert set(by_dataset) == {
        "arecel-census13",
        "arecel-forest10",
        "arecel-power7",
        "arecel-dmv11",
    }
    assert by_dataset["arecel-dmv11"]["random_summary"]["advisor_reference_tied_best"] == 2
    for dataset in by_dataset.values():
        assert len(dataset["hypothetical"]) == 7
        assert len(dataset["physical"]) == 7
        assert dataset["physical_control_summary"]["all_requested_oid_orders_observed"]
        assert dataset["physical_control_summary"]["non_reference_statuses"] == [
            "confounded-operational-deployment"
        ]
