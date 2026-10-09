from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from extstats_advisor_research import rq1_workload_generalization_live as live
from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import read_json

ROOT = Path(__file__).resolve().parents[1]


def test_dmv11_spec_binds_committed_provenance_and_namespace() -> None:
    spec = live.DMV11_SPEC
    assert spec.dataset_id == "arecel-dmv11"
    assert spec.cli_name == "dmv11"
    assert spec.dataset_module.RELATION == "public.dmv11"
    assert spec.dataset_module.EXPECTED_ROWS == 11_591_877
    assert spec.valid_workload_id == "arecel_dmv11_valid_v1"
    assert spec.valid_workload_sha256 == (
        "b2b98540a800a0a8cafac2cd9d5a21c61db954eddd401d2df1f633a2edbe2dd5"
    )
    assert spec.valid_canonical_workload_sha256 == (
        "71bb6e3c61da5ab1096cc52d41e7f696d486e4a3ba1c4586c01119df26fe9f40"
    )
    assert spec.valid_observations_path == Path(
        "truth/arecel/dmv11/authoritative-cardinality-observations-valid-v1.json"
    )
    assert spec.valid_observations_sha256 == (
        "f1586fc00d7dcd719aeb58a512c41a397f5b9afbc7be0b48cbfb30b4ee7417e0"
    )
    assert spec.valid_audit_path == Path("truth/arecel/dmv11/audit-valid-v1.json")
    assert spec.test_workload_id == "arecel_dmv11_test_v1"
    assert spec.test_workload_sha256 == (
        "fb4f1ab89f21b34214a94e1d1bd77cf52d9e5d1e7fd9c2ee7178245832163d2d"
    )
    assert spec.test_observations_path == Path(
        "truth/arecel/dmv11/authoritative-cardinality-observations-v1.json"
    )
    assert spec.test_observations_sha256 == (
        "aaedaf54313926fb03efb1051ba58c86b17729cad372a69e9c6faf5b192eb37a"
    )
    assert spec.dataset_content_identity == (
        "6fc636211b53bc29993c0ffa6e6c2cd13444ce1166d7e0b2af534563f2edeef8"
    )
    assert spec.baseline_path == Path(
        "experiments/arecel-dmv11/rq1-confirmatory/rq1-matched-comparison-v1.json"
    )
    assert spec.baseline_semantic_digest == (
        "9015b7b824107c9b99e34dadcc1e50e5c1a4a8d03c2b616a5bfab04450cb82dd"
    )
    assert spec.output_root == Path("experiments/arecel-dmv11/rq1-workload-generalization-v1")
    assert spec.design_format == "rq1-workload-generalization-dmv11-design-v1"
    assert spec.result_format == "rq1-workload-generalization-dmv11-v1"
    assert spec.preflight_format == "rq1-workload-generalization-dmv11-preflight-v1"
    assert spec.runtime_name == "dmv11"
    assert spec.campaign_attempt_index == 1
    assert spec.prior_failed_attempt_path is None
    assert spec.output_root not in {live.POWER7_SPEC.output_root, live.FOREST10_SPEC.output_root}


def test_dmv11_adapter_schema_preserves_eleven_column_contract() -> None:
    schema = live.DMV11_SPEC.dataset_module.schema_contract()
    assert schema["id"] == "arecel-dmv11-postgres-schema-v1"
    assert schema["relation"] == "public.dmv11"
    assert len(schema["columns"]) == 11
    assert schema["not_null"] is False
    assert schema["identifier_contract"] == "unquoted-upstream-ddl-folds-to-lowercase-catalog-names"
    assert schema["collation_contract"] == "capture-native-postgresql-collation-for-varchar-columns"


def test_dmv11_spec_matches_immutable_split_and_truth_provenance() -> None:
    spec = live.DMV11_SPEC
    source_row = next(
        row
        for row in read_json(ROOT / live.SOURCE_AUDIT_V2_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    truth_row = next(
        row
        for row in read_json(ROOT / live.TRUTH_POLICY_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    strict_row = next(
        row
        for row in read_json(ROOT / live.STRICT_UNSEEN_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    baseline = read_json(ROOT / spec.baseline_path)
    assert source_row["dataset_content_identity"] == spec.dataset_content_identity
    assert source_row["valid_workload_id"] == spec.valid_workload_id
    assert source_row["valid_workload_sha256"] == spec.valid_workload_sha256
    assert source_row["test_workload_id"] == spec.test_workload_id
    assert source_row["test_workload_sha256"] == spec.test_workload_sha256
    assert source_row["valid_query_count"] == source_row["test_query_count"] == 10_000
    assert source_row["valid_unique_query_hash_count"] == 8_490
    assert source_row["test_unique_query_hash_count"] == 8_482
    assert source_row["valid_test_unique_hash_overlap_count"] == 497
    assert truth_row["valid_observations_path"] == spec.valid_observations_path.as_posix()
    assert truth_row["valid_observations_sha256"] == spec.valid_observations_sha256
    assert truth_row["test_observations_path"] == spec.test_observations_path.as_posix()
    assert truth_row["test_observations_sha256"] == spec.test_observations_sha256
    assert strict_row["strict_unseen_count"] == 8_138
    assert strict_row["seen_in_valid_count"] == 1_862
    assert strict_row["strict_unseen_count"] / 10_000 == 0.8138
    assert baseline["dataset"]["dataset_id"] == spec.dataset_id
    assert baseline["workload"]["workload_id"] == spec.test_workload_id


def test_dmv11_valid_truth_identity_reads_no_evaluation_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    identity = live._valid_truth_identity(ROOT, live.DMV11_SPEC)
    assert identity["source_kind"] == "authoritative-external-exact"
    assert identity["workload_id"] == "arecel_dmv11_valid_v1"
    assert identity["query_count"] == 10_000


def test_dmv11_preflight_is_design_safe_and_attempt_one(monkeypatch: pytest.MonkeyPatch) -> None:
    producer = "1" * 40

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        if "rev-parse" in command:
            return Completed(producer + "\n")
        if "status" in command:
            return Completed("")
        raise AssertionError(f"unexpected live command during preflight: {command}")

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    monkeypatch.setattr(
        live,
        "_design_source_spec",
        lambda root, data_root=None, spec=live.DMV11_SPEC: {
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
    value = live.build_dmv11_rq1b_preflight(
        research_root=ROOT,
        producer_sha=producer,
    )
    serialized = json.dumps(value, sort_keys=True)
    assert value["campaign_attempt_index"] == 1
    assert value["prior_failed_attempt"] is None
    assert value["research_commit_sha"] == producer
    assert value["preseal_evaluation_content_unread"] is True
    assert "arecel_dmv11_test_" not in serialized
    assert "strict_unseen_count" not in serialized
    assert "rq1-confirmatory" in serialized
    assert live.validate_dmv11_rq1b_preflight(value, research_root=ROOT)["status"] == "valid"


def test_dmv11_baseline_binding_is_dataset_local() -> None:
    binding = live._baseline_binding(ROOT, live.DMV11_SPEC)
    assert binding["path"] == live.DMV11_SPEC.baseline_path.as_posix()
    assert binding["semantic_digest"] == live.DMV11_SPEC.baseline_semantic_digest
    assert binding["s_test_candidate_ids"]

    swapped = replace(
        live.DMV11_SPEC,
        baseline_path=live.POWER7_SPEC.baseline_path,
        baseline_semantic_digest=live.POWER7_SPEC.baseline_semantic_digest,
    )
    with pytest.raises(live.RQ1BValidationError):
        live._baseline_binding(ROOT, swapped)


def test_dmv11_strict_unseen_selects_its_membership_row() -> None:
    membership = read_json(ROOT / live.STRICT_UNSEEN_PATH)
    records = [
        {
            "query_id": f"arecel_dmv11_test_{index:06d}",
            "estimate": 1.0,
            "truth": 1,
            "weight": 1.0,
            "qerror": 1.0,
        }
        for index in range(live.SAMPLE_ROWS)
    ]
    selected = live.strict_unseen_filter(records, membership, spec=live.DMV11_SPEC)
    assert len(selected) == 8_138
    assert len(selected) < len(records)
    dmv_membership = next(
        row for row in membership["datasets"] if row["dataset_id"] == live.DMV11_SPEC.dataset_id
    )
    seen = set(dmv_membership["seen_in_valid_test_query_ids"])
    assert all(row["query_id"] not in seen for row in selected)


def test_dmv11_cross_dataset_artifacts_fail_closed() -> None:
    power_design = read_json(ROOT / live.POWER7_SPEC.output_root / "design-v1.json")
    power_result = read_json(ROOT / live.POWER7_SPEC.output_root / "result-v1.json")
    with pytest.raises(live.RQ1BValidationError):
        live.validate_design_artifact(power_design, _spec=live.DMV11_SPEC)
    with pytest.raises(live.RQ1BValidationError):
        live.validate_rq1b_result(power_result, spec=live.DMV11_SPEC)


def test_dmv11_cli_routes_without_invoking_live_work() -> None:
    parser = _parser()
    preflight = parser.parse_args(["rq1-generalization", "dmv11", "preflight-create"])
    run = parser.parse_args(
        [
            "rq1-generalization",
            "dmv11",
            "run",
            "--stock-dsn",
            "stock",
            "--planner-dsn",
            "planner",
            "--preflight",
            "preflight.json",
        ]
    )
    assert preflight.rq1g_live_command == "dmv11"
    assert preflight.rq1g_dataset_command == "preflight-create"
    assert run.rq1g_live_command == "dmv11"
    assert run.rq1g_dataset_command == "run"


def test_dmv11_result_and_design_formats_are_not_power7_or_forest10() -> None:
    assert live.DMV11_SPEC.design_format not in {
        live.POWER7_SPEC.design_format,
        live.FOREST10_SPEC.design_format,
    }
    assert live.DMV11_SPEC.result_format not in {
        live.POWER7_SPEC.result_format,
        live.FOREST10_SPEC.result_format,
    }
