from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.arecel_truth import (
    authoritative_truth_spec_for_split,
    validate_observation_wire,
)
from extstats_advisor_research.datasets import DATASETS
from extstats_advisor_research.paper_spec import load_paper_spec, validate_paper_spec
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq1_workload_generalization import (
    DATASET_ORDER,
    PROTOCOL_FORMAT,
    SOURCE_AUDIT_FORMAT,
    validate_protocol,
    validate_source_audit,
    validate_truth_policy,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "paper/rq1-workload-generalization-protocol-v1.json"
TRUTH_POLICY = ROOT / "paper/rq1-workload-generalization-truth-policy-v1.json"
SOURCE_AUDIT = ROOT / "experiments/rq1-workload-generalization-source-audit-v1.json"


def test_all_audited_splits_have_exact_counts_and_namespaces() -> None:
    audit = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    observed = {row["dataset_id"]: row for row in audit["datasets"]}
    for dataset_id in DATASET_ORDER:
        row = observed[dataset_id]
        assert row["split_counts"] == {"train": 100_000, "valid": 10_000, "test": 10_000}
        slug = dataset_id.replace("-", "_")
        assert row["valid_workload_id"] == f"{slug}_valid_v1"
        assert row["test_workload_id"] == f"{slug}_test_v1"


def test_live_adapter_split_loading_is_checked_when_audited_source_is_present() -> None:
    missing = [
        dataset_id
        for dataset_id in DATASET_ORDER
        if not DATASETS[dataset_id].canonical_workload_path().is_file()
    ]
    if missing:
        pytest.skip("canonical AreCEL source is not mounted: " + ", ".join(missing))
    for dataset_id in DATASET_ORDER:
        dataset = DATASETS[dataset_id]
        valid = dataset.load_valid_records()
        test = dataset.load_test_records()
        assert len(valid) == 10_000
        assert len(test) == 10_000
        assert valid[0]["query_id"] == f"{dataset_id.replace('-', '_')}_valid_000000"
        assert test[0]["query_id"] == f"{dataset_id.replace('-', '_')}_test_000000"
        assert valid[-1]["source_index"] == 9_999
        assert test[-1]["source_index"] == 9_999


def test_workload_extraction_preserves_test_identity_and_adds_valid_identity() -> None:
    expected_test_workload_hashes = {
        "arecel-census13": "84d2fadeacddec2a65f0a2f01d7852eb09681dd847989eb27e380da73d474fc9",
        "arecel-forest10": "362b8b9afccd979a8cea25e996d6b216535a05ab175b7452574310d7644515c6",
        "arecel-power7": "649b0e422ad869e758db5dfd11b6e61a49f7ada5637252be5c7ca9e20b884eeb",
        "arecel-dmv11": "fb4f1ab89f21b34214a94e1d1bd77cf52d9e5d1e7fd9c2ee7178245832163d2d",
    }
    audit = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    observed = {row["dataset_id"]: row for row in audit["datasets"]}
    for dataset_id in DATASET_ORDER:
        row = observed[dataset_id]
        assert row["test_workload_id"] == f"{dataset_id.replace('-', '_')}_test_v1"
        assert row["test_workload_sha256"] == expected_test_workload_hashes[dataset_id]
        assert row["valid_workload_id"] == f"{dataset_id.replace('-', '_')}_valid_v1"
        assert row["valid_query_count"] == 10_000


def test_split_mixing_is_rejected() -> None:
    with pytest.raises(ValueError, match="valid or test"):
        DATASETS["arecel-forest10"].load_records(split="train")


def test_valid_truth_has_exact_wire_and_query_coverage() -> None:
    spec = authoritative_truth_spec_for_split("arecel-census13", "valid", ROOT)
    value = json.loads(spec["observations_path"].read_text(encoding="utf-8"))
    checked = validate_observation_wire(value, workload_id="arecel_census13_valid_v1")
    assert checked["query_count"] == 10_000
    assert value["truths"][0]["query_id"] == "arecel_census13_valid_000000"
    assert value["truths"][-1]["query_id"] == "arecel_census13_valid_009999"
    assert set(value) == {"format_version", "workload_id", "truths"}


def test_test_truth_policy_and_artifacts_remain_test_scoped() -> None:
    policy = json.loads((ROOT / "paper/benchmark-truth-policy-v1.json").read_text())
    assert all(item["workload_id"].endswith("_test_v1") for item in policy["datasets"])
    assert not (ROOT / "paper/benchmark-truth-policy-v1.json").read_text().count("_valid_")


def test_source_audit_records_real_counts_and_overlap() -> None:
    result = validate_source_audit(SOURCE_AUDIT, ROOT)
    assert result["status"] == "valid"
    audit = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    assert audit["format_version"] == SOURCE_AUDIT_FORMAT
    assert audit["status"] == "source-audit-complete"
    observed = {row["dataset_id"]: row for row in audit["datasets"]}
    assert all(
        row["split_counts"] == {"train": 100000, "valid": 10000, "test": 10000}
        for row in observed.values()
    )
    assert {
        key: observed[key]["valid_test_exact_query_hash_overlap_count"] for key in observed
    } == {
        "arecel-census13": 137,
        "arecel-forest10": 0,
        "arecel-power7": 0,
        "arecel-dmv11": 497,
    }


def test_protocol_forbids_train_and_workload_leakage() -> None:
    result = validate_protocol(PROTOCOL)
    assert result["status"] == "valid"
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    assert protocol["format_version"] == PROTOCOL_FORMAT
    assert protocol["design_workload"]["source_split"] == "valid"
    assert protocol["evaluation_workload"]["source_split"] == "test"
    assert protocol["no_workload_drift_claim"] is True
    forbidden = set(protocol["leakage_contract"]["forbidden"])
    assert "test truth" in forbidden
    assert "test workload SQL" in forbidden
    assert "train workload as Advisor input" in protocol["forbidden_work"]


def test_truth_policy_digest_and_source_binding_mutations_fail(tmp_path: Path) -> None:
    value = json.loads(TRUTH_POLICY.read_text(encoding="utf-8"))
    value["datasets"][0]["valid_workload_id"] = "arecel_census13_test_v1"
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    path = tmp_path / "mutated-policy.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_truth_policy(path, ROOT)


def test_source_audit_digest_and_identity_mutations_fail(tmp_path: Path) -> None:
    value = json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))
    value["datasets"][0]["dataset_content_identity"] = "0" * 64
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    path = tmp_path / "mutated-audit.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_source_audit(path, ROOT)


def test_paper_registry_keeps_rq1a_complete_and_registers_rq1b() -> None:
    spec = load_paper_spec(ROOT / "paper/paper-experiment-v1.json")
    assert validate_paper_spec(spec)["status"] == "valid"
    by_id = {item["experiment_id"]: item for item in spec["experiments"]}
    assert by_id["rq1-confirmatory-matched-baselines"]["status"] == "complete"
    rq1b = by_id["rq1-held-out-workload-generalization"]
    assert rq1b["status"] == "preregistered"
    assert rq1b["design_split"] == "valid"
    assert rq1b["evaluation_split"] == "test"
    assert rq1b["formal_execution"] == "not-started"
    assert spec["status_ledger"]["allowed_statuses"]
    assert "preregistered" in spec["status_ledger"]["allowed_statuses"]
