"""Offline artifact-binding checks for the Foundations experiments.

This module deliberately keeps database access out of the binding layer.  Advisor
validators read sealed artifacts; a live planner session is owned by the oracle adapter.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ArtifactBindingError(ValueError):
    """Raised when an artifact cannot be bound to the declared experiment unit."""


def validate_digest(actual: str, expected: str, *, label: str) -> str:
    """Require an exact lowercase SHA-256 identity without touching external systems."""

    if not isinstance(actual, str) or len(actual) != 64:
        raise ArtifactBindingError(f"{label} is not a SHA-256 digest")
    if actual != expected:
        raise ArtifactBindingError(f"{label} digest mismatch: expected={expected}, actual={actual}")
    return actual


def sha256_file(path: Path) -> str:
    """Hash a file in bounded chunks for source-binding checks."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def incidence_digest(query_ids: list[str] | tuple[str, ...]) -> str:
    """Digest the canonical sorted query-ID list used by the EXP-002 manifest."""

    if len(query_ids) != len(set(query_ids)):
        raise ArtifactBindingError("candidate incidence contains duplicate query IDs")
    payload = json.dumps(sorted(query_ids), ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


def validate_source_bindings(actual: dict[str, str], expected: dict[str, str]) -> dict[str, str]:
    """Require exact source identities before a live oracle can be opened."""

    missing = sorted(set(expected) - set(actual))
    mismatched = {
        key: (expected[key], actual.get(key))
        for key in expected
        if key in actual and actual[key] != expected[key]
    }
    if missing or mismatched:
        raise ArtifactBindingError(
            f"source binding mismatch; missing={missing!r}, mismatched={mismatched!r}"
        )
    return {key: actual[key] for key in expected}


@dataclass(frozen=True)
class ArtifactBindingSummary:
    """Portable summary of offline validation without serializing artifact contents."""

    snapshot: dict[str, Any]
    candidate_universe: dict[str, Any]
    native_repository: dict[str, Any]
    ground_truth: dict[str, Any]

    @property
    def ready_for_objective_evaluation(self) -> bool:
        return all(
            item.get("status") == "validated"
            for item in (
                self.snapshot,
                self.candidate_universe,
                self.native_repository,
                self.ground_truth,
            )
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "snapshot": dict(self.snapshot),
            "candidate_universe": dict(self.candidate_universe),
            "native_repository": dict(self.native_repository),
            "ground_truth": dict(self.ground_truth),
            "ready_for_objective_evaluation": self.ready_for_objective_evaluation,
        }


def validate_forest10_artifacts(
    *,
    snapshot_path: Path,
    candidate_universe_path: Path,
    native_repository_path: Path,
    ground_truth_path: Path,
    expected_snapshot_digest: str,
    expected_candidate_universe_digest: str,
    expected_native_repository_digest: str,
    expected_ground_truth_digest: str,
) -> ArtifactBindingSummary:
    """Run Advisor's sealed-artifact validators and preserve each failure explicitly.

    The function returns a report instead of hiding a failed truth binding.  Callers that
    require an executable oracle must check ``ready_for_objective_evaluation``.
    """

    from extstats_advisor.candidates.universe import (
        load_candidate_universe,
        validate_candidate_universe,
    )
    from extstats_advisor.ground_truth.artifact import (
        load_ground_truth_set,
        validate_ground_truth_set,
    )
    from extstats_advisor.native_stats.repository import (
        load_native_stats_repository,
        validate_native_stats_repository,
        validate_native_stats_repository_compatibility,
    )
    from extstats_advisor.snapshot.bundle import load_snapshot, validate_snapshot

    snapshot = load_snapshot(Path(snapshot_path))
    snapshot_summary = validate_snapshot(Path(snapshot_path))
    validate_digest(snapshot.semantic_digest or "", expected_snapshot_digest, label="snapshot")

    universe = load_candidate_universe(Path(candidate_universe_path), snapshot)
    universe_summary = validate_candidate_universe(Path(candidate_universe_path), snapshot)
    validate_digest(
        universe.semantic_digest, expected_candidate_universe_digest, label="candidate universe"
    )

    repository = load_native_stats_repository(Path(native_repository_path))
    repository_summary = validate_native_stats_repository(Path(native_repository_path))
    validate_digest(
        repository.semantic_digest, expected_native_repository_digest, label="native repository"
    )
    compatibility = validate_native_stats_repository_compatibility(repository, snapshot, universe)
    repository_summary = {**repository_summary, "compatibility": compatibility}

    truth_result: dict[str, Any]
    try:
        truth_summary = validate_ground_truth_set(Path(ground_truth_path), snapshot)
        validate_digest(
            truth_summary["semantic_digest"], expected_ground_truth_digest, label="ground truth"
        )
        load_ground_truth_set(Path(ground_truth_path), snapshot)
    except Exception as exc:  # noqa: BLE001 - preserve every validator failure in the report
        truth_result = {
            "status": "blocked",
            "expected_digest": expected_ground_truth_digest,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    else:
        truth_result = {"status": "validated", **truth_summary}

    return ArtifactBindingSummary(
        snapshot={"status": "validated", **snapshot_summary},
        candidate_universe={"status": "validated", **universe_summary},
        native_repository={"status": "validated", **repository_summary},
        ground_truth=truth_result,
    )
