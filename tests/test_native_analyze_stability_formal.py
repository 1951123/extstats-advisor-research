from __future__ import annotations

from extstats_advisor_research.native_analyze_stability_formal import _missing_payloads


def test_missing_native_payload_is_a_scientific_control_failure() -> None:
    realization = {
        "shared_parent": {
            "physical_statistics": [
                {"candidate_id": "cand-present", "payload_present": True},
                {"candidate_id": "cand-missing", "payload_present": False},
            ]
        }
    }

    missing = _missing_payloads(realization)

    assert [item["candidate_id"] for item in missing] == ["cand-missing"]


def test_empty_statistics_inventory_has_no_missing_payloads() -> None:
    assert _missing_payloads({"shared_parent": {"physical_statistics": []}}) == []
