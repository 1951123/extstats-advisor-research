from __future__ import annotations

import json
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
        "advisor_command": "extstats-advisor",
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "canonical_workload_sha256": None,
        "protocol_path": footprint.PROTOCOL_PATH,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": "e" * 64,
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "relation": "public.t",
        "dataset_identity": "arecel-power7",
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
        "advisor_command": "extstats-advisor",
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "canonical_workload_sha256": None,
        "protocol_path": footprint.PROTOCOL_PATH,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": "e" * 64,
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "relation": "public.t",
        "dataset_identity": "arecel-power7",
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


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("sample_rows", None),
        ("sample_seed", 7),
        ("dataset_content_identity", "0" * 64),
        ("workload_sha256", "0" * 64),
        ("schema_contract_id", "wrong-schema"),
    ],
)
def test_raw_validation_rejects_snapshot_source_contract_mutations(field, replacement):
    raw = _raw_fixture()
    if replacement is None:
        raw.pop(field)
    else:
        raw[field] = replacement
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError):
        footprint.validate_raw_artifact(raw)


def _preflight_fixture(root: Path, source_spec: dict) -> Path:
    path = root / "experiments/rq5-snapshot-footprint-preflight-v1.json"
    path.parent.mkdir(parents=True)
    value = {
        "format_version": footprint.PREFLIGHT_FORMAT,
        "status": "ready-to-run",
        "formal_execution_started": False,
        "research_commit_sha": "a" * 40,
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "stock_postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "dataset_order": ["arecel-power7"],
        "repetitions": 3,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "stage_hard_cap_seconds": footprint.STAGE_HARD_CAP_SECONDS,
        "datasets": [source_spec],
        "no_truth_or_planner_work": True,
        "patched_postgres_required": False,
        "sensitive_snapshot_not_tracked": True,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "semantic_digest": "p" * 64,
    }
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def _structural_preflight_value() -> dict:
    datasets = []
    for dataset_id in footprint.DATASETS:
        datasets.append(
            {
                "dataset_id": dataset_id,
                "benchmark_id": dataset_id,
                "dataset_content_identity": "f" * 64,
                "relation": f"public.{dataset_id.removeprefix('arecel-')}",
                "schema_contract_id": f"{dataset_id}-schema-v1",
                "workload_id": f"{dataset_id}-workload-v1",
                "workload_sha256": "c" * 64,
                "workload_query_count": 10_000,
                "canonical_workload_sha256": None,
            }
        )
    value = {
        "format_version": footprint.PREFLIGHT_FORMAT,
        "status": "ready-to-run",
        "formal_execution_started": False,
        "research_commit_sha": "a" * 40,
        "advisor_sha": footprint.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": footprint.FROZEN_STOCK_POSTGRES_SHA,
        "stock_postgres_version": footprint.STOCK_POSTGRES_VERSION,
        "protocol_semantic_digest": footprint.PROTOCOL_DIGEST,
        "dataset_order": list(footprint.DATASETS),
        "repetitions": 3,
        "sample_rows": footprint.SAMPLE_ROWS,
        "sample_seed": footprint.SAMPLE_SEED,
        "stage_hard_cap_seconds": footprint.STAGE_HARD_CAP_SECONDS,
        "datasets": datasets,
        "no_truth_or_planner_work": True,
        "patched_postgres_required": False,
        "sensitive_snapshot_not_tracked": True,
        "preflight_path": "preflight.json",
    }
    value["semantic_digest"] = footprint.semantic_digest(footprint._without_digest(value))
    return value


def test_preflight_source_spec_order_and_required_fields_are_fail_closed(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    path = root / "preflight.json"
    value = _structural_preflight_value()
    path.write_text(json.dumps(value), encoding="utf-8")
    assert footprint.validate_preflight(path, root)["status"] == "valid"

    mutated = dict(value)
    mutated["datasets"] = list(value["datasets"])
    mutated["datasets"][1] = dict(mutated["datasets"][1])
    mutated["datasets"][1]["dataset_id"] = "arecel-power7"
    mutated["semantic_digest"] = footprint.semantic_digest(footprint._without_digest(mutated))
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="order or identity"):
        footprint.validate_preflight(path, root)

    mutated = _structural_preflight_value()
    mutated["datasets"][0].pop("schema_contract_id")
    mutated["semantic_digest"] = footprint.semantic_digest(footprint._without_digest(mutated))
    path.write_text(json.dumps(mutated), encoding="utf-8")
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="incomplete"):
        footprint.validate_preflight(path, root)


def test_runner_output_self_validates_and_binds_source(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source_spec = {
        "dataset_id": "arecel-power7",
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "relation": "public.t",
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "canonical_workload_sha256": None,
    }
    preflight = _preflight_fixture(root, source_spec)
    monkeypatch.setattr(
        footprint, "validate_preflight", lambda path, research_root: {"status": "valid"}
    )
    monkeypatch.setattr(
        footprint, "verify_research_repository", lambda root: {"research_commit_sha": "a" * 40}
    )
    monkeypatch.setattr(
        footprint,
        "_verify_formal_campaign_tree",
        lambda *args, **kwargs: "a" * 40,
    )
    monkeypatch.setattr(footprint, "verify_git_sha", lambda path, expected: expected)
    monkeypatch.setattr(
        footprint, "_dataset_source_spec", lambda dataset_id, data_root, temporary: source_spec
    )
    calls = []

    def fake_repetition(dataset_id, **kwargs):
        calls.append(kwargs["repetition_id"])
        repetition = _repetition_fixture("a" * 40)
        repetition["repetition_id"] = kwargs["repetition_id"]
        return repetition

    monkeypatch.setattr(footprint, "_run_repetition", fake_repetition)
    output = Path("experiments/rq5-snapshot-footprint-v1/raw/arecel-power7.json")
    result = footprint.run_snapshot_footprint(
        "arecel-power7",
        research_root=root,
        stock_dsn="unused",
        output=output,
        preflight=preflight,
        advisor_root=tmp_path / "advisor",
        stock_postgres_root=tmp_path / "stock",
    )
    assert calls == [1, 2, 3]
    assert result["sample_rows"] == 10_000
    assert result["sample_seed"] == 42
    assert result["dataset_content_identity"] == source_spec["dataset_content_identity"]
    assert result["schema_contract_id"] == source_spec["schema_contract_id"]
    assert result["workload_sha256"] == source_spec["workload_sha256"]
    assert result["research_commit_sha"] == "a" * 40
    assert (
        footprint.validate_raw_artifact(result, expected_source_spec=source_spec)[
            "repetition_count"
        ]
        == 3
    )


def test_source_or_checkout_drift_is_rejected_before_database(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    source_spec = {
        "dataset_id": "arecel-power7",
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "relation": "public.t",
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "canonical_workload_sha256": None,
    }
    preflight = _preflight_fixture(root, source_spec)
    monkeypatch.setattr(
        footprint, "validate_preflight", lambda path, research_root: {"status": "valid"}
    )
    monkeypatch.setattr(
        footprint, "verify_research_repository", lambda root: {"research_commit_sha": "a" * 40}
    )
    monkeypatch.setattr(
        footprint,
        "_verify_formal_campaign_tree",
        lambda *args, **kwargs: "a" * 40,
    )
    monkeypatch.setattr(footprint, "verify_git_sha", lambda path, expected: expected)
    drifted = {**source_spec, "dataset_content_identity": "0" * 64}
    monkeypatch.setattr(
        footprint, "_dataset_source_spec", lambda dataset_id, data_root, temporary: drifted
    )
    calls = {"create": 0, "run": 0}
    monkeypatch.setattr(
        footprint,
        "_create_database",
        lambda *args, **kwargs: calls.__setitem__("create", calls["create"] + 1),
    )
    monkeypatch.setattr(
        footprint,
        "_run_repetition",
        lambda *args, **kwargs: calls.__setitem__("run", calls["run"] + 1),
    )
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="source identity"):
        footprint.run_snapshot_footprint(
            "arecel-power7",
            research_root=root,
            stock_dsn="unused",
            output=Path("experiments/rq5-snapshot-footprint-v1/raw/arecel-power7.json"),
            preflight=preflight,
            advisor_root=tmp_path / "advisor",
            stock_postgres_root=tmp_path / "stock",
        )
    assert calls == {"create": 0, "run": 0}


@pytest.mark.parametrize(
    ("drift", "message"),
    [
        ("advisor", "Advisor source"),
        ("stock", "stock PostgreSQL source"),
    ],
)
def test_live_checkout_drift_is_rejected_before_database(monkeypatch, tmp_path, drift, message):
    root = tmp_path / "repo"
    root.mkdir()
    source_spec = {
        "dataset_id": "arecel-power7",
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "relation": "public.t",
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "workload_id": "workload-v1",
        "workload_sha256": "c" * 64,
        "workload_query_count": 10_000,
        "canonical_workload_sha256": None,
    }
    preflight = _preflight_fixture(root, source_spec)
    monkeypatch.setattr(
        footprint, "validate_preflight", lambda path, research_root: {"status": "valid"}
    )
    monkeypatch.setattr(
        footprint, "verify_research_repository", lambda root: {"research_commit_sha": "a" * 40}
    )
    monkeypatch.setattr(
        footprint,
        "_verify_formal_campaign_tree",
        lambda *args, **kwargs: "a" * 40,
    )

    def verify(path, expected):
        return (
            "0" * 40
            if ((drift == "advisor") == (expected == footprint.FROZEN_ADVISOR_SHA))
            else expected
        )

    monkeypatch.setattr(footprint, "verify_git_sha", verify)
    monkeypatch.setattr(
        footprint, "_dataset_source_spec", lambda dataset_id, data_root, temporary: source_spec
    )
    calls = {"create": 0}
    monkeypatch.setattr(
        footprint,
        "_create_database",
        lambda *args, **kwargs: calls.__setitem__("create", calls["create"] + 1),
    )
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match=message):
        footprint.run_snapshot_footprint(
            "arecel-power7",
            research_root=root,
            stock_dsn="unused",
            output=Path("experiments/rq5-snapshot-footprint-v1/raw/arecel-power7.json"),
            preflight=preflight,
            advisor_root=tmp_path / "advisor",
            stock_postgres_root=tmp_path / "stock",
        )
    assert calls == {"create": 0}


def test_snapshot_cli_parser_has_offline_preflight_and_summary_commands():
    from extstats_advisor_research.cli import _parser

    args = _parser().parse_args(["validate", "rq5-cost", "snapshot-footprint", "preflight"])
    assert args.rq5_snapshot_command == "preflight"
    args = _parser().parse_args(
        ["validate", "rq5-cost", "snapshot-footprint", "summarize", "--preflight", "preflight.json"]
    )
    assert args.rq5_snapshot_command == "summarize"
    assert args.preflight == Path("preflight.json")


def test_normalized_workload_identity_uses_real_adapter_shape():
    workload = {
        "workload_id": "arecel_census13_test_v1",
        "query_count": 10_000,
        "sha256": "a" * 64,
    }
    assert footprint._normalized_workload_identity(workload) == {
        "workload_id": "arecel_census13_test_v1",
        "workload_sha256": "a" * 64,
        "workload_query_count": 10_000,
    }


def test_dataset_source_spec_normalizes_real_adapter_shape(monkeypatch, tmp_path):
    module = footprint.DATASET_SPECS["arecel-census13"][0]
    monkeypatch.setattr(
        module,
        "inspect",
        lambda data_root: {
            "source_present": True,
            "dataset_content_identity": "b" * 64,
            "canonical_workload_sha256": "c" * 64,
        },
    )
    monkeypatch.setattr(
        module,
        "extract_workload",
        lambda path, data_root, split: {
            "workload_id": "arecel_census13_test_v1",
            "query_count": 10_000,
            "sha256": "a" * 64,
        },
    )
    result = footprint._dataset_source_spec("arecel-census13", None, tmp_path)
    assert result["workload_id"] == "arecel_census13_test_v1"
    assert result["workload_sha256"] == "a" * 64
    assert result["workload_query_count"] == 10_000


@pytest.mark.parametrize(
    "workload",
    [
        {"workload_id": "workload", "sha256": "a" * 64},
        {"workload_id": "workload", "query_count": 10_000},
        {"workload_id": "workload", "query_count": 10_000, "sha256": "abc"},
        {"query_count": 10_000, "sha256": "a" * 64},
        {"workload_id": "workload", "query_count": 9_999, "sha256": "a" * 64},
    ],
)
def test_normalized_workload_identity_rejects_invalid_adapter_results(workload):
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError):
        footprint._normalized_workload_identity(workload)


def _run_repetition_source_spec(query_count: int = 10_000) -> dict:
    return {
        "dataset_id": "arecel-power7",
        "benchmark_id": "arecel-power7",
        "dataset_content_identity": "f" * 64,
        "relation": "public.power7",
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "workload_id": "arecel_power7_test_v1",
        "workload_sha256": "a" * 64,
        "workload_query_count": query_count,
        "canonical_workload_sha256": None,
    }


def _exercise_repetition_workload_gate(monkeypatch, query_count: int, source_count: int):
    module = footprint.DATASET_SPECS["arecel-power7"][0]
    monkeypatch.setattr(
        module,
        "extract_workload",
        lambda path, data_root, split: {
            "workload_id": "arecel_power7_test_v1",
            "query_count": query_count,
            "sha256": "a" * 64,
        },
    )
    source_spec = _run_repetition_source_spec(source_count)
    monkeypatch.setattr(
        footprint,
        "_dataset_source_spec",
        lambda dataset_id, data_root, temporary: source_spec,
    )
    monkeypatch.setattr(footprint, "_psycopg", lambda: object())
    created = []

    def fake_create(*args, **kwargs):
        created.append(True)
        raise RuntimeError("stop after create")

    monkeypatch.setattr(footprint, "_create_database", fake_create)
    return source_spec, created


def test_run_repetition_reaches_create_database_with_real_adapter_shape(monkeypatch):
    _, created = _exercise_repetition_workload_gate(monkeypatch, 10_000, 10_000)
    with pytest.raises(RuntimeError, match="stop after create"):
        footprint._run_repetition(
            "arecel-power7",
            repetition_id=1,
            stock_dsn="unused",
            data_root=None,
            advisor_command="extstats-advisor",
            research_commit_sha="a" * 40,
            source_spec=_run_repetition_source_spec(),
        )
    assert created == [True]


def test_run_repetition_rejects_wrong_query_count_before_create(monkeypatch):
    _, created = _exercise_repetition_workload_gate(monkeypatch, 9_999, 10_000)
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError):
        footprint._run_repetition(
            "arecel-power7",
            repetition_id=1,
            stock_dsn="unused",
            data_root=None,
            advisor_command="extstats-advisor",
            research_commit_sha="a" * 40,
            source_spec=_run_repetition_source_spec(),
        )
    assert created == []


def test_run_repetition_rejects_source_query_count_mismatch_before_create(monkeypatch):
    _, created = _exercise_repetition_workload_gate(monkeypatch, 10_000, 9_999)
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="workload source"):
        footprint._run_repetition(
            "arecel-power7",
            repetition_id=1,
            stock_dsn="unused",
            data_root=None,
            advisor_command="extstats-advisor",
            research_commit_sha="a" * 40,
            source_spec=_run_repetition_source_spec(9_999),
        )
    assert created == []


def _campaign_preflight() -> dict:
    value = _structural_preflight_value()
    value["preflight_path"] = "experiments/rq5-snapshot-footprint-preflight-v1.json"
    return value


def _campaign_status(preflight: dict, *datasets: str) -> list[tuple[str, str]]:
    return [
        ("??", preflight["preflight_path"]),
        *[("??", footprint._raw_relative_path(dataset_id)) for dataset_id in datasets],
    ]


def _patch_campaign_git(monkeypatch, preflight: dict, statuses: list[tuple[str, str]]):
    monkeypatch.setattr(footprint, "_git_head_sha", lambda root: preflight["research_commit_sha"])
    monkeypatch.setattr(footprint, "_git_status_entries", lambda root: statuses)


def test_campaign_gate_census_accepts_preflight_only_and_rejects_unrelated_or_tracked(
    monkeypatch, tmp_path
):
    root = tmp_path / "repo"
    root.mkdir()
    preflight = _campaign_preflight()
    expected = _campaign_status(preflight)
    _patch_campaign_git(monkeypatch, preflight, expected)
    assert (
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id="arecel-census13",
            output_relative=footprint._raw_relative_path("arecel-census13"),
        )
        == preflight["research_commit_sha"]
    )

    _patch_campaign_git(monkeypatch, preflight, [*expected, ("??", "debug.json")])
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="unexpected dirty"):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id="arecel-census13",
            output_relative=footprint._raw_relative_path("arecel-census13"),
        )

    _patch_campaign_git(
        monkeypatch, preflight, [("??", preflight["preflight_path"]), (" M", "src/x.py")]
    )
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError, match="tracked modifications"
    ):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id="arecel-census13",
            output_relative=footprint._raw_relative_path("arecel-census13"),
        )


@pytest.mark.parametrize(
    ("dataset_id", "prior"),
    [
        ("arecel-forest10", ("arecel-census13",)),
        ("arecel-power7", ("arecel-census13", "arecel-forest10")),
        ("arecel-dmv11", ("arecel-census13", "arecel-forest10", "arecel-power7")),
    ],
)
def test_campaign_gate_accepts_only_valid_prior_progression(
    monkeypatch, tmp_path, dataset_id, prior
):
    root = tmp_path / "repo"
    root.mkdir()
    preflight = _campaign_preflight()
    statuses = _campaign_status(preflight, *prior)
    _patch_campaign_git(monkeypatch, preflight, statuses)
    validated = []
    monkeypatch.setattr(
        footprint,
        "_validate_raw_child_reference",
        lambda root, **kwargs: validated.append(kwargs["dataset_id"]) or {},
    )
    assert (
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id=dataset_id,
            output_relative=footprint._raw_relative_path(dataset_id),
        )
        == preflight["research_commit_sha"]
    )
    assert validated == list(prior)


def test_campaign_gate_rejects_future_child_before_live_work(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    future = root / footprint._raw_relative_path("arecel-power7")
    future.parent.mkdir(parents=True)
    future.write_text("future", encoding="utf-8")
    preflight = _campaign_preflight()
    _patch_campaign_git(
        monkeypatch,
        preflight,
        _campaign_status(preflight, "arecel-census13", "arecel-power7"),
    )
    monkeypatch.setattr(footprint, "_validate_raw_child_reference", lambda *args, **kwargs: {})
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError, match="future formal raw child"
    ):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id="arecel-forest10",
            output_relative=footprint._raw_relative_path("arecel-forest10"),
        )


def test_campaign_gate_rejects_invalid_prior_child(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    preflight = _campaign_preflight()
    _patch_campaign_git(monkeypatch, preflight, _campaign_status(preflight, "arecel-census13"))
    monkeypatch.setattr(
        footprint,
        "_validate_raw_child_reference",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            footprint.RQ5SnapshotFootprintValidationError("raw digest mismatch")
        ),
    )
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="raw digest"):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="run",
            dataset_id="arecel-forest10",
            output_relative=footprint._raw_relative_path("arecel-forest10"),
        )


def test_campaign_gate_summarize_accepts_exact_accumulated_set(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    preflight = _campaign_preflight()
    statuses = _campaign_status(preflight, *footprint.DATASETS)
    _patch_campaign_git(monkeypatch, preflight, statuses)
    validated = []
    monkeypatch.setattr(
        footprint,
        "_validate_raw_child_reference",
        lambda root, **kwargs: validated.append(kwargs["dataset_id"]) or {},
    )
    assert (
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="summarize",
            output_relative="experiments/rq5-snapshot-footprint-v1.json",
        )
        == preflight["research_commit_sha"]
    )
    assert validated == list(footprint.DATASETS)


def test_campaign_gate_summarize_rejects_missing_child_or_existing_summary(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    preflight = _campaign_preflight()
    statuses = _campaign_status(preflight, *footprint.DATASETS[:-1])
    _patch_campaign_git(monkeypatch, preflight, statuses)
    monkeypatch.setattr(footprint, "_validate_raw_child_reference", lambda *args, **kwargs: {})
    with pytest.raises(footprint.RQ5SnapshotFootprintValidationError, match="unexpected dirty"):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="summarize",
            output_relative="experiments/rq5-snapshot-footprint-v1.json",
        )

    summary = root / "experiments/rq5-snapshot-footprint-v1.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    summary.write_text("existing", encoding="utf-8")
    statuses = _campaign_status(preflight, *footprint.DATASETS) + [
        ("??", summary.relative_to(root).as_posix())
    ]
    _patch_campaign_git(monkeypatch, preflight, statuses)
    with pytest.raises(
        footprint.RQ5SnapshotFootprintValidationError, match="summary already exists"
    ):
        footprint._verify_formal_campaign_tree(
            root,
            preflight=preflight,
            preflight_relative=preflight["preflight_path"],
            phase="summarize",
            output_relative="experiments/rq5-snapshot-footprint-v1.json",
        )
