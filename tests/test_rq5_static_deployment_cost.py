from __future__ import annotations

import json
from pathlib import Path

import pytest

import extstats_advisor_research.rq5_static_deployment_cost as static_cost
from extstats_advisor_research.cli import _parser
from extstats_advisor_research.rq5_static_deployment_cost import (
    DATASETS,
    PROTOCOL_DIGEST,
    PROTOCOL_FORMAT,
    RQ5StaticDeploymentValidationError,
    _baseline_state,
    _canonical_repo_artifact_path,
    _logical_storage,
    _source_projection,
    _validate_dataset_raw,
    build_preflight,
    run_static_deployment,
    summarize_static_deployment,
    validate_protocol,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "paper/rq5-static-deployment-cost-protocol-v1.json"


def _protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def test_artifact_paths_are_canonicalized_relative_to_research_root(tmp_path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)

    relative = Path("experiments/rq5-static-deployment-cost-preflight-v1.json")
    absolute, repo_relative = _canonical_repo_artifact_path(root, relative, label="preflight")
    assert absolute == root / relative
    assert repo_relative.as_posix() == str(relative)

    absolute_again, relative_again = _canonical_repo_artifact_path(
        root, absolute, label="preflight"
    )
    assert (absolute_again, relative_again) == (absolute, repo_relative)


def test_artifact_paths_reject_outside_repository(tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    for path in (tmp_path / "outside.json", Path("../outside.json")):
        with pytest.raises(
            RQ5StaticDeploymentValidationError, match="must reside inside the research repository"
        ):
            _canonical_repo_artifact_path(root, path, label="preflight")


def test_preflight_builder_rejects_outside_output_before_repository_work(tmp_path) -> None:
    with pytest.raises(
        RQ5StaticDeploymentValidationError, match="must reside inside the research repository"
    ):
        build_preflight(
            ROOT,
            stock_postgres_root=ROOT,
            output=tmp_path / "outside-preflight.json",
        )


def test_illegal_run_paths_fail_before_any_live_work(monkeypatch, tmp_path) -> None:
    calls = {"create": 0, "loader": 0, "repetition": 0}

    def fail_live(*args, **kwargs):
        calls["create"] += 1
        raise AssertionError("live work was reached")

    monkeypatch.setattr(static_cost, "_create_database", fail_live)
    monkeypatch.setattr(
        static_cost,
        "_run_repetition",
        lambda *args, **kwargs: calls.__setitem__("repetition", calls["repetition"] + 1),
    )
    monkeypatch.setattr(
        static_cost,
        "load_power7",
        lambda *args, **kwargs: calls.__setitem__("loader", calls["loader"] + 1),
    )

    with pytest.raises(
        RQ5StaticDeploymentValidationError, match="must reside inside the research repository"
    ):
        run_static_deployment(
            "arecel-power7",
            research_root=ROOT,
            stock_dsn="local",
            output=Path("experiments/raw.json"),
            stock_postgres_root=ROOT,
            preflight=tmp_path / "outside-preflight.json",
        )
    assert calls == {"create": 0, "loader": 0, "repetition": 0}


def test_runner_assembles_with_repo_relative_paths_without_database(monkeypatch, tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    stock_root = tmp_path / "stock-source"
    stock_root.mkdir()
    preflight = root / "experiments/rq5-static-deployment-cost-preflight-v1.json"
    preflight.parent.mkdir(parents=True)
    producer_sha = "a" * 40
    preflight.write_text(
        json.dumps({"research_commit_sha": producer_sha, "semantic_digest": "preflight"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        static_cost, "validate_preflight", lambda path, research_root: {"status": "valid"}
    )
    monkeypatch.setattr(
        static_cost,
        "_git_sha",
        lambda path: (
            static_cost.STOCK_POSTGRES_SHA
            if Path(path).resolve() == stock_root.resolve()
            else producer_sha
        ),
    )
    projection = {
        "dataset_id": "arecel-power7",
        "source_rq2_child": "experiments/source-rq2.json",
        "source_rq2_digest": "b" * 64,
        "source_deployment_artifact": "experiments/source-deployment.json",
        "source_deployment_digest": "c" * 64,
        "selected_candidate_ids": ["candidate"],
        "objects": [{"kind": "postgresql.mcv"}],
    }
    monkeypatch.setattr(
        static_cost,
        "_source_projection",
        lambda research_root, dataset_id: projection,
    )

    def fake_repetition(source_projection, **kwargs):
        return {
            "repetition_id": kwargs["repetition_id"],
            "source_rq2_child": source_projection["source_rq2_child"],
            "source_rq2_digest": source_projection["source_rq2_digest"],
            "source_deployment_artifact": source_projection["source_deployment_artifact"],
            "source_deployment_digest": source_projection["source_deployment_digest"],
            "research_commit_sha": producer_sha,
            "statistics_target": 100,
            "ddl": {"elapsed_seconds": 1.0, "committed": True},
            "analyze": {
                "elapsed_seconds": 2.0,
                "completed": True,
                "payload_verification_passed": True,
            },
            "derived": {"direct_combined_wall_clock_measurement": False},
            "cleanup": {"database_dropped": True},
        }

    monkeypatch.setattr(
        static_cost,
        "_run_repetition",
        fake_repetition,
    )
    output = Path("experiments/rq5-static-deployment-cost-v1/raw/arecel-power7.json")
    result = run_static_deployment(
        "arecel-power7",
        research_root=root,
        stock_dsn="local",
        output=output,
        stock_postgres_root=stock_root,
        preflight=Path("experiments/rq5-static-deployment-cost-preflight-v1.json"),
    )
    assert result["preflight_path"] == "experiments/rq5-static-deployment-cost-preflight-v1.json"
    assert result["source_rq2_child"] == projection["source_rq2_child"]
    assert result["source_rq2_digest"] == projection["source_rq2_digest"]
    assert result["source_deployment_artifact"] == projection["source_deployment_artifact"]
    assert result["source_deployment_digest"] == projection["source_deployment_digest"]
    assert result["selected_candidate_ids"] == projection["selected_candidate_ids"]
    assert result["object_count"] == len(projection["objects"])
    assert result["statistics_kinds"] == [item["kind"] for item in projection["objects"]]
    assert result["statistics_target"] == 100
    assert result["research_commit_sha"] == producer_sha
    assert (root / output).is_file()
    assert not (other_cwd / output).exists()
    static_cost._validate_dataset_raw(
        json.loads((root / output).read_text(encoding="utf-8")),
        projection,
        expected_research_commit_sha=producer_sha,
    )


def test_summarizer_assembles_with_repo_relative_paths(monkeypatch, tmp_path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    preflight = root / "experiments/rq5-static-deployment-cost-preflight-v1.json"
    preflight.parent.mkdir(parents=True)
    producer_sha = "a" * 40
    preflight.write_text(
        json.dumps({"research_commit_sha": producer_sha, "semantic_digest": "preflight"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        static_cost, "validate_preflight", lambda path, research_root: {"status": "valid"}
    )
    source_paths = {}
    projections = {}
    for dataset_id in DATASETS:
        source_path = root / f"sources/{dataset_id}.json"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.write_text(
            json.dumps({"full_data": {"runtime": {"deployment_including_final_analyze": 1.0}}}),
            encoding="utf-8",
        )
        source_paths[dataset_id] = {"path": str(source_path.relative_to(root))}
        projections[dataset_id] = {
            "dataset_id": dataset_id,
            "source_rq2_child": source_paths[dataset_id]["path"],
            "source_rq2_digest": "rq2",
            "source_deployment_artifact": f"deployments/{dataset_id}.json",
            "source_deployment_digest": "deployment",
            "selected_candidate_ids": ["candidate"],
            "object_count": 1,
            "statistics_kinds": ["postgresql.mcv"],
            "statistics_target": 100,
            "actual_selected_k": 1,
            "objects": [{"kind": "postgresql.mcv"}],
        }
    monkeypatch.setattr(static_cost, "RQ2_CHILDREN", source_paths)
    monkeypatch.setattr(
        static_cost, "_source_projection", lambda root, dataset_id: projections[dataset_id]
    )
    for dataset_id in DATASETS:
        raw_path = root / f"experiments/rq5-static-deployment-cost-v1/raw/{dataset_id}.json"
        repetition = {
            "repetition_id": 1,
            "ddl": {"elapsed_seconds": 1.0},
            "analyze": {"elapsed_seconds": 2.0, "payload_verification_passed": True},
            "derived": {"sequential_ddl_plus_analyze_seconds": 3.0},
            "logical_storage": {"after_ddl": {"total": 10}, "after_analyze": {"total": 20}},
            "physical_catalog_allocation": {
                "deltas": {
                    "after_ddl_minus_baseline": {"combined_catalog_total_relation_bytes": 0},
                    "after_analyze_minus_baseline": {"combined_catalog_total_relation_bytes": 0},
                }
            },
            "base_relation_total_bytes_context": 100,
            "cleanup": {"database_dropped": True},
        }
        raw = {
            "status": "complete",
            "format_version": "rq5-static-deployment-cost-dataset-v1",
            "formal_execution": True,
            "dataset_id": dataset_id,
            "research_commit_sha": producer_sha,
            "source_rq2_child": source_paths[dataset_id]["path"],
            "source_rq2_digest": "rq2",
            "source_deployment_artifact": f"deployments/{dataset_id}.json",
            "source_deployment_digest": "deployment",
            "selected_candidate_ids": ["candidate"],
            "object_count": 1,
            "statistics_kinds": ["postgresql.mcv"],
            "statistics_target": 100,
            "stock_postgresql_sha": static_cost.STOCK_POSTGRES_SHA,
            "no_advisor_selection": True,
            "repetitions": [],
        }
        for repetition_id in (1, 2, 3):
            raw["repetitions"].append(
                {
                    **repetition,
                    "repetition_id": repetition_id,
                    "source_rq2_child": source_paths[dataset_id]["path"],
                    "source_rq2_digest": "rq2",
                    "source_deployment_artifact": f"deployments/{dataset_id}.json",
                    "source_deployment_digest": "deployment",
                    "research_commit_sha": producer_sha,
                    "statistics_target": 100,
                    "ddl": {"elapsed_seconds": 1.0, "committed": True},
                    "analyze": {
                        "elapsed_seconds": 2.0,
                        "completed": True,
                        "payload_verification_passed": True,
                    },
                    "derived": {
                        "sequential_ddl_plus_analyze_seconds": 3.0,
                        "direct_combined_wall_clock_measurement": False,
                    },
                }
            )
        raw["semantic_digest"] = static_cost.semantic_digest(
            {key: item for key, item in raw.items() if key != "semantic_digest"}
        )
        static_cost.write_json(raw_path, raw)

    output = Path("experiments/rq5-static-deployment-cost-v1.json")
    result = summarize_static_deployment(
        root,
        output=output,
        preflight=Path("experiments/rq5-static-deployment-cost-preflight-v1.json"),
    )
    assert result["preflight_path"] == "experiments/rq5-static-deployment-cost-preflight-v1.json"
    assert (root / output).is_file()
    assert not (other_cwd / output).exists()


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
    producer_sha = "a" * 40
    repetition = {
        "repetition_id": 1,
        "source_rq2_child": projection["source_rq2_child"],
        "source_rq2_digest": projection["source_rq2_digest"],
        "source_deployment_artifact": projection["source_deployment_artifact"],
        "source_deployment_digest": projection["source_deployment_digest"],
        "research_commit_sha": producer_sha,
        "statistics_target": 100,
        "ddl": {"elapsed_seconds": 1.0, "committed": True},
        "analyze": {"elapsed_seconds": 2.0, "completed": True, "payload_verification_passed": True},
        "derived": {"direct_combined_wall_clock_measurement": False},
        "cleanup": {"database_dropped": True},
    }
    return {
        "format_version": "rq5-static-deployment-cost-dataset-v1",
        "status": "complete",
        "dataset_id": dataset_id,
        "formal_execution": True,
        "research_commit_sha": producer_sha,
        "source_rq2_child": projection["source_rq2_child"],
        "source_rq2_digest": projection["source_rq2_digest"],
        "source_deployment_artifact": projection["source_deployment_artifact"],
        "source_deployment_digest": projection["source_deployment_digest"],
        "selected_candidate_ids": projection["selected_candidate_ids"],
        "object_count": len(projection["objects"]),
        "statistics_kinds": [item["kind"] for item in projection["objects"]],
        "statistics_target": 100,
        "stock_postgresql_sha": "0d1c00c624fa7367d4a895f44381887757289682",
        "no_advisor_selection": True,
        "repetitions": [
            repetition,
            {**repetition, "repetition_id": 2},
            {**repetition, "repetition_id": 3},
        ],
    }


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("source_rq2_digest", "0" * 64, "raw RQ2 source drifted"),
        ("source_deployment_digest", "0" * 64, "raw deployment source drifted"),
        ("source_rq2_child", "wrong/rq2.json", "raw RQ2 source path drifted"),
        (
            "source_deployment_artifact",
            "wrong/deployment.json",
            "raw deployment source path drifted",
        ),
        ("selected_candidate_ids", ["wrong"], "raw recommendation membership drifted"),
        ("object_count", 0, "raw recommendation object count drifted"),
        (
            "statistics_kinds",
            ["postgresql.dependencies"],
            "raw recommendation statistics kinds drifted",
        ),
        ("statistics_target", 99, "raw recommendation target drifted"),
    ],
)
def test_raw_provenance_and_recommendation_mutations_are_rejected(
    field, replacement, message
) -> None:
    raw = _raw_fixture()
    raw[field] = replacement
    with pytest.raises(ValueError, match=message):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))


def test_raw_wrong_producer_is_rejected_when_expected_producer_is_bound() -> None:
    raw = _raw_fixture()
    raw["research_commit_sha"] = "b" * 40
    with pytest.raises(ValueError, match="raw research producer SHA drifted"):
        _validate_dataset_raw(
            raw,
            _source_projection(ROOT, "arecel-power7"),
            expected_research_commit_sha="a" * 40,
        )


def test_mixed_repetition_provenance_is_rejected() -> None:
    raw = _raw_fixture()
    raw["repetitions"][1]["source_rq2_digest"] = "d" * 64
    with pytest.raises(ValueError, match="repetition source_rq2_digest"):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))

    raw = _raw_fixture()
    raw["repetitions"][1]["research_commit_sha"] = "b" * 40
    with pytest.raises(ValueError, match="repetition research producer SHA"):
        _validate_dataset_raw(raw, _source_projection(ROOT, "arecel-power7"))


class _FakeConnection:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)
        self.executed: list[tuple[str, object]] = []
        self.current: object = None

    def execute(self, statement: str, parameters: object = None) -> _FakeConnection:
        self.executed.append((statement, parameters))
        self.current = next(self.responses)
        return self

    def fetchone(self) -> object:
        return self.current

    def fetchall(self) -> object:
        return self.current


def _relation_size_rows() -> list[object]:
    return [(8192, 8192), (16384,)]


def _catalog_row(item: dict, *, payload: bool = False, oid: int = 1) -> tuple:
    kind_code = {"postgresql.mcv": "{m}", "postgresql.dependencies": "{f}"}[item["kind"]]
    return (
        oid,
        item["name"],
        kind_code,
        " ".join(str(value) for value in item["column_ordinals"]),
        item["statistics_target"],
        32,
        64 if payload else None,
        16 if payload and item["kind"] == "postgresql.mcv" else None,
        16 if payload and item["kind"] == "postgresql.dependencies" else None,
        None,
        payload,
        payload,
        False,
    )


def test_empty_baseline_has_zero_selected_object_bytes_and_real_physical_sizes() -> None:
    projection = _source_projection(ROOT, "arecel-power7")
    connection = _FakeConnection([(0,), *_relation_size_rows()])
    state = _baseline_state(connection, projection)
    assert state["logical_catalog_row_bytes"] == {
        "pg_statistic_ext": 0,
        "pg_statistic_ext_data": 0,
        "total": 0,
    }
    assert state["mcv_payload_bytes"] == 0
    assert state["dependencies_payload_bytes"] == 0
    assert state["ndistinct_payload_bytes"] == 0
    assert state["objects"] == []
    assert (
        state["physical_catalog_relation_allocation"]["combined_catalog_total_relation_bytes"]
        == 16384
    )


def test_dirty_baseline_is_rejected_even_if_recommendation_names_differ() -> None:
    projection = _source_projection(ROOT, "arecel-power7")
    connection = _FakeConnection([(1,)])
    with pytest.raises(ValueError, match="fresh baseline"):
        _baseline_state(connection, projection)


def test_after_ddl_requires_all_exact_definitions_and_rejects_extra() -> None:
    projection = _source_projection(ROOT, "arecel-power7")
    with pytest.raises(ValueError, match="catalog logical row count"):
        _logical_storage(_FakeConnection([[]]), projection, require_payload=False)

    partial_rows = [
        _catalog_row(item, oid=index)
        for index, item in enumerate(projection["objects"][:-1], start=1)
    ]
    with pytest.raises(ValueError, match="catalog logical row count"):
        _logical_storage(_FakeConnection([partial_rows]), projection, require_payload=False)

    extra = _catalog_row(projection["objects"][0], oid=999)
    extra = (extra[0], "unexpected_extra", *extra[2:])
    complete_rows = [
        _catalog_row(item, oid=index) for index, item in enumerate(projection["objects"], start=1)
    ]
    with pytest.raises(ValueError, match="catalog logical row count"):
        _logical_storage(
            _FakeConnection([[*complete_rows, extra]]), projection, require_payload=False
        )


def test_payload_state_remains_kind_specific_before_and_after_analyze() -> None:
    projection = _source_projection(ROOT, "arecel-power7")
    rows_without_payload = [
        _catalog_row(item, oid=index) for index, item in enumerate(projection["objects"], start=1)
    ]
    _logical_storage(_FakeConnection([rows_without_payload]), projection, require_payload=False)
    with pytest.raises(ValueError, match="required"):
        _logical_storage(_FakeConnection([rows_without_payload]), projection, require_payload=True)

    rows_with_payload = [
        _catalog_row(item, payload=True, oid=index)
        for index, item in enumerate(projection["objects"], start=1)
    ]
    with pytest.raises(ValueError, match="payload appeared before"):
        _logical_storage(_FakeConnection([rows_with_payload]), projection, require_payload=False)
    _logical_storage(_FakeConnection([rows_with_payload]), projection, require_payload=True)


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
