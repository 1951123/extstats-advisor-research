"""Audited AreCELearnedYet census13 adapter.

This module promotes an audited CSV and canonical workload into research
artifacts. It does not construct advisor objects or collect truth.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any

from ..provenance import read_json, semantic_digest, sha256_file, write_json

BENCHMARK_ID = "arecel-census13"
RELATION = "public.census13"
UPSTREAM_URL = "https://github.com/sfu-db/AreCELearnedYet"
UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
ARCHIVE_SHA256 = "5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79"
CSV_SHA256 = "f751fc7ecd5bd5e62acd9868a0bbe36413528b6eb2de9c2fc8fcf71d9ef2c13a"
EXPECTED_ROWS = 48_842

COLUMNS: tuple[tuple[str, str], ...] = (
    ("age", "bigint"),
    ("workclass", "text"),
    ("education", "text"),
    ("education_num", "bigint"),
    ("marital_status", "text"),
    ("occupation", "text"),
    ("relationship", "text"),
    ("race", "text"),
    ("sex", "text"),
    ("capital_gain", "bigint"),
    ("capital_loss", "bigint"),
    ("hours_per_week", "bigint"),
    ("native_country", "text"),
)


def data_root(value: Path | None = None) -> Path:
    return (
        (value or Path(os.environ.get("EXTSTATS_RESEARCH_DATA_ROOT", "/home/wqts/benchmark-data")))
        .expanduser()
        .resolve()
    )


def audit_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "audit-v1"


def source_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "raw" / "data" / "census13"


def csv_path(value: Path | None = None) -> Path:
    return source_root(value) / "original.csv"


def canonical_workload_path(value: Path | None = None) -> Path:
    return audit_root(value) / "census13.canonical.jsonl.gz"


def _audit(value: Path | None = None) -> dict[str, Any]:
    audit = read_json(audit_root(value) / "audit.json")
    if audit.get("archive_expected_sha256") != ARCHIVE_SHA256:
        raise ValueError(
            "audited AreCELearnedYet archive SHA256 does not match the frozen contract"
        )
    if audit.get("upstream", {}).get("AreCELearnedYet", {}).get("commit") != UPSTREAM_COMMIT:
        raise ValueError("audited AreCELearnedYet commit does not match the frozen contract")
    if audit["datasets"]["census13"]["csv"]["sha256"] != CSV_SHA256:
        raise ValueError("audited census13 CSV SHA256 does not match the frozen contract")
    return audit


def inspect(value: Path | None = None) -> dict[str, Any]:
    audit_path = audit_root(value) / "audit.json"
    if not audit_path.is_file():
        return {
            "benchmark_id": BENCHMARK_ID,
            "relation": RELATION,
            "source_present": False,
            "data_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/census13",
            "audit_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/audit-v1",
            "upstream_url": UPSTREAM_URL,
            "upstream_commit": UPSTREAM_COMMIT,
            "archive_sha256": ARCHIVE_SHA256,
            "expected_rows": EXPECTED_ROWS,
            "columns": [{"name": n, "postgres_type": t} for n, t in COLUMNS],
        }
    audit = _audit(value)
    csv = csv_path(value)
    workload = canonical_workload_path(value)
    present = csv.is_file() and workload.is_file()
    result: dict[str, Any] = {
        "benchmark_id": BENCHMARK_ID,
        "relation": RELATION,
        "source_present": present,
        "data_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/census13",
        "audit_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/audit-v1",
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": UPSTREAM_COMMIT,
        "archive_sha256": ARCHIVE_SHA256,
        "expected_rows": EXPECTED_ROWS,
        "columns": [{"name": n, "postgres_type": t} for n, t in COLUMNS],
        "workload_splits": audit["datasets"]["census13"]["workload"]["splits"],
    }
    if present:
        if sha256_file(csv) != CSV_SHA256:
            raise ValueError("audited census13 CSV content hash mismatch")
        result["csv_sha256"] = sha256_file(csv)
        result["canonical_workload_sha256"] = sha256_file(workload)
    result["dataset_content_identity"] = semantic_digest(
        {
            "csv_sha256": result.get("csv_sha256", CSV_SHA256),
            "archive_sha256": ARCHIVE_SHA256,
            "upstream_commit": UPSTREAM_COMMIT,
        }
    )
    return result


def extract_workload(
    output: Path, value: Path | None = None, split: str = "test"
) -> dict[str, Any]:
    if split not in {"valid", "test"}:
        raise ValueError("canonical research workload split must be valid or test")
    source = canonical_workload_path(value)
    if not source.is_file():
        raise FileNotFoundError(source)
    queries: list[dict[str, Any]] = []
    query_id_mapping: dict[str, str] = {}
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record["split"] == split:
                source_index = int(record.get("index", len(queries)))
                query_id = f"arecel_census13_{split}_{source_index:06d}"
                queries.append({"query_id": query_id, "sql": record["sql"], "weight": 1.0})
                query_id_mapping[query_id] = record["query_id"]
    result = {
        "workload_id": f"arecel_census13_{split}_v1",
        "provenance": {
            "benchmark_id": BENCHMARK_ID,
            "source_workload": "data/census13/workload/base.pkl",
            "source_split": split,
            "canonical_source_sha256": sha256_file(source),
            "upstream_commit": UPSTREAM_COMMIT,
            "query_id_mapping": query_id_mapping,
        },
        "queries": queries,
    }
    write_json(output, result)
    return {
        "workload_id": result["workload_id"],
        "query_count": len(queries),
        "sha256": sha256_file(output),
    }


def write_dataset_manifest(output: Path, value: Path | None = None) -> dict[str, Any]:
    manifest = inspect(value)
    manifest.update(
        {
            "format": "research-dataset-manifest-v1",
            "dataset_content_identity": semantic_digest(
                {
                    "csv_sha256": manifest.get("csv_sha256", CSV_SHA256),
                    "archive_sha256": ARCHIVE_SHA256,
                    "upstream_commit": UPSTREAM_COMMIT,
                }
            ),
            "source_files": [
                "data/census13/original.csv",
                "data/census13/workload/base.pkl",
                "data/census13/workload/base-original-label.pkl",
            ],
        }
    )
    write_json(output, manifest)
    return manifest
