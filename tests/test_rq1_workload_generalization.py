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
    PROTOCOL_V2_FORMAT,
    SOURCE_AUDIT_FORMAT,
    SOURCE_AUDIT_V1_DIGEST,
    SOURCE_AUDIT_V2_FORMAT,
    STRICT_UNSEEN_FORMAT,
    validate_protocol,
    validate_protocol_v2,
    validate_source_audit,
    validate_source_audit_v2,
    validate_strict_unseen_membership,
    validate_truth_policy,
)

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "paper/rq1-workload-generalization-protocol-v1.json"
TRUTH_POLICY = ROOT / "paper/rq1-workload-generalization-truth-policy-v1.json"
SOURCE_AUDIT = ROOT / "experiments/rq1-workload-generalization-source-audit-v1.json"
PROTOCOL_V2 = ROOT / "paper/rq1-workload-generalization-protocol-v2.json"
SOURCE_AUDIT_V2 = ROOT / "experiments/rq1-workload-generalization-source-audit-v2.json"
STRICT_UNSEEN = ROOT / "experiments/rq1-workload-generalization-strict-unseen-v1.json"
ATTEMPT5_FAILURE = ROOT / (
    "experiments/arecel-power7/rq1-workload-generalization-v1/"
    "failed-attempts/attempt-005/failure-v1.json"
)
ATTEMPT5_CORRECTION = ROOT / (
    "experiments/arecel-power7/rq1-workload-generalization-v1/"
    "failed-attempts/attempt-005/failure-correction-v1.json"
)


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


def test_v1_artifacts_remain_immutable_and_v2_binds_them() -> None:
    assert json.loads(PROTOCOL.read_text(encoding="utf-8"))["semantic_digest"] == (
        "7c8d959a4388fc484013e3736140e9b49aa1060bad22736065a60522c0b3e83d"
    )
    assert json.loads(TRUTH_POLICY.read_text(encoding="utf-8"))["semantic_digest"] == (
        "e880f0f5b6cebd468fbadcd235408ba3a4b07ee9313f4705d11cf8f4fe83d36a"
    )
    assert json.loads(SOURCE_AUDIT.read_text(encoding="utf-8"))["semantic_digest"] == (
        SOURCE_AUDIT_V1_DIGEST
    )
    assert validate_source_audit_v2(SOURCE_AUDIT_V2, ROOT)["status"] == "valid"
    v2 = json.loads(SOURCE_AUDIT_V2.read_text(encoding="utf-8"))
    assert v2["format_version"] == SOURCE_AUDIT_V2_FORMAT
    assert v2["supersedes"] == {
        "path": "experiments/rq1-workload-generalization-source-audit-v1.json",
        "semantic_digest": SOURCE_AUDIT_V1_DIGEST,
    }


def test_v2_instance_accounting_distinguishes_hashes_and_instances() -> None:
    audit = json.loads(SOURCE_AUDIT_V2.read_text(encoding="utf-8"))
    observed = {row["dataset_id"]: row for row in audit["datasets"]}
    for row in observed.values():
        assert (
            row["test_instance_count_with_hash_seen_in_valid"]
            + row["strict_unseen_test_instance_count"]
            == 10_000
        )
        assert (
            row["valid_duplicate_instance_count"] == 10_000 - row["valid_unique_query_hash_count"]
        )
        assert row["test_duplicate_instance_count"] == 10_000 - row["test_unique_query_hash_count"]
    assert observed["arecel-forest10"]["strict_unseen_test_instance_count"] == 10_000
    assert observed["arecel-power7"]["strict_unseen_test_instance_count"] == 10_000
    assert observed["arecel-census13"]["test_instance_count_with_hash_seen_in_valid"] > 137
    assert observed["arecel-dmv11"]["test_instance_count_with_hash_seen_in_valid"] > 497


def test_strict_unseen_membership_is_canonical_and_evaluation_only() -> None:
    assert validate_strict_unseen_membership(STRICT_UNSEEN, ROOT)["status"] == "valid"
    value = json.loads(STRICT_UNSEEN.read_text(encoding="utf-8"))
    assert value["format_version"] == STRICT_UNSEEN_FORMAT
    for row in value["datasets"]:
        all_ids = [
            f"{row['test_workload_id'].removesuffix('_v1')}_{index:06d}" for index in range(10_000)
        ]
        seen = row["seen_in_valid_test_query_ids"]
        strict = row["strict_unseen_test_query_ids"]
        assert [int(item.rsplit("_", 1)[1]) for item in seen] == sorted(
            int(item.rsplit("_", 1)[1]) for item in seen
        )
        assert [int(item.rsplit("_", 1)[1]) for item in strict] == sorted(
            int(item.rsplit("_", 1)[1]) for item in strict
        )
        assert set(seen).isdisjoint(strict)
        assert set(seen) | set(strict) == set(all_ids)
        assert all("SELECT" not in item for item in seen + strict)


def test_protocol_v2_freezes_full_test_primary_and_strict_unseen_secondary() -> None:
    assert validate_protocol_v2(PROTOCOL_V2)["status"] == "valid"
    value = json.loads(PROTOCOL_V2.read_text(encoding="utf-8"))
    assert value["format_version"] == PROTOCOL_V2_FORMAT
    assert value["primary_evaluation_population"]["query_count"] == 10_000
    assert value["secondary_evaluation_population"]["evaluation_only"] is True
    forbidden = set(value["leakage_contract"]["forbidden"])
    assert "strict-unseen membership before Recommendation is sealed" in forbidden
    assert value["future_evaluation_efficiency"].startswith(
        "evaluate the full test split exactly once"
    )


def test_v2_mutations_fail_closed(tmp_path: Path) -> None:
    value = json.loads(SOURCE_AUDIT_V2.read_text(encoding="utf-8"))
    value["datasets"][0]["strict_unseen_test_instance_count"] += 1
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    path = tmp_path / "mutated-audit-v2.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_source_audit_v2(path, ROOT)

    membership = json.loads(STRICT_UNSEEN.read_text(encoding="utf-8"))
    membership["membership_key"] = "adapted PostgreSQL SQL"
    membership["semantic_digest"] = semantic_digest(
        {key: item for key, item in membership.items() if key != "semantic_digest"}
    )
    path = tmp_path / "mutated-membership.json"
    path.write_text(json.dumps(membership), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_strict_unseen_membership(path, ROOT)


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


def test_attempt5_failure_correction_is_append_only_and_signed() -> None:
    original = json.loads(ATTEMPT5_FAILURE.read_text(encoding="utf-8"))
    correction = json.loads(ATTEMPT5_CORRECTION.read_text(encoding="utf-8"))
    assert original["semantic_digest"] == (
        "4c5aeef9248d1073538d091ba74f32b8330c065f0bd858028f841e59d3b08931"
    )
    assert correction["supersedes"] == {
        "path": "experiments/arecel-power7/rq1-workload-generalization-v1/"
        "failed-attempts/attempt-005/failure-v1.json",
        "semantic_digest": original["semantic_digest"],
    }
    assert correction["semantic_digest"] == semantic_digest(
        {key: item for key, item in correction.items() if key != "semantic_digest"}
    )
    assert correction["failure_stage"] == "design-stage-sandbox-prepare"
    assert correction["catalog_contract"]["contract_mismatch_present"] is True
    assert correction["evidence_eligibility"] is False


def test_paper_registry_keeps_rq1a_complete_and_registers_rq1b() -> None:
    spec = load_paper_spec(ROOT / "paper/paper-experiment-v1.json")
    assert validate_paper_spec(spec)["status"] == "valid"
    by_id = {item["experiment_id"]: item for item in spec["experiments"]}
    assert by_id["rq1-confirmatory-matched-baselines"]["status"] == "complete"
    rq1b = by_id["rq1-held-out-workload-generalization"]
    assert rq1b["status"] == "preregistered"
    assert rq1b["design_split"] == "valid"
    assert rq1b["evaluation_split"] == "test"
    assert rq1b["formal_execution"] == "four-dataset-children-complete"
    assert rq1b["formal_evidence_status"] == "four-dataset-children-complete"
    assert rq1b["next_formal_attempt"] == "none-pending-review"
    assert rq1b["cross_dataset_synthesis_status"] == "complete"
    assert rq1b["cross_dataset_synthesis"]["artifact"] == (
        "experiments/rq1-workload-generalization-cross-dataset-v1.json"
    )
    assert rq1b["cross_dataset_synthesis"]["derived_offline"] is True
    assert rq1b["cross_dataset_synthesis"]["scientific_reexecution"] is False
    assert rq1b["power7_runner_readiness"] == "formal-evidence-published"
    assert rq1b["forest10_runner_readiness"] == "formal-evidence-published"
    assert rq1b["forest10_formal_evidence_status"] == "complete"
    assert rq1b["forest10_next_formal_attempt"] == "none-pending-until-review"
    assert len(rq1b["forest10_formal_attempts"]) == 1
    forest_attempt = rq1b["forest10_formal_attempts"][-1]
    assert forest_attempt["attempt_index"] == 1
    assert forest_attempt["status"] == "complete"
    assert forest_attempt["scientific_pipeline_status"] == "complete"
    assert forest_attempt["evidence_eligible"] is True
    assert forest_attempt["formal_invocation_count"] == 1
    assert forest_attempt["producer_sha"] == "85b9721bd2a381a6253046fa16ee7f5ba2d7d59c"
    assert forest_attempt["preflight_semantic_digest"] == (
        "06e18f9631b6066b19c00351a388ba42514c0181409c6f7b8d1b54fcd0157dd8"
    )
    assert forest_attempt["design_semantic_digest"] == (
        "3a77258a694a880270d80420be1f9b3359383b28a98a36b419560cc2c8d54a31"
    )
    assert forest_attempt["deployment_semantic_digest"] == (
        "d24621881b1bdabb36d2bac156df13db3a95f439f0c7483784c5606191aa6cc8"
    )
    assert forest_attempt["per_query_sha256"] == (
        "40753a85902396e4cf48bfd9f8b0c0a4fd0c1ec79705d72bade19d3f2373732a"
    )
    assert forest_attempt["result_semantic_digest"] == (
        "106809bb0ad45ee6aa0ac0a238eb22299a17e0f4d396e883ef68718d8234d8dd"
    )
    assert rq1b["forest10_formal_evidence"]["result_path"] == (
        "experiments/arecel-forest10/rq1-workload-generalization-v1/result-v1.json"
    )
    assert rq1b["dmv11_runner_readiness"] == "formal-evidence-published"
    assert rq1b["dmv11_formal_evidence_status"] == "complete"
    assert rq1b["dmv11_next_formal_attempt"] == "none-pending-until-review"
    assert len(rq1b["dmv11_formal_attempts"]) == 1
    dmv_attempt = rq1b["dmv11_formal_attempts"][-1]
    assert dmv_attempt["attempt_index"] == 1
    assert dmv_attempt["status"] == "complete"
    assert dmv_attempt["scientific_pipeline_status"] == "complete"
    assert dmv_attempt["evidence_eligible"] is True
    assert dmv_attempt["formal_invocation_count"] == 1
    assert dmv_attempt["producer_sha"] == ("d426a1c2bc59284c82f44f5963c62a75f890916f")
    assert dmv_attempt["preflight_semantic_digest"] == (
        "a9849f53965ffa3e903039b2a17a8368efefb6871d43c88bc7b1e70761c10cad"
    )
    assert dmv_attempt["design_semantic_digest"] == (
        "550da1e174ad16680d270f531bff4eeb0dc03421c6773b5a7d73a6c88e2172bf"
    )
    assert dmv_attempt["deployment_semantic_digest"] == (
        "0a62dc07fd4919f1f4c8903b18d7961016a5c207a873d34e6df84169bd2b18d2"
    )
    assert dmv_attempt["per_query_sha256"] == (
        "8745be88f458528f8e52e2d9ec95cbf89d5e31cd04451ba9064948dbeb427654"
    )
    assert dmv_attempt["result_semantic_digest"] == (
        "f7aabb8ac7839457cb49bd3dd21e57e482a9967141ef45174293b5c06422b53c"
    )
    assert rq1b["dmv11_formal_evidence"]["result_path"] == (
        "experiments/arecel-dmv11/rq1-workload-generalization-v1/result-v1.json"
    )
    assert rq1b["forest10_formal_evidence"]["producer_sha"] == (
        "85b9721bd2a381a6253046fa16ee7f5ba2d7d59c"
    )
    assert rq1b["forest10_formal_evidence"]["evidence_eligible"] is True
    assert rq1b["forest10_formal_evidence"]["result_semantic_digest"] == (
        "106809bb0ad45ee6aa0ac0a238eb22299a17e0f4d396e883ef68718d8234d8dd"
    )
    assert len(rq1b["power7_formal_attempts"]) == 6
    assert rq1b["power7_formal_attempts"][-1]["status"] == "complete"
    assert rq1b["power7_formal_attempts"][-1]["evidence_eligible"] is True
    assert rq1b["power7_formal_attempts"][-1]["result_semantic_digest"] == (
        "1af0a8d65a78f6c6c19e63bcf2a8f1f8ab39f6a9a24dc0f00a8266e44508e9a3"
    )
    assert rq1b["power7_formal_attempts"][-2]["correction_artifact"]["semantic_digest"] == (
        "7e22906424e734c4726e0d7853d61bb8ed17c3e9f1cb1245fbcf5f8e4c5d2ff5"
    )
    assert rq1b["power7_formal_attempts"][-1]["result_digest_correction"]["semantic_digest"] == (
        "47cef82c9b1abe3739ad3a7192f192589d57a61c732dfb74a7c2869973e76ea5"
    )
    assert rq1b["protocol"] == "paper/rq1-workload-generalization-protocol-v2.json"
    assert rq1b["source_audit"] == "experiments/rq1-workload-generalization-source-audit-v2.json"
    assert (
        rq1b["strict_unseen_membership"]
        == "experiments/rq1-workload-generalization-strict-unseen-v1.json"
    )
    assert rq1b["protocol_v1_historical"]["semantic_digest"] == (
        "7c8d959a4388fc484013e3736140e9b49aa1060bad22736065a60522c0b3e83d"
    )
    assert spec["status_ledger"]["allowed_statuses"]
    assert "preregistered" in spec["status_ledger"]["allowed_statuses"]
