from __future__ import annotations

import copy

import pytest

from extstats_advisor_research.rq1_canary import (
    ARM_IDS,
    build_rq1_artifact,
    validate_rq1_artifact,
)
from extstats_advisor_research.system_freeze import load_system_freeze


def _build_identity(sha: str, role: str) -> dict[str, object]:
    return {
        "format_version": "postgres-lab-instance-v1",
        "role": role,
        "source_commit_sha": sha,
        "postgres_version": "16.14",
        "locale": "C.utf8",
        "encoding": "UTF8",
        "build_identity_digest": "a" * 64,
    }


def _artifact() -> dict[str, object]:
    freeze = load_system_freeze()
    workload = {
        "workload_id": "arecel_census13_test_v1",
        "sha256": "b" * 64,
        "query_count": 2,
        "canonical_source_sha256": "c" * 64,
    }
    truth = {
        "source_kind": "production-exact-execution",
        "semantic_digest": "2cb7c9e89d020581c8a632c20a3a971cee3fd7e44a6a67e91bcea28d57038509",
        "source_snapshot_semantic_digest": "d" * 64,
        "source_view_token": "765:765:",
        "workload_id": "arecel_census13_test_v1",
        "query_count": 2,
    }
    records = [
        {"query_id": "q0", "estimate": 10, "truth": 10, "weight": 1.0, "qerror": 1.0},
        {"query_id": "q1", "estimate": 0, "truth": 2, "weight": 1.0, "qerror": 2.0},
    ]
    arms = {}
    for arm_id in ARM_IDS:
        arm = {
            "evaluation_mode": (
                "stock-full-data-deployment"
                if arm_id == "pg16-advisor"
                else "stock-full-data-physical"
            ),
            "headline_source": "full_data_evaluation_metrics",
            "deployment_build_identity": _build_identity(
                "0d1c00c624fa7367d4a895f44381887757289682", "stock"
            ),
            "design_time_build_identity": _build_identity(
                "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6", "patched"
            ),
            "workload_identity": workload,
            "truth_identity": truth,
            "physical_extended_statistics": [],
            "physical_extended_statistics_count": 0,
            "per_query_artifact": {"logical_path": f"raw/{arm_id}.jsonl", "sha256": "e" * 64},
            "per_query": copy.deepcopy(records),
        }
        if arm_id == "pg16-advisor":
            arm["sandbox_optimization_objective"] = {
                "contract": "weighted-workload-mean-v1",
                "final": 1.5,
            }
        arms[arm_id] = arm
    return build_rq1_artifact(
        research_commit_sha="f" * 40,
        system_freeze=freeze,
        dataset={"dataset_id": "arecel-census13", "content_identity": "g" * 64},
        workload=workload,
        truth=truth,
        arms=arms,
        cleanup={"clusters_stopped": True},
    )


def test_rq1_arms_share_workload_and_truth() -> None:
    assert validate_rq1_artifact(_artifact())["status"] == "valid"


def test_rq1_rejects_stock_build_mismatch() -> None:
    broken = _artifact()
    broken["per_arm"]["pg16-default"]["deployment_build_identity"]["source_commit_sha"] = "0" * 40
    with pytest.raises(ValueError, match="frozen source SHA"):
        validate_rq1_artifact(broken)


def test_rq1_rejects_extended_statistics_leak() -> None:
    broken = _artifact()
    broken["per_arm"]["pg16-target10000"]["physical_extended_statistics_count"] = 1
    with pytest.raises(ValueError, match="extended-statistics leak"):
        validate_rq1_artifact(broken)


def test_rq1_requires_full_data_advisor_headline() -> None:
    broken = _artifact()
    broken["per_arm"]["pg16-advisor"]["evaluation_mode"] = "patched-sandbox"
    broken["semantic_digest"] = "x" * 64
    with pytest.raises(ValueError, match="full-data stock deployment"):
        validate_rq1_artifact(broken)


def test_rq1_cannot_mark_global_campaign_complete_early() -> None:
    broken = _artifact()
    broken["experiment_status"] = "complete"
    broken["semantic_digest"] = "x" * 64
    with pytest.raises(ValueError, match="datasets remain incomplete"):
        validate_rq1_artifact(broken)
