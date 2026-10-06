from __future__ import annotations

import json
import os

import pytest

from extstats_advisor_research import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq3_fidelity import (
    MISMATCH_CATEGORIES,
    build_fidelity_artifact,
    classify_plan_rows_mismatch,
    inspect_fidelity_artifact,
    paired_plan_rows_metrics,
    run_synthetic_fidelity,
    validate_fidelity_artifact,
    write_fidelity_artifact,
)


def _artifact() -> dict:
    digest = "a" * 64
    return build_fidelity_artifact(
        experiment_id="rq3-synthetic-test",
        system={
            "research_commit_sha": digest,
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "patched_backend_contract": "postgresql-pgextadv-16.14-v1",
            "patched_server_version": "16.14",
        },
        fixture={"fixture_id": "fixture", "workload_digest": digest},
        settings={"statistics_target": 100},
        physical={
            "candidate_ids": ["mcv", "fd"],
            "ordinary_stats_fingerprint": digest,
            "payload_correspondence": "exact-bytes-from-physical-source",
        },
        hypothetical={
            "active_candidate_ids": ["mcv", "fd"],
            "ordinary_stats_fingerprint": digest,
            "payload_source": "physical-extracted-payloads",
        },
        paired_queries=[
            {
                "query_id": "q1",
                "physical_plan_rows": 10,
                "hypothetical_plan_rows": 10,
                "physical_explain_sha256": digest,
                "hypothetical_explain_sha256": digest,
                "mismatch_evidence": {
                    "payload_correspondence": True,
                    "ordinary_stats_equal": True,
                    "statistics_order_equal": True,
                    "planner_settings_equal": True,
                    "physical_object_selection_equal": True,
                    "overlay_resolution_equal": True,
                },
            }
        ],
        controls={
            "identical_binary": True,
            "identical_relation_contents": True,
            "identical_schema": True,
            "identical_workload": True,
            "ordinary_stats_equal": True,
            "identical_statistics_target": True,
            "equivalent_design": True,
            "payload_correspondence_verified": True,
            "identical_planner_settings": True,
            "physical_object_selection_equal": True,
            "overlay_resolution_verified": True,
        },
        cleanup={
            "verified": True,
            "physical_extstats_count_after": 0,
            "overlay_active_after": None,
        },
    )


def test_plan_rows_metrics_are_direct_and_exact() -> None:
    records = [
        {
            "query_id": "q1",
            "physical_plan_rows": 0,
            "hypothetical_plan_rows": 2,
            "exact_match": False,
            "absolute_delta": 2,
            "relative_delta": 2.0,
            "mismatch_category": "plan-rows-mismatch-unexplained",
        },
        {
            "query_id": "q2",
            "physical_plan_rows": 4,
            "hypothetical_plan_rows": 4,
            "exact_match": True,
            "absolute_delta": 0,
            "relative_delta": 0.0,
            "mismatch_category": "exact-match",
        },
    ]
    result = paired_plan_rows_metrics(records)
    assert result["query_count"] == 2
    assert result["exact_match_fraction"] == 0.5
    assert result["max_absolute_delta"] == 2


def test_mismatch_classification_requires_evidence() -> None:
    evidence = {
        "payload_correspondence": True,
        "ordinary_stats_equal": False,
        "statistics_order_equal": True,
        "planner_settings_equal": True,
        "physical_object_selection_equal": True,
        "overlay_resolution_equal": True,
    }
    assert classify_plan_rows_mismatch(5, 4, evidence) == "ordinary-stats-drift"
    with pytest.raises(ValueError, match="missing boolean field"):
        classify_plan_rows_mismatch(5, 4, {"payload_correspondence": True})


def test_fidelity_artifact_round_trips_and_digest_is_stable(tmp_path) -> None:
    artifact = _artifact()
    path = tmp_path / "rq3-fidelity-v1.json"
    write_fidelity_artifact(path, artifact)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert validate_fidelity_artifact(loaded) == artifact["summary"]
    assert inspect_fidelity_artifact(path)["semantic_digest"] == artifact["semantic_digest"]


def test_fidelity_artifact_fails_closed_on_digest_or_cleanup_drift() -> None:
    artifact = _artifact()
    broken = {**artifact, "summary": {"query_count": 0}}
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_fidelity_artifact(broken)
    broken = {**artifact, "cleanup": {**artifact["cleanup"], "verified": False}}
    broken["semantic_digest"] = semantic_digest(
        {key: value for key, value in broken.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="cleanup"):
        validate_fidelity_artifact(broken)


def test_categories_are_closed() -> None:
    assert "exact-match" in MISMATCH_CATEGORIES
    assert "plan-rows-mismatch-unexplained" in MISMATCH_CATEGORIES


@pytest.mark.integration
def test_synthetic_mcv_fd_live_fixture(tmp_path) -> None:
    dsn = os.environ.get("EXTSTATS_RQ3_PATCHED_DSN")
    if not dsn:
        pytest.skip("set EXTSTATS_RQ3_PATCHED_DSN to run the small patched-PostgreSQL fixture")
    result = run_synthetic_fidelity(
        dsn=dsn,
        output=tmp_path / "rq3-fidelity-v1.json",
    )
    assert result["status"] == "ready-to-run"
    assert result["summary"]["mismatch_count"] == 0
