from __future__ import annotations

from pathlib import Path

import pytest

import extstats_advisor_research.rq5_snapshot_footprint as footprint


def _snapshot_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "samples").mkdir(parents=True)
    for name, data in {
        "manifest.json": b"{}",
        "schema.json": b"schema",
        "population.json": b"population",
        "workload.json": b"workload",
        "samples/part-0.arrow": b"arrow-0",
        "samples/part-1.arrow": b"arrow-1-long",
        "extra.bin": b"validated-extra",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "empty-directory").mkdir()
    return root


def test_measurement_counts_nested_regular_files_and_ignores_directories(tmp_path, monkeypatch):
    root = _snapshot_fixture(tmp_path)
    monkeypatch.setattr(
        footprint,
        "_advisor_validate_snapshot",
        lambda path: {"format_version": "advisor-snapshot-v1", "semantic_digest": "a" * 64},
    )

    result = footprint.measure_snapshot_footprint(root)
    files = result["per_file_inventory"]
    assert [item["relative_path"] for item in files] == sorted(
        item["relative_path"] for item in files
    )
    assert result["component_bytes"]["sample_payload_bytes_total"] == len(b"arrow-0") + len(
        b"arrow-1-long"
    )
    assert result["component_bytes"]["sealed_snapshot_logical_bytes"] == sum(
        item["logical_bytes"] for item in files
    )
    assert result["component_bytes"]["regular_file_count"] == len(files)
    assert (
        next(item for item in files if item["relative_path"] == "extra.bin")["component_role"]
        == "other-validated-component"
    )


def test_measurement_rejects_symlink(tmp_path, monkeypatch):
    root = _snapshot_fixture(tmp_path)
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    try:
        (root / "samples" / "escape.arrow").symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable")
    monkeypatch.setattr(
        footprint,
        "_advisor_validate_snapshot",
        lambda path: {"format_version": "advisor-snapshot-v1", "semantic_digest": "a" * 64},
    )
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="symlink"):
        footprint.measure_snapshot_footprint(root)


def test_manifest_only_or_missing_component_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        footprint,
        "_advisor_validate_snapshot",
        lambda path: {"format_version": "advisor-snapshot-v1", "semantic_digest": "a" * 64},
    )
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="required JSON"):
        footprint.measure_snapshot_footprint(root)


def test_capture_command_has_no_truth_output_or_planner_work(tmp_path):
    command = footprint._capture_command(
        "extstats-advisor",
        target_dsn="dbname=test",
        relation="public.t",
        workload_path=tmp_path / "workload.json",
        snapshot_path=tmp_path / "snapshot",
    )
    assert "--ground-truth-output" not in command
    assert "EXPLAIN" not in command
    assert command[1:4] == ["snapshot", "capture", "postgres"]


def test_outside_paths_fail_closed_before_any_work(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError,
        match="must reside inside the research repository",
    ):
        footprint._canonical_repo_path(root, tmp_path / "outside.json", label="output")
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError,
        match="must reside inside the research repository",
    ):
        footprint._canonical_repo_path(root, Path("../outside.json"), label="preflight")


def _repetition_fixture(producer: str = "a" * 40) -> dict:
    files = [
        {
            "relative_path": name,
            "logical_bytes": size,
            "sha256": "b" * 64,
            "component_role": role,
        }
        for name, size, role in (
            ("manifest.json", 1, "manifest"),
            ("schema.json", 2, "schema"),
            ("population.json", 3, "population"),
            ("workload.json", 4, "workload"),
            ("samples/part.arrow", 5, "sample-payload"),
        )
    ]
    files.sort(key=lambda item: item["relative_path"])
    return {
        "format_version": "rq5-snapshot-footprint-repetition-v1",
        "status": "complete",
        "dataset_id": "arecel-power7",
        "repetition_id": 1,
        "research_commit_sha": producer,
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "protocol_path": footprint.PROTOCOL_PATH,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": "e" * 64,
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "relation": "public.t",
        "dataset_identity": "power7",
        "snapshot_capture_elapsed_seconds": 1.0,
        "snapshot_semantic_digest": "d" * 64,
        "component_bytes": {
            "manifest_json_bytes": 1,
            "schema_json_bytes": 2,
            "population_json_bytes": 3,
            "workload_json_bytes": 4,
            "sample_payload_bytes_total": 5,
            "sealed_snapshot_logical_bytes": 15,
            "regular_file_count": 5,
        },
        "per_file_inventory": files,
        "validation_passed": True,
        "cleanup_passed": True,
        "no_truth_acquisition": True,
        "no_planner_evaluation": True,
        "no_candidate_or_native_work": True,
    }


def _raw_fixture(producer: str = "a" * 40) -> dict:
    repetitions = []
    for repetition_id in (1, 2, 3):
        repetition = _repetition_fixture(producer)
        repetition["repetition_id"] = repetition_id
        repetitions.append(repetition)
    return {
        "format_version": "rq5-snapshot-footprint-dataset-v1",
        "status": "complete",
        "dataset_id": "arecel-power7",
        "research_commit_sha": producer,
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "protocol_path": footprint.PROTOCOL_PATH,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": "e" * 64,
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "relation": "public.t",
        "dataset_identity": "power7",
        "repetitions": repetitions,
        "validation_passed": True,
        "cleanup_passed": True,
        "no_truth_acquisition": True,
        "no_planner_evaluation": True,
        "no_candidate_or_native_work": True,
    }


def test_three_repetition_raw_schema_validates_and_sensitive_paths_are_rejected():
    raw = _raw_fixture()
    assert (
        footprint.validate_raw_artifact(raw, expected_producer_sha="a" * 40)["repetition_count"]
        == 3
    )
    raw["repetitions"][0]["snapshot_path"] = "/tmp/secret-snapshot"
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="sensitive"):
        footprint.validate_raw_artifact(raw, expected_producer_sha="a" * 40)


def test_raw_validation_rejects_manifest_only_metric_and_filesystem_allocation():
    raw = _raw_fixture()
    raw["repetitions"][0]["component_bytes"]["sealed_snapshot_logical_bytes"] = 10
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="total"):
        footprint.validate_raw_artifact(raw)
    raw = _raw_fixture()
    raw["repetitions"][0]["component_bytes"]["filesystem_allocated_bytes"] = 15
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError, match="component byte schema"
    ):
        footprint.validate_raw_artifact(raw)


def test_raw_validation_rejects_mixed_producer_repetitions():
    raw = _raw_fixture()
    raw["repetitions"][1]["research_commit_sha"] = "b" * 40
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="producer SHA"):
        footprint.validate_raw_artifact(raw, expected_producer_sha="a" * 40)


def test_snapshot_cli_parser_has_offline_preflight_and_summary_commands():
    from extstats_advisor_research.cli import _parser

    args = _parser().parse_args(["validate", "rq5-cost", "snapshot-footprint", "preflight"])
    assert args.rq5_snapshot_command == "preflight"
    args = _parser().parse_args(
        ["validate", "rq5-cost", "snapshot-footprint", "summarize", "--preflight", "preflight.json"]
    )
    assert args.rq5_snapshot_command == "summarize"
    assert args.preflight == Path("preflight.json")
