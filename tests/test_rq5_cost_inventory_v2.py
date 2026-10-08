from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq5_cost_inventory import (
    FORMAT_VERSION_V2,
    build_inventory_v2,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v1.json"
V2_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v2.json"


def _v2() -> dict:
    return json.loads(V2_PATH.read_text(encoding="utf-8"))


def _redigest(value: dict) -> dict:
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    return value


def _mutated_artifact(mutation, tmp_path: Path) -> Path:
    value = copy.deepcopy(_v2())
    mutation(value)
    path = tmp_path / "mutated-v2.json"
    path.write_text(json.dumps(_redigest(value)), encoding="utf-8")
    return path


def test_inventory_v2_is_source_derived_and_incomplete() -> None:
    artifact = _v2()
    result = validate_inventory(V2_PATH, ROOT)
    assert result["status"] == "valid"
    assert artifact["format_version"] == FORMAT_VERSION_V2
    assert artifact["rq5_completion_status"] == "incomplete"
    assert artifact["rq5_registry_status"] == "planned"
    assert artifact["supersedes_inventory"]["path"].endswith("inventory-v1.json")
    assert artifact["supersedes_inventory"]["semantic_digest"] == (
        "54f846c0c50e8db787b87b0ad5caa50a2e180e7983ea963a9ee4d823cb0e5eab"
    )
    assert artifact["semantic_digest"] == build_inventory_v2(ROOT)["semantic_digest"]


def test_v1_remains_immutable_and_static_deployment_closes_three_gaps() -> None:
    v1 = json.loads(V1_PATH.read_text(encoding="utf-8"))
    assert v1["semantic_digest"] == (
        "54f846c0c50e8db787b87b0ad5caa50a2e180e7983ea963a9ee4d823cb0e5eab"
    )
    artifact = _v2()
    for stage in ("deployment DDL", "deployment ANALYZE", "catalog/storage"):
        assert artifact["stages"][stage]["measurement_status"] == "formal-measured"
        assert artifact["stages"][stage]["unmeasured_required_metrics"] == []
    assert artifact["stages"]["post-deployment planning"]["measurement_status"] == "formal-measured"
    assert artifact["stages"]["post-deployment planning"]["unmeasured_required_metrics"] == []
    assert artifact["stages"]["snapshot capture"]["measured_metrics"]["metric_status"] == {
        "capture_time": "formal-measured",
        "snapshot_bytes": "missing",
    }
    assert artifact["stages"]["refresh"]["measurement_status"] == "missing"


def test_v2_static_headline_and_claim_readiness() -> None:
    artifact = _v2()
    rows = {row["dataset_id"]: row for row in artifact["static_deployment_cost"]["headline_rows"]}
    assert [rows[d]["selected_k"] for d in rows] == [7, 8, 7, 5]
    assert all(
        artifact["static_deployment_cost"]["deployment_fractions"][d][
            "ddl_median_less_than_analyze_median"
        ]
        for d in rows
    )
    claims = artifact["claim_readiness"]
    for key in (
        "can_claim_external_truth_import_cost",
        "can_claim_incremental_singleton_savings",
        "can_claim_search_scaling_cost",
        "can_claim_deployment_ddl_cost",
        "can_claim_deployment_analyze_cost",
        "can_claim_catalog_storage_cost",
        "can_claim_post_deployment_planning_cost",
    ):
        assert claims[key] is True
    for key in (
        "can_claim_production_exact_truth_cost",
        "can_claim_refresh_cost",
        "can_claim_refresh_quality_trend",
        "can_claim_end_to_end_advisor_cost",
    ):
        assert claims[key] is False
    assert artifact["protocol_required_unresolved"]
    assert artifact["optional_external_validity_gaps"] == [
        {
            "stage": "post-deployment planning",
            "missing_metric": "production_online_query_latency",
            "reason": "offline EXPLAIN timing is not online latency",
            "completion_blocker": False,
        }
    ]
    assert "total_advisor_seconds" not in artifact


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["supersedes_inventory"].__setitem__("semantic_digest", "0" * 64),
        lambda value: value["source_evidence"]["static_deployment"]["summary"].__setitem__(
            "semantic_digest", "0" * 64
        ),
        lambda value: value["static_deployment_cost"]["headline_rows"][0].__setitem__(
            "ddl_seconds_median", 0
        ),
        lambda value: value["stages"]["deployment DDL"].__setitem__(
            "measurement_status", "missing"
        ),
        lambda value: value["stages"]["post-deployment planning"].__setitem__(
            "unmeasured_required_metrics", ["production_online_query_latency"]
        ),
        lambda value: value["stages"]["refresh"].__setitem__(
            "measurement_status", "formal-measured"
        ),
        lambda value: value["claim_readiness"].__setitem__(
            "can_claim_end_to_end_advisor_cost", True
        ),
        lambda value: value.__setitem__("total_advisor_seconds", 1),
    ],
)
def test_inventory_v2_rejects_semantic_mutations(mutation, tmp_path: Path) -> None:
    path = _mutated_artifact(mutation, tmp_path)
    with pytest.raises(ValueError):
        validate_inventory(path, ROOT)


def test_v2_cli_is_offline_only() -> None:
    args = _parser().parse_args(["validate", "rq5-cost", "inventory-v2"])
    assert args.rq5_cost_command == "inventory-v2"
    assert not hasattr(args, "dsn")
    assert not hasattr(args, "patched_dsn")
