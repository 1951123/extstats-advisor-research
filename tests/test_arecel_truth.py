from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.arecel_truth import (
    _SANITY_CHECK_COUNTS,
    OBSERVATIONS_FORMAT,
    _source_hashes,
    authoritative_truth_spec,
    validate_observation_wire,
    validate_truth_policy,
)
from extstats_advisor_research.datasets import census13
from extstats_advisor_research.provenance import read_json, sha256_file


def _wire() -> dict[str, object]:
    return {
        "format_version": OBSERVATIONS_FORMAT,
        "workload_id": "synthetic-workload",
        "truths": [
            {"query_id": f"q_{index:05d}", "cardinality": index % 7} for index in range(10_000)
        ],
    }


def test_observation_wire_is_exact_and_rejects_research_metadata() -> None:
    value = _wire()
    assert validate_observation_wire(value)["query_count"] == 10_000

    with pytest.raises(ValueError, match="unexpected fields"):
        validate_observation_wire({**value, "research_sha": "unwanted"})

    duplicate = copy.deepcopy(value)
    duplicate["truths"][1]["query_id"] = duplicate["truths"][0]["query_id"]
    with pytest.raises(ValueError, match="unique"):
        validate_observation_wire(duplicate)


def test_label_source_hash_mismatch_is_rejected(tmp_path: Path, monkeypatch) -> None:
    files = {}
    for name in ("workload_pickle", "label_pickle", "canonical_workload"):
        path = tmp_path / name
        path.write_bytes(name.encode())
        files[name] = path
    monkeypatch.setattr(
        census13,
        "inspect",
        lambda _data_root=None: {
            "source_file_sha256": {
                name: ("0" * 64 if name == "label_pickle" else sha256_file(path))
                for name, path in files.items()
            }
        },
    )
    for name, path in files.items():
        monkeypatch.setattr(census13, f"{name}_path", lambda _root=None, p=path: p)
    with pytest.raises(ValueError, match="label_pickle SHA256"):
        _source_hashes(census13, tmp_path)


def test_audited_policy_covers_all_datasets_and_external_spec_is_snapshot_input() -> None:
    root = Path(__file__).resolve().parents[1]
    policy = read_json(root / "paper/benchmark-truth-policy-v1.json")
    assert validate_truth_policy(policy, research_root=root)["dataset_count"] == 4
    for dataset_id in (
        "arecel-census13",
        "arecel-forest10",
        "arecel-power7",
        "arecel-dmv11",
    ):
        spec = authoritative_truth_spec(dataset_id, root)
        assert spec["kind"] == "authoritative-external-exact"
        assert spec["query_count"] == 10_000
        assert spec["observations_path"].is_file()


def test_registered_sanity_counts_match_deterministic_selection() -> None:
    expected = {
        "arecel-forest10": 14,
        "arecel-power7": 10,
        "arecel-dmv11": 15,
    }
    assert _SANITY_CHECK_COUNTS == expected
    for dataset_id, count in expected.items():
        assert authoritative_truth_spec(dataset_id)["sanity_check_count"] == count


def test_provenance_validation_cannot_claim_full_equivalence_without_passing_audit() -> None:
    root = Path(__file__).resolve().parents[1]
    policy = read_json(root / "paper/benchmark-truth-policy-v1.json")
    broken = copy.deepcopy(policy)
    forest = next(item for item in broken["datasets"] if item["dataset_id"] == "arecel-forest10")
    forest["status"] = "validated-full-equivalence"
    forest["equivalence_evidence"] = {
        "status": "provenance-only",
        "artifact": "experiments/arecel-census13/arecel-truth-equivalence-v1.json",
    }
    with pytest.raises(ValueError, match="full-equivalence policy"):
        validate_truth_policy(broken, research_root=root)


def test_observation_artifacts_have_no_embedded_audit_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    path = root / "truth/arecel/census13/authoritative-cardinality-observations-v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert set(value) == {"format_version", "workload_id", "truths"}
    assert set(value["truths"][0]) == {"query_id", "cardinality"}
