from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq5_cost_inventory import (
    FORMAT_VERSION,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
INVENTORY_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v1.json"


def _inventory() -> dict:
    return json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))


def _redigest(value: dict) -> dict:
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    return value


def test_inventory_is_offline_and_valid() -> None:
    artifact = _inventory()
    result = validate_inventory(INVENTORY_PATH, ROOT)
    assert result["status"] == "valid"
    assert artifact["format_version"] == FORMAT_VERSION
    assert artifact["rq5_completion_status"] == "incomplete"
    assert artifact["rq5_registry_status"] == "planned"
    assert artifact["new_postgresql_execution"] is False
    assert artifact["new_planner_execution"] is False
    assert artifact["no_synthetic_total"] is True


def test_all_four_rq2_children_are_projected_exactly() -> None:
    rows = _inventory()["formal_operational_costs"]
    assert [row["dataset_id"] for row in rows] == [
        "arecel-census13",
        "arecel-forest10",
        "arecel-power7",
        "arecel-dmv11",
    ]
    assert all(row["sample_pair_total_planner_calls"] == 20000 for row in rows)
    assert all(row["deployment_plus_analyze_seconds"] > 0 for row in rows)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["source_evidence"]["rq2_children"][0].__setitem__(
            "semantic_digest", "0" * 64
        ),
        lambda value: value["source_evidence"]["rq4_ks_cross_dataset_summary"].__setitem__(
            "semantic_digest", "0" * 64
        ),
        lambda value: value["source_evidence"]["singleton_equivalence"][0].__setitem__(
            "artifact_digest", "0" * 64
        ),
        lambda value: value["formal_operational_costs"][0].__setitem__(
            "sample_baseline_eval_seconds", 0
        ),
        lambda value: value["stages"]["authoritative external truth import/validation"].__setitem__(
            "evidence_class", "production exact truth"
        ),
        lambda value: value["stages"]["deployment DDL"]["measured_metrics"].__setitem__(
            "DDL_only_seconds", 1.0
        ),
        lambda value: value["stages"]["catalog/storage"]["measured_metrics"].__setitem__(
            "catalog_bytes", 123
        ),
        lambda value: value["stages"]["refresh"].__setitem__(
            "measurement_status", "formal-measured"
        ),
    ],
)
def test_inventory_rejects_semantic_mutations(mutation) -> None:
    broken = copy.deepcopy(_inventory())
    mutation(broken)
    _redigest(broken)
    broken_path = ROOT / "experiments/rq5-existing-trace-cost-inventory-mutated.json"
    broken_path.write_text(json.dumps(broken), encoding="utf-8")
    try:
        with pytest.raises(ValueError):
            validate_inventory(broken_path, ROOT)
    finally:
        broken_path.unlink()


def test_inventory_exposes_explicit_missing_and_historical_evidence() -> None:
    artifact = _inventory()
    assert artifact["stages"]["snapshot capture"]["unmeasured_required_metrics"] == [
        "snapshot_bytes"
    ]
    assert artifact["stages"]["production exact truth acquisition"]["measurement_status"] == (
        "evidence-exists-timing-missing"
    )
    assert artifact["stages"]["deployment DDL"]["measurement_status"] == (
        "evidence-exists-timing-missing"
    )
    assert artifact["stages"]["deployment ANALYZE"]["measurement_status"] == (
        "evidence-exists-timing-missing"
    )
    assert artifact["stages"]["refresh"]["measurement_status"] == "missing"
    assert artifact["historical_preflight_timings"]
    assert all(
        row["included_in_primary_cost_table"] is False
        for row in artifact["historical_preflight_timings"]
    )


def test_inventory_keeps_b4_search_separate_from_canonical_b8_cost() -> None:
    artifact = _inventory()
    assert artifact["interpretation"]["ks_sensitivity_is_B4_not_canonical_B8"] is True
    assert {row["B"] for row in artifact["search_scaling_evidence"]} == {4}
    assert all(
        row["canonical_production_cost_measurement"] is False
        for row in artifact["search_scaling_evidence"]
    )


def test_inventory_has_no_end_to_end_cost_claim() -> None:
    artifact = _inventory()
    assert artifact["claim_readiness"]["can_claim_end_to_end_advisor_cost"] is False
    assert "total_advisor_seconds" not in artifact
    assert artifact["claim_readiness"]["can_claim_production_exact_truth_cost"] is False


def test_inventory_command_is_offline_only() -> None:
    args = _parser().parse_args(["validate", "rq5-cost", "inventory"])
    assert args.rq5_cost_command == "inventory"
    assert not hasattr(args, "dsn")
    assert not hasattr(args, "patched_dsn")
