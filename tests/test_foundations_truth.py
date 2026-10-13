from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_truth import (
    TruthBindingError,
    validate_external_source_provenance,
    validate_singleton_profile_binding,
    validate_truth_mapping,
)


def _source() -> dict[str, str]:
    return {
        "kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        "authority": "sfu-db/AreCELearnedYet",
        "dataset_identity": "d" * 64,
        "source_revision": "a" * 40,
        "source_artifact_sha256": "b" * 64,
    }


def test_external_truth_provenance_rejects_database_or_wrong_source() -> None:
    assert (
        validate_external_source_provenance(
            _source(),
            {
                "authority": "sfu-db/AreCELearnedYet",
                "dataset_identity": "d" * 64,
                "source_revision": "a" * 40,
                "source_artifact_sha256": "b" * 64,
            },
        )["authority"]
        == "sfu-db/AreCELearnedYet"
    )
    bad = {**_source(), "source_revision": "c" * 40}
    with pytest.raises(TruthBindingError, match="provenance mismatch"):
        validate_external_source_provenance(bad, {"source_revision": "a" * 40})
    with pytest.raises(TruthBindingError, match="forbidden_database_fields"):
        validate_external_source_provenance(
            {**_source(), "dbms": "postgres"}, {"authority": "sfu-db/AreCELearnedYet"}
        )


def test_truth_mapping_rejects_incomplete_labels_and_query_identity_changes() -> None:
    queries = (
        SimpleNamespace(query_id="q1", sql="SELECT 1", weight=1.0),
        SimpleNamespace(query_id="q2", sql="SELECT 2", weight=1.0),
    )
    records = (
        {"query_id": "q1", "sql": "SELECT 1", "truth": 1},
        {"query_id": "q2", "sql": "SELECT 2", "truth": 2},
    )
    assert validate_truth_mapping(queries, records, {"q1": 1, "q2": 2})["query_count"] == 2
    with pytest.raises(TruthBindingError, match="cover"):
        validate_truth_mapping(queries, records, {"q1": 1})
    with pytest.raises(TruthBindingError, match="SQL mismatch"):
        validate_truth_mapping(
            queries, ({**records[0]}, {**records[1], "sql": "SELECT 99"}), {"q1": 1, "q2": 2}
        )


def test_stale_singleton_profile_is_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "singleton-profile.json"
    profile.write_text(
        json.dumps(
            {
                "source_snapshot_semantic_digest": "old" * 16,
                "candidate_universe_semantic_digest": "u" * 64,
                "ground_truth_semantic_digest": "g" * 64,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(TruthBindingError, match="stale"):
        validate_singleton_profile_binding(
            profile,
            expected_snapshot_digest="s" * 64,
            expected_candidate_universe_digest="u" * 64,
            expected_ground_truth_digest="g" * 64,
        )
