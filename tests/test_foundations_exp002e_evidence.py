from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parents[1] / "experiments" / "foundations-interaction-landscape-v1"


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


def test_exp002e_bootstrap_evidence_binds_the_frozen_artifacts() -> None:
    evidence = _load("exp-002e-bootstrap-v1.json")
    assert evidence["classification"] == "integration-readiness-only"
    assert evidence["status"] == "success"
    assert evidence["historical_advisor_binding"] == ("0865c5a6afb8bc176bd7d3b10b13b3da83f1f641")
    assert evidence["database"]["name"] == "extstats_stock"
    assert evidence["sandbox"]["sample_rows"] == 30000
    assert evidence["sandbox"]["population_metadata_rows"] == 581012
    assert evidence["sandbox"]["repository_candidates"] == 57
    assert evidence["sandbox"]["present_payloads"] == 57
    assert evidence["sandbox"]["absent_payloads"] == 0
    assert evidence["artifacts"] == {
        "snapshot": "ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5",
        "candidate_universe": "292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134",
        "native_repository": "fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184",
        "ground_truth": "bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2",
        "workload": "arecel_forest10_test_v1",
    }


def test_exp002e_smoke_evidence_rejects_partial_results_as_success() -> None:
    evidence = _load("exp-002e-smoke-v1.json")
    assert evidence["classification"] == "smoke-test"
    assert evidence["status"] == "failed-partial"
    assert evidence["historical_advisor_binding"] == ("0865c5a6afb8bc176bd7d3b10b13b3da83f1f641")
    assert evidence["failure"]["smoke_valid"] is False
    assert evidence["failure"]["exp003_ready"] is False
    assert evidence["primary_planner_calls"] == {
        "expected": 30000,
        "attempted": 20000,
        "succeeded": 20000,
    }
    assert evidence["observations"][2]["planner_calls_attempted"] == 0
    assert evidence["cleanup"]["sandbox_destroyed"] is True
    assert evidence["cleanup"]["post_cleanup_target_relation"] is False
