from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from extstats_advisor_research import rq1_workload_generalization_live as live
from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import read_json

ROOT = Path(__file__).resolve().parents[1]


def test_forest10_spec_is_frozen_and_namespaced() -> None:
    spec = live.FOREST10_SPEC
    assert spec.dataset_id == "arecel-forest10"
    assert spec.valid_workload_id == "arecel_forest10_valid_v1"
    assert spec.valid_workload_sha256 == (
        "e589bf277cf8303bfaf12943e651e5ba10065f8510df9b0f195dff7dfc1b5cb5"
    )
    assert spec.valid_observations_sha256 == (
        "fbd68c2b22600d69e29cedbfd567716d9a613cf2d1d434298f1f661e73f8a414"
    )
    assert spec.dataset_content_identity == (
        "fc25a1e2c7deefd2ba071985afd8dc78a7d547602e49f9fff7ed3def8d230556"
    )
    assert spec.baseline_semantic_digest == (
        "3dbc024c6d182011f3eab3c5c333a7013a445f4f951e6cc8725086bfff3b0436"
    )
    assert spec.campaign_attempt_index == 1
    assert spec.prior_failed_attempt_path is None
    assert spec.output_root != live.POWER7_SPEC.output_root
    assert spec.runtime_name == "forest10"


def test_forest_valid_identity_uses_only_valid_side_inputs(monkeypatch: pytest.MonkeyPatch) -> None:
    original_read_json = live.read_json

    def guarded_read_json(path: Path):
        text = str(path)
        forbidden = (
            "authoritative-cardinality-observations-v1.json",
            "rq1-confirmatory",
            "strict-unseen",
            "source-audit-v2",
            "truth-policy-v1",
        )
        if any(item in text for item in forbidden):
            raise AssertionError(f"evaluation-side artifact opened pre-seal: {path}")
        return original_read_json(path)

    monkeypatch.setattr(live, "read_json", guarded_read_json)
    identity = live._valid_truth_identity(ROOT, live.FOREST10_SPEC)
    assert identity["workload_id"] == "arecel_forest10_valid_v1"
    assert identity["source_kind"] == "authoritative-external-exact"


def test_forest_preflight_is_design_safe_and_attempt_one(monkeypatch: pytest.MonkeyPatch) -> None:
    producer = "1" * 40
    original_read_json = live.read_json

    def guarded_read_json(path: Path):
        text = str(path)
        forbidden = (
            "authoritative-cardinality-observations-v1.json",
            "rq1-confirmatory",
            "strict-unseen",
        )
        if any(item in text for item in forbidden):
            raise AssertionError(f"evaluation-side artifact opened pre-seal: {path}")
        return original_read_json(path)

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        if "rev-parse" in command:
            return Completed(producer + "\n")
        if "status" in command:
            return Completed("")
        raise AssertionError(command)

    monkeypatch.setattr(live, "read_json", guarded_read_json)
    monkeypatch.setattr(live.subprocess, "run", fake_run)
    monkeypatch.setattr(
        live,
        "_design_source_spec",
        lambda root, data_root=None, spec=live.FOREST10_SPEC: {
            "dataset_id": spec.dataset_id,
            "benchmark_id": spec.dataset_id,
            "dataset_content_identity": spec.dataset_content_identity,
            "relation": spec.dataset_module.RELATION,
            "schema_contract_id": spec.dataset_module.SCHEMA_CONTRACT_ID,
            "rows": spec.dataset_module.EXPECTED_ROWS,
            "design_workload": {
                "source_split": "valid",
                "workload_id": spec.valid_workload_id,
                "sha256": spec.valid_workload_sha256,
                "query_count": live.SAMPLE_ROWS,
                "canonical_source_sha256": spec.valid_canonical_workload_sha256,
            },
            "design_truth": {
                "source_split": "valid",
                "path": spec.valid_observations_path.as_posix(),
                "observations_sha256": spec.valid_observations_sha256,
                "workload_id": spec.valid_workload_id,
                "query_count": live.SAMPLE_ROWS,
                "dataset_identity": spec.dataset_content_identity,
            },
        },
    )
    value = live.build_forest10_rq1b_preflight(
        research_root=ROOT,
        producer_sha=producer,
    )
    assert value["campaign_attempt_index"] == 1
    assert value["prior_failed_attempt"] is None
    assert value["research_commit_sha"] == producer
    assert value["preseal_evaluation_content_unread"] is True
    assert "arecel_forest10_test_" not in json.dumps(value, sort_keys=True)
    assert "strict_unseen_count" not in json.dumps(value, sort_keys=True)
    assert live.validate_forest10_rq1b_preflight(value, research_root=ROOT)["status"] == "valid"


def test_forest_baseline_binding_rejects_power7_artifact() -> None:
    swapped = replace(
        live.FOREST10_SPEC,
        baseline_path=live.POWER7_SPEC.baseline_path,
        baseline_semantic_digest=live.POWER7_SPEC.baseline_semantic_digest,
    )
    with pytest.raises(live.RQ1BValidationError):
        live._baseline_binding(ROOT, swapped)


def test_strict_unseen_selects_the_forest10_membership_row() -> None:
    membership = read_json(ROOT / live.STRICT_UNSEEN_PATH)
    records = [
        {
            "query_id": f"arecel_forest10_test_{index:06d}",
            "estimate": 1.0,
            "truth": 1,
            "qerror": 1.0,
        }
        for index in range(live.SAMPLE_ROWS)
    ]
    selected = live.strict_unseen_filter(records, membership, spec=live.FOREST10_SPEC)
    assert len(selected) == 10_000
    assert [row["query_id"] for row in selected] == [row["query_id"] for row in records]


def test_cross_dataset_design_and_result_validation_fail_closed() -> None:
    power_design = read_json(ROOT / live.POWER7_SPEC.output_root / "design-v1.json")
    with pytest.raises(live.RQ1BValidationError):
        live.validate_design_artifact(power_design, _spec=live.FOREST10_SPEC)

    power_result = read_json(ROOT / live.POWER7_SPEC.output_root / "result-v1.json")
    with pytest.raises(live.RQ1BValidationError):
        live.validate_rq1b_result(power_result, spec=live.FOREST10_SPEC)


def test_dataset_cli_exposes_only_the_parameterized_rq1b_family() -> None:
    parser = _parser()
    forest_preflight = parser.parse_args(["rq1-generalization", "forest10", "preflight-create"])
    run_args = [
        "--stock-dsn",
        "stock",
        "--planner-dsn",
        "planner",
        "--preflight",
        "preflight.json",
    ]
    forest_run = parser.parse_args(["rq1-generalization", "forest10", "run", *run_args])
    power_run = parser.parse_args(["rq1-generalization", "power7", "run", *run_args])
    assert forest_preflight.rq1g_live_command == "forest10"
    assert forest_preflight.rq1g_dataset_command == "preflight-create"
    assert forest_run.rq1g_live_command == "forest10"
    assert power_run.rq1g_live_command == "power7"
