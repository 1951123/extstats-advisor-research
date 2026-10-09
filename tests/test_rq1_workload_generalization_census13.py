from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from extstats_advisor_research import rq1_workload_generalization_live as live
from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import read_json, semantic_digest

ROOT = Path(__file__).resolve().parents[1]


def test_census13_spec_binds_immutable_provenance_and_schema() -> None:
    spec = live.CENSUS13_SPEC
    assert spec.dataset_id == "arecel-census13"
    assert spec.cli_name == "census13"
    assert spec.dataset_module.RELATION == "public.census13"
    assert spec.dataset_module.EXPECTED_ROWS == 48_842
    assert spec.valid_workload_id == "arecel_census13_valid_v1"
    assert spec.valid_workload_sha256 == (
        "9e616db0383ec10fc784797964338ff4fa1e69726fa007fa7f4a0515c41b2a0f"
    )
    assert spec.valid_canonical_workload_sha256 == (
        "9bcfc868effee9a796fff08454eeb85f135049e862ee0eb2dd801831d7df9388"
    )
    assert spec.test_workload_id == "arecel_census13_test_v1"
    assert spec.test_workload_sha256 == (
        "84d2fadeacddec2a65f0a2f01d7852eb09681dd847989eb27e380da73d474fc9"
    )
    assert spec.baseline_path == Path(
        "experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json"
    )
    assert spec.baseline_semantic_digest == (
        "647d09ecb0bdedce426f71c2527a5f30c3c413da4d4c670d4c5e22710b906fec"
    )
    assert spec.output_root == Path("experiments/arecel-census13/rq1-workload-generalization-v1")
    assert spec.design_format == "rq1-workload-generalization-census13-design-v1"
    assert spec.result_format == "rq1-workload-generalization-census13-v1"
    assert spec.preflight_format == "rq1-workload-generalization-census13-preflight-v1"
    assert spec.campaign_attempt_index == 1
    assert spec.prior_failed_attempt_path is None

    schema = spec.dataset_module.schema_contract()
    assert schema["id"] == "arecel-census13-postgres-schema-v1"
    assert len(schema["columns"]) == 13
    assert sum(type_ == "DOUBLE PRECISION" for _, type_ in spec.dataset_module.COLUMNS) == 5
    assert sum(type_ == "VARCHAR(64)" for _, type_ in spec.dataset_module.COLUMNS) == 8


def test_census13_spec_matches_source_truth_and_strict_unseen_contract() -> None:
    spec = live.CENSUS13_SPEC
    source = next(
        row
        for row in read_json(ROOT / live.SOURCE_AUDIT_V2_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    truth = next(
        row
        for row in read_json(ROOT / live.TRUTH_POLICY_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    strict = next(
        row
        for row in read_json(ROOT / live.STRICT_UNSEEN_PATH)["datasets"]
        if row["dataset_id"] == spec.dataset_id
    )
    assert source["dataset_content_identity"] == spec.dataset_content_identity
    assert source["valid_workload_sha256"] == spec.valid_workload_sha256
    assert source["test_workload_sha256"] == spec.test_workload_sha256
    assert source["valid_unique_query_hash_count"] == 9_472
    assert source["test_unique_query_hash_count"] == 9_476
    assert source["valid_test_unique_hash_overlap_count"] == 137
    assert source["strict_unseen_test_instance_count"] == 9_386
    assert source["test_instance_count_with_hash_seen_in_valid"] == 614
    assert truth["valid_observations_sha256"] == spec.valid_observations_sha256
    assert truth["test_observations_sha256"] == spec.test_observations_sha256
    assert strict["strict_unseen_count"] == 9_386
    assert strict["seen_in_valid_count"] == 614
    assert strict["strict_unseen_count"] / 10_000 == 0.9386


def test_census13_preflight_is_in_memory_design_safe_and_attempt_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = "1" * 40

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def fake_run(command: list[str], **kwargs: object) -> Completed:
        if "status" in command:
            return Completed("")
        if "rev-parse" in command:
            return Completed(producer + "\n")
        raise AssertionError(f"unexpected command during offline preflight: {command}")

    monkeypatch.setattr(live.subprocess, "run", fake_run)
    value = live.build_census13_rq1b_preflight(
        research_root=ROOT,
        producer_sha=producer,
    )
    serialized = json.dumps(value, sort_keys=True)
    assert value["campaign_attempt_index"] == 1
    assert value["prior_failed_attempt"] is None
    assert value["preseal_evaluation_content_unread"] is True
    assert "arecel_census13_test_" not in serialized
    assert "strict_unseen_count" not in serialized
    assert live.validate_census13_rq1b_preflight(value, research_root=ROOT)["status"] == "valid"


def test_census13_preflight_valid_truth_does_not_read_test_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    monkeypatch.setattr(live, "read_json", guarded_read_json)
    identity = live._valid_truth_identity(ROOT, live.CENSUS13_SPEC)
    assert identity["workload_id"] == "arecel_census13_valid_v1"
    assert identity["query_count"] == 10_000


def test_census13_historical_baseline_hash_exception_is_exact_and_audited() -> None:
    binding = live._baseline_binding(ROOT, live.CENSUS13_SPEC)
    assert binding["semantic_digest"] == live.CENSUS13_SPEC.baseline_semantic_digest
    assert binding["s_test_candidate_ids"]


def test_census13_historical_baseline_rejects_unsupported_hash(tmp_path: Path) -> None:
    original = read_json(ROOT / live.CENSUS13_SPEC.baseline_path)
    mutated = copy.deepcopy(original)
    mutated["workload"]["sha256"] = "0" * 64
    mutated.pop("semantic_digest", None)
    mutated["semantic_digest"] = semantic_digest(mutated)
    path = tmp_path / "baseline.json"
    path.write_text(json.dumps(mutated), encoding="utf-8")
    spec = replace(
        live.CENSUS13_SPEC,
        baseline_path=path,
        baseline_semantic_digest=mutated["semantic_digest"],
    )
    with pytest.raises(live.RQ1BValidationError, match="historical workload"):
        live._baseline_binding(ROOT, spec)


def test_census13_cross_dataset_baseline_fails_closed() -> None:
    swapped = replace(
        live.CENSUS13_SPEC,
        baseline_path=live.DMV11_SPEC.baseline_path,
        baseline_semantic_digest=live.DMV11_SPEC.baseline_semantic_digest,
    )
    with pytest.raises(live.RQ1BValidationError):
        live._baseline_binding(ROOT, swapped)


def test_census13_cross_dataset_design_and_result_fail_closed() -> None:
    power_design = read_json(ROOT / live.POWER7_SPEC.output_root / "design-v1.json")
    power_result = read_json(ROOT / live.POWER7_SPEC.output_root / "result-v1.json")
    with pytest.raises(live.RQ1BValidationError):
        live.validate_design_artifact(power_design, _spec=live.CENSUS13_SPEC)
    with pytest.raises(live.RQ1BValidationError):
        live.validate_rq1b_result(power_result, research_root=ROOT, spec=live.CENSUS13_SPEC)


def test_census13_cli_routes_without_live_invocation() -> None:
    parser = _parser()
    preflight = parser.parse_args(["rq1-generalization", "census13", "preflight-create"])
    assert preflight.rq1g_live_command == "census13"
    assert preflight.rq1g_dataset_command == "preflight-create"
    run = parser.parse_args(
        [
            "rq1-generalization",
            "census13",
            "run",
            "--stock-dsn",
            "stock",
            "--planner-dsn",
            "planner",
            "--preflight",
            "preflight.json",
        ]
    )
    assert run.rq1g_live_command == "census13"
    assert run.rq1g_dataset_command == "run"


def test_census13_output_namespace_is_distinct_from_existing_children() -> None:
    assert live.CENSUS13_SPEC.output_root not in {
        live.POWER7_SPEC.output_root,
        live.FOREST10_SPEC.output_root,
        live.DMV11_SPEC.output_root,
    }


def test_census13_registry_records_readiness_without_formal_evidence() -> None:
    registry = read_json(ROOT / "paper/paper-experiment-v1.json")
    rq1b = next(
        item
        for item in registry["experiments"]
        if item["experiment_id"] == "rq1-held-out-workload-generalization"
    )
    assert rq1b["status"] == "preregistered"
    assert rq1b["census13_runner_readiness"] == "implementation-ready"
    assert rq1b["census13_formal_evidence_status"] == "not-produced"
    assert rq1b["census13_next_formal_attempt"] == "attempt-1-pending"
    assert rq1b["census13_formal_attempts"] == []
