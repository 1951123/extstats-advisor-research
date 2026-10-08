from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.rq5_static_deployment_cost import (
    DATASETS,
    PROTOCOL_DIGEST,
    PROTOCOL_FORMAT,
    _source_projection,
    _validate_dataset_raw,
    validate_protocol,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "paper/rq5-static-deployment-cost-protocol-v1.json"


def _protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def test_static_protocol_is_preregistered_and_valid() -> None:
    result = validate_protocol(PROTOCOL_PATH)
    assert result == {
        "status": "valid",
        "format_version": PROTOCOL_FORMAT,
        "semantic_digest": PROTOCOL_DIGEST,
    }
    protocol = _protocol()
    assert protocol["dataset_order"] == list(DATASETS)
    assert protocol["repetition_order"] == [1, 2, 3]
    assert protocol["repetitions"] == 3
    assert protocol["system"]["patched_postgres_used"] is False
    assert protocol["system"]["advisor_selection_used"] is False


def test_projection_comes_from_immutable_deployment_artifacts() -> None:
    expected_counts = {
        "arecel-census13": 7,
        "arecel-forest10": 8,
        "arecel-power7": 7,
        "arecel-dmv11": 5,
    }
    for dataset_id in DATASETS:
        projection = _source_projection(ROOT, dataset_id)
        assert projection["actual_selected_k"] == expected_counts[dataset_id]
        assert len(projection["objects"]) == expected_counts[dataset_id]
        assert projection["selected_candidate_ids"] == projection["deployment_order"]
        assert all(item["statistics_target"] == 100 for item in projection["objects"])
        assert all(
            item["deployment_order_position"] == index
            for index, item in enumerate(projection["objects"], 1)
        )


def test_protocol_keeps_ddl_and_analyze_timers_separate() -> None:
    protocol = _protocol()
    assert "ANALYZE" in protocol["stages"]["ddl"]["excludes"]
    assert "storage queries" in protocol["stages"]["ddl"]["excludes"]
    assert "CREATE STATISTICS" in protocol["stages"]["analyze"]["excludes"]
    assert protocol["stages"]["ddl_verification"]["charged_to_ddl"] is False
    assert protocol["stages"]["payload_verification"]["charged_to_analyze"] is False


def _raw_fixture(dataset_id: str = "arecel-power7") -> dict:
    projection = _source_projection(ROOT, dataset_id)
    repetition = {
        "repetition_id": 1,
        "statistics_target": 100,
        "ddl": {"elapsed_seconds": 1.0, "committed": True},
        "analyze": {"elapsed_seconds": 2.0, "completed": True, "payload_verification_passed": True},
        "derived": {"direct_combined_wall_clock_measurement": False},
        "cleanup": {"database_dropped": True},
    }
    return {
        "status": "complete",
        "dataset_id": dataset_id,
        "source_rq2_digest": projection["source_rq2_digest"],
        "source_deployment_digest": projection["source_deployment_digest"],
        "stock_postgresql_sha": "0d1c00c624fa7367d4a895f44381887757289682",
        "no_advisor_selection": True,
        "repetitions": [repetition],
    }


def test_zero_physical_delta_is_valid_and_storage_views_are_not_summed() -> None:
    raw = _raw_fixture()
    _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))
    assert "total_storage" not in raw["repetitions"][0]
    raw["repetitions"][0]["physical_catalog_allocation_delta"] = 0
    _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))


def test_storage_and_refresh_semantics_are_fail_closed() -> None:
    raw = _raw_fixture()
    raw["repetitions"][0]["total_storage"] = 1
    with pytest.raises(ValueError, match="logical and physical storage"):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))
    protocol = _protocol()
    assert "refresh measurement" in protocol["non_goals"]
    assert "snapshot byte measurement" in protocol["non_goals"]


def test_raw_stage_hard_cap_rejects_overlong_measurement() -> None:
    raw = _raw_fixture()
    raw["repetitions"][0]["analyze"]["elapsed_seconds"] = 300.001
    with pytest.raises(ValueError, match="ANALYZE cap"):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))


def test_mutated_source_provenance_is_rejected() -> None:
    raw = _raw_fixture()
    raw["source_deployment_digest"] = "0" * 64
    with pytest.raises(ValueError, match="deployment source"):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))


def test_static_cli_has_stock_only_arguments() -> None:
    args = _parser().parse_args(
        [
            "validate",
            "rq5-cost",
            "static-deployment",
            "run",
            "--dataset",
            "arecel-power7",
            "--stock-dsn",
            "dbname=postgres",
            "--output",
            "raw.json",
        ]
    )
    assert args.rq5_static_command == "run"
    assert args.dataset == "arecel-power7"
    assert not hasattr(args, "patched_dsn")
    assert not hasattr(args, "advisor_root")
    assert not hasattr(args, "truth_artifact")
