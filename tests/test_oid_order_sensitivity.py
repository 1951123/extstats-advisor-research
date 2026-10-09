from __future__ import annotations

import copy
from pathlib import Path

import pytest

from extstats_advisor_research.oid_order_sensitivity import (
    DATASET_SPECS,
    PREFLIGHT_PATH,
    PROTOCOL_PATH,
    deterministic_orders,
    validate_preflight,
    validate_protocol,
)
from extstats_advisor_research.provenance import read_json, semantic_digest

ROOT = Path(__file__).resolve().parents[1]


def test_order_roster_is_deterministic_and_unique() -> None:
    reference = ["a", "b", "c", "d", "e"]
    first = deterministic_orders(reference)
    assert first == deterministic_orders(reference)
    assert len(first) == 7
    assert len({tuple(item["order"]) for item in first}) == 7
    assert first[0]["kind"] == "reference"
    assert first[1]["order"] == list(reversed(reference))
    assert [item["seed"] for item in first[2:]] == [17, 29, 43, 71, 101]


def test_published_protocol_and_valid_only_preflight_validate() -> None:
    protocol = read_json(ROOT / PROTOCOL_PATH)
    preflight = read_json(ROOT / PREFLIGHT_PATH)
    assert validate_protocol(protocol)["status"] == "valid"
    assert validate_preflight(preflight, root=ROOT)["status"] == "valid"
    assert len(preflight["datasets"]) == 4
    for dataset in preflight["datasets"]:
        assert len(dataset["valid_workload"]["queries"]) == 10_000
        assert dataset["valid_truth"]["query_count"] == 10_000
        assert dataset["valid_workload"]["queries"][0].keys() == {
            "index",
            "query_id",
            "source_query_sha256",
            "sql_sha256",
        }
    serialized = str(preflight)
    assert "test workload" not in serialized
    assert "strict-unseen" not in serialized


def test_protocol_mutation_fails_closed() -> None:
    protocol = read_json(ROOT / PROTOCOL_PATH)
    mutated = copy.deepcopy(protocol)
    mutated["datasets"][0]["reference_order"] = list(
        reversed(mutated["datasets"][0]["reference_order"])
    )
    mutated["semantic_digest"] = semantic_digest(
        {key: value for key, value in mutated.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="permutation schedule drift"):
        validate_protocol(mutated)


def test_all_four_dataset_specs_are_distinct() -> None:
    assert [spec.cli_name for spec in DATASET_SPECS] == [
        "census13",
        "forest10",
        "power7",
        "dmv11",
    ]
    assert len({spec.output_root for spec in DATASET_SPECS}) == 4
