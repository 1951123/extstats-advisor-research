"""Shared schema-aware dataset identity helpers."""

from __future__ import annotations

from typing import Any

from ..provenance import semantic_digest


def compute_dataset_content_identity(
    *,
    benchmark_id: str,
    relation: str,
    csv_sha256: str,
    archive_sha256: str,
    upstream_commit: str,
    schema_contract_id: str,
    columns: tuple[tuple[str, str], ...],
    not_null: bool | None = None,
) -> str:
    """Hash source identity and ordered physical schema without credentials."""
    value: dict[str, Any] = {
        "benchmark_id": benchmark_id,
        "relation": relation,
        "csv_sha256": csv_sha256,
        "archive_sha256": archive_sha256,
        "upstream_commit": upstream_commit,
        "schema_contract_id": schema_contract_id,
        "columns": [{"name": name, "postgres_type": type_} for name, type_ in columns],
    }
    if not_null is not None:
        value["not_null"] = not_null
    return semantic_digest(value)
