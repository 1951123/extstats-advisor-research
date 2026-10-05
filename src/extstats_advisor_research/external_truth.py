"""Research-owned bridge from audited labels to the production truth importer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _observations_format_version() -> str:
    """Resolve the observations contract from the installed frozen advisor."""

    from extstats_advisor.ground_truth import AUTHORITATIVE_CARDINALITY_OBSERVATIONS_FORMAT_VERSION

    return AUTHORITATIVE_CARDINALITY_OBSERVATIONS_FORMAT_VERSION


def _checked_records(records: Mapping[str, int]) -> list[dict[str, Any]]:
    if not isinstance(records, Mapping) or not records:
        raise ValueError("audited cardinality records must be a non-empty mapping")
    result = []
    for query_id, cardinality in sorted(records.items(), key=lambda item: item[0]):
        if not isinstance(query_id, str) or not query_id.strip():
            raise ValueError("audited query IDs must be non-empty strings")
        if not isinstance(cardinality, int) or isinstance(cardinality, bool) or cardinality < 0:
            raise ValueError("audited cardinalities must be non-negative integers")
        result.append({"query_id": query_id, "cardinality": cardinality})
    return result


def write_authoritative_observations(
    workload_id: str,
    records: Mapping[str, int],
    output: str | Path,
) -> Path:
    """Write only the production observations contract in deterministic order."""

    if not isinstance(workload_id, str) or not workload_id.strip():
        raise ValueError("workload_id must be a non-empty string")
    destination = Path(output).expanduser()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"observations destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    value = {
        "format_version": _observations_format_version(),
        "workload_id": workload_id,
        "truths": _checked_records(records),
    }
    destination.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return destination


def import_audited_authoritative_truth(
    snapshot: Any,
    observations: str | Path,
    *,
    authority: str,
    dataset_identity: str,
    source_revision: str,
) -> Any:
    """Delegate sealed-snapshot binding and GroundTruthSet construction to production."""

    from extstats_advisor.ground_truth import import_authoritative_ground_truth

    return import_authoritative_ground_truth(
        snapshot,
        observations,
        authority=authority,
        dataset_identity=dataset_identity,
        source_revision=source_revision,
    )


__all__ = [
    "import_audited_authoritative_truth",
    "write_authoritative_observations",
]
