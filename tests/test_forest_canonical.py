from __future__ import annotations

import pytest

from extstats_advisor_research.datasets import forest10
from extstats_advisor_research.forest_canonical import (
    compact_summary,
    compare_label_maps,
)
from extstats_advisor_research.postgres.loader import validate_forest_physical_schema
from extstats_advisor_research.runs.layout import run_id


def test_forest_loader_accepts_nullable_catalog_schema() -> None:
    observed = [
        {"name": name, "postgres_type": "double precision", "not_null": False}
        for name, _ in forest10.COLUMNS
    ]
    result = validate_forest_physical_schema(
        observed,
        row_count=forest10.EXPECTED_ROWS,
        extended_statistics_count=0,
    )
    assert result["verified"] is True
    assert result["columns"][0]["postgres_type"] == "DOUBLE PRECISION"

    with pytest.raises(ValueError, match="physical schema"):
        validate_forest_physical_schema(
            [{**item, "not_null": True} for item in observed],
            row_count=forest10.EXPECTED_ROWS,
            extended_statistics_count=0,
        )


def test_full_truth_comparison_is_fail_closed() -> None:
    assert compare_label_maps({"q1": 1, "q2": 2}, {"q1": 1, "q2": 2})["matched"] == 2
    with pytest.raises(ValueError, match="mismatched=1"):
        compare_label_maps({"q1": 1, "q2": 2}, {"q1": 1, "q2": 3})
    with pytest.raises(ValueError, match="missing=1"):
        compare_label_maps({"q1": 1, "q2": 2}, {"q1": 1})


def test_truth_comparison_uses_adapter_query_ids() -> None:
    assert (
        compare_label_maps({"arecel_power7_test_000001": 7}, {"arecel_power7_test_000001": 7})[
            "matched"
        ]
        == 1
    )


def test_forest_run_identity_binds_all_experiment_settings() -> None:
    base = {
        "research_commit_sha": "a" * 40,
        "advisor_commit_sha": "b" * 40,
        "patched_postgres_commit_sha": "c" * 40,
        "benchmark_id": forest10.BENCHMARK_ID,
        "dataset_content_identity": forest10.compute_dataset_content_identity(forest10.CSV_SHA256),
        "workload_id": "arecel_forest10_test_v1",
        "workload_sha256": forest10.WORKLOAD_PICKLE_SHA256,
        "sample_rows": 10_000,
        "sample_seed": 42,
        "statistics_target": 100,
        "candidate_limit": 8,
        "search_wall_clock_seconds": 300,
    }
    assert run_id(base) != run_id({**base, "candidate_limit": 7})
    assert run_id(base) != run_id({**base, "search_wall_clock_seconds": 301})


def test_compact_summary_keeps_candidate_and_search_evidence() -> None:
    manifest = {
        "research_repository": "research",
        "research_commit_sha": "a" * 40,
        "advisor_repository": "advisor",
        "advisor_commit_sha": "b" * 40,
        "patched_postgres_repository": "postgres",
        "patched_postgres_commit_sha": "c" * 40,
        "dataset_content_identity": "dataset",
        "workload_id": "workload",
        "workload_sha256": "d" * 64,
        "label_sha256": "e" * 64,
        "sample_rows": 10_000,
        "sample_seed": 42,
        "statistics_target": 100,
        "candidate_limit": 8,
        "search_wall_clock_seconds": 300,
        "native_repository_semantic_digest": "native",
        "artifacts": {},
    }
    result = compact_summary(
        run_id="run",
        run_directory="runs/run",
        manifest=manifest,
        full_data={"semantic_digest": "full", "summary": {"p50": 2}},
        paper_baseline={"summary": {"p50": 1}},
        candidate_universe={
            "candidates": [{"candidate_id": "c1", "column_names": ["a", "b"]}],
            "incidence": [{"candidate_id": "c1", "query_id": "q1"}],
        },
        native_repository={"candidates": [{"candidate_id": "c1", "state": "present"}]},
        singleton_profile={
            "candidate_profiles": [
                {"candidate_id": "c1", "native_state": "present", "improvement": 1}
            ],
            "best_singleton_candidate_id": "c1",
            "best_singleton_objective": 1,
            "best_singleton_improvement": 1,
        },
        optimization_plan={"candidate_limit": 8, "screened_candidate_count": 1},
        search_result={
            "baseline_objective": 2,
            "final_objective": 1,
            "improvement": 1,
            "relative_improvement": 0.5,
            "termination_reason": "local-optimum",
            "accepted_moves": [],
            "final_ordered_candidate_ids": ["c1"],
        },
        recommendation={
            "decision": "deploy",
            "deployment_ordered_candidate_ids": ["c1"],
            "selected_candidates": [],
        },
        truth_validation={"matched": 10_000, "mismatched": 0, "missing": 0, "extra": 0},
        sampling={"method": "postgresql-system-adaptive-v1"},
        stage_timings={"optimization_search": 3.0},
        audit={"semantic_digest": "audit"},
    )
    assert result["candidates"]["candidate_count"] == 1
    assert result["search"]["termination_reason"] == "local-optimum"
    assert result["deployment"]["performed"] is False
    assert result["source_artifacts"]["audit"]["semantic_digest"] == "audit"

    power = compact_summary(
        run_id="power-run",
        run_directory="runs/power-run",
        manifest=manifest,
        full_data={"semantic_digest": "full", "summary": {"p50": 2}},
        paper_baseline={"summary": {"p50": 1}},
        candidate_universe={"candidates": [], "incidence": []},
        native_repository={"candidates": []},
        singleton_profile={"candidate_profiles": []},
        optimization_plan={"candidate_limit": 8},
        search_result={"final_ordered_candidate_ids": [], "termination_reason": "local-optimum"},
        recommendation={"selected_candidates": []},
        truth_validation={"matched": 10_000, "mismatched": 0, "missing": 0, "extra": 0},
        sampling={"method": "postgresql-system-adaptive-v1"},
        stage_timings={},
        audit={},
        benchmark_id="arecel-power7",
        schema_contract_id="arecel-power7-postgres-schema-v1",
        row_count=2_075_259,
        format_version="arecel-power7-canonical-k8-summary-v1",
        expected_candidate_count=42,
    )
    assert power["dataset"]["benchmark_id"] == "arecel-power7"
    assert power["dataset"]["schema_contract_id"] == "arecel-power7-postgres-schema-v1"
    assert power["dataset"]["row_count"] == 2_075_259
    assert power["candidates"]["expected_candidate_count"] == 42
