"""Research-owned rebinding checks for the Foundations external truth contract."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


class TruthBindingError(ValueError):
    """Raised when external labels cannot be bound without changing their meaning."""


def validate_external_source_provenance(
    source: Mapping[str, Any], expected: Mapping[str, str]
) -> dict[str, str]:
    """Validate the non-PostgreSQL external-exact source contract."""

    required = {
        "kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        **expected,
    }
    mismatched = {
        key: (value, source.get(key)) for key, value in required.items() if source.get(key) != value
    }
    forbidden = {
        key: source.get(key)
        for key in ("dbms", "server_version", "server_version_num", "source_view_token")
        if source.get(key) is not None
    }
    if mismatched or forbidden:
        raise TruthBindingError(
            f"external truth provenance mismatch; mismatched={mismatched!r}, "
            f"forbidden_database_fields={forbidden!r}"
        )
    return {key: str(source[key]) for key in required}


def validate_truth_mapping(
    snapshot_queries: Sequence[Any],
    source_records: Sequence[Mapping[str, Any]],
    truth_records: Mapping[str, int],
) -> dict[str, int]:
    """Require identical query order/SQL and one non-negative label per query."""

    if len(snapshot_queries) != len(source_records):
        raise TruthBindingError("snapshot and source workload query counts differ")
    if len(truth_records) != len(snapshot_queries):
        raise TruthBindingError("truth labels do not cover the snapshot workload exactly")
    for query, source in zip(snapshot_queries, source_records):
        query_id = getattr(query, "query_id", None)
        if query_id != source.get("query_id") or query.sql != source.get("sql"):
            raise TruthBindingError(f"query identity or SQL mismatch at {query_id!r}")
        if query.weight != 1.0:
            raise TruthBindingError(f"unexpected workload weight for {query_id!r}")
        value = truth_records.get(query_id)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TruthBindingError(f"missing or invalid cardinality for {query_id!r}")
        if source.get("truth") != value:
            raise TruthBindingError(f"external label mismatch for {query_id!r}")
    expected_ids = [getattr(query, "query_id", None) for query in snapshot_queries]
    if len(set(expected_ids)) != len(expected_ids) or set(expected_ids) != set(truth_records):
        raise TruthBindingError("query IDs are not unique and complete")
    return {"query_count": len(expected_ids), "positive_weight_query_count": len(expected_ids)}


def validate_singleton_profile_binding(
    profile_path: Path,
    *,
    expected_snapshot_digest: str,
    expected_candidate_universe_digest: str,
    expected_ground_truth_digest: str,
) -> dict[str, Any]:
    """Reject historical singleton profiles from a different realization."""

    try:
        value = json.loads(Path(profile_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TruthBindingError("singleton profile is unreadable") from exc
    expected = {
        "source_snapshot_semantic_digest": expected_snapshot_digest,
        "candidate_universe_semantic_digest": expected_candidate_universe_digest,
        "ground_truth_semantic_digest": expected_ground_truth_digest,
    }
    mismatched = {
        key: (want, value.get(key)) for key, want in expected.items() if value.get(key) != want
    }
    if mismatched:
        raise TruthBindingError(f"singleton profile is stale: {mismatched!r}")
    return {key: value[key] for key in expected}


def rebind_forest10_ground_truth(
    *,
    snapshot_path: Path,
    observations_path: Path,
    output_path: Path,
    producer_revision: str,
) -> dict[str, Any]:
    """Create a new Advisor GroundTruthSet from audited external observations.

    This function does not run PostgreSQL.  The output is a new realization and must not
    overwrite a historical production-exact artifact.
    """

    from extstats_advisor.ground_truth.artifact import (
        validate_ground_truth_set,
        write_ground_truth_set,
    )
    from extstats_advisor.snapshot.bundle import load_snapshot

    from .arecel_truth import authoritative_truth_spec, observation_records
    from .datasets.forest10 import load_test_records
    from .external_truth import import_audited_authoritative_truth

    snapshot = load_snapshot(Path(snapshot_path))
    spec = authoritative_truth_spec("arecel-forest10", Path(__file__).resolve().parents[2])
    records = load_test_records()
    truth_records = observation_records(Path(observations_path))
    validate_truth_mapping(snapshot.workload.queries, records, truth_records)
    truth = import_audited_authoritative_truth(
        snapshot,
        observations_path,
        authority=spec["authority"],
        dataset_identity=spec["dataset_identity"],
        source_revision=spec["source_revision"],
    )
    destination = Path(output_path)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"ground-truth destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_ground_truth_set(truth, destination)
    summary = validate_ground_truth_set(destination, snapshot)
    return {
        "format_version": "foundations-ground-truth-binding-v1",
        "classification": "offline-artifact-binding",
        "producer_revision": producer_revision,
        "snapshot_semantic_digest": snapshot.semantic_digest,
        "ground_truth_semantic_digest": summary["semantic_digest"],
        "source_artifact_sha256": spec["observations_sha256"],
        "source_revision": spec["source_revision"],
        "dataset_identity": spec["dataset_identity"],
        "workload_id": summary["workload_id"],
        "query_count": summary["query_count"],
        "collection_contract": summary["collection_contract"],
        "ground_truth_path": str(destination),
    }
