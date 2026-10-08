from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq5_cost_inventory import (
    FORMAT_VERSION_V3,
    build_inventory_v3,
    validate_inventory,
)

ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v1.json"
V2_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v2.json"
V3_PATH = ROOT / "experiments/rq5-existing-trace-cost-inventory-v3.json"


def _v3() -> dict:
    return json.loads(V3_PATH.read_text(encoding="utf-8"))


def _redigest(value: dict) -> dict:
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    return value


def _mutated_artifact(mutation, tmp_path: Path) -> Path:
    value = copy.deepcopy(_v3())
    mutation(value)
    path = tmp_path / "mutated-v3.json"
    path.write_text(json.dumps(_redigest(value)), encoding="utf-8")
    return path


def test_inventory_v3_is_source_derived_and_closes_snapshot_gap() -> None:
    artifact = _v3()
    result = validate_inventory(V3_PATH, ROOT)
    assert result["status"] == "valid"
    assert artifact["format_version"] == FORMAT_VERSION_V3
    assert artifact["rq5_completion_status"] == "incomplete"
    assert artifact["rq5_registry_status"] == "planned"
    assert artifact["supersedes_inventory"] == {
        "path": "experiments/rq5-existing-trace-cost-inventory-v2.json",
        "semantic_digest": "de6d4f3291735dfdf03007adfc92678cfc31f11c8ef3f813b25b3b0ac271230b",
    }
    assert artifact["semantic_digest"] == build_inventory_v3(ROOT)["semantic_digest"]
    assert artifact["stages"]["snapshot capture"]["measurement_status"] == "formal-measured"
    assert artifact["stages"]["snapshot capture"]["unmeasured_required_metrics"] == []
    assert artifact["snapshot_footprint"]["logical_size_is_not_filesystem_allocation"] is True
    assert artifact["snapshot_footprint"]["logical_size_is_not_compressed_size"] is True
    assert artifact["snapshot_footprint"]["logical_size_is_not_manifest_only"] is True
    assert artifact["protocol_required_unresolved"] == [
        item for item in artifact["remaining_measurement_gaps"]
    ]
    assert all(
        artifact["snapshot_footprint"]["per_dataset"][dataset]["snapshot_semantic_digests_equal"]
        is False
        for dataset in ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
    )


def test_v1_and_v2_remain_immutable() -> None:
    v1 = json.loads(V1_PATH.read_text(encoding="utf-8"))
    v2 = json.loads(V2_PATH.read_text(encoding="utf-8"))
    assert v1["semantic_digest"] == (
        "54f846c0c50e8db787b87b0ad5caa50a2e180e7983ea963a9ee4d823cb0e5eab"
    )
    assert v2["semantic_digest"] == (
        "de6d4f3291735dfdf03007adfc92678cfc31f11c8ef3f813b25b3b0ac271230b"
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value["source_evidence"]["snapshot_footprint"]["summary"].__setitem__(
            "semantic_digest", "0" * 64
        ),
        lambda value: value["source_evidence"]["snapshot_footprint"]["raw_children"][0].__setitem__(
            "semantic_digest", "0" * 64
        ),
        lambda value: value["source_evidence"]["snapshot_footprint"].__setitem__(
            "producer_sha", "0" * 40
        ),
        lambda value: value["snapshot_footprint"]["per_dataset"]["arecel-census13"][
            "sealed_snapshot_logical_bytes"
        ].__setitem__("median", 0),
        lambda value: value["snapshot_footprint"]["per_dataset"]["arecel-census13"][
            "sample_payload_bytes_total"
        ].__setitem__("median", 0),
        lambda value: value["stages"]["snapshot capture"]["unmeasured_required_metrics"].append(
            "snapshot_bytes"
        ),
        lambda value: value["snapshot_footprint"].__setitem__(
            "size_definition", "filesystem allocation bytes"
        ),
        lambda value: value["stages"]["refresh"].__setitem__(
            "measurement_status", "formal-measured"
        ),
        lambda value: value["claim_readiness"].__setitem__(
            "can_claim_production_exact_truth_cost", True
        ),
    ],
)
def test_inventory_v3_rejects_semantic_mutations(mutation, tmp_path: Path) -> None:
    path = _mutated_artifact(mutation, tmp_path)
    with pytest.raises(ValueError):
        validate_inventory(path, ROOT)


def test_v3_does_not_require_digest_equality() -> None:
    artifact = _v3()
    for dataset in artifact["snapshot_footprint"]["per_dataset"].values():
        assert dataset["snapshot_semantic_digests_equal"] is False
        assert dataset["sample_payload_sha256_stable"] is True
        assert dataset["workload_sha256_stable"] is True


def test_v3_cli_is_offline_only() -> None:
    args = _parser().parse_args(["validate", "rq5-cost", "inventory-v3"])
    assert args.rq5_cost_command == "inventory-v3"
    assert not hasattr(args, "dsn")
    assert not hasattr(args, "patched_dsn")
