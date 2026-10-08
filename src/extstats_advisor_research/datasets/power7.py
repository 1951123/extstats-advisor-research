"""Audited AreCELearnedYet Power7 dataset adapter."""

from __future__ import annotations

import csv
import gzip
import json
import os
import re
from pathlib import Path
from typing import Any

from ..provenance import read_json, sha256_file, write_json
from .common import compute_dataset_content_identity as _compute_dataset_content_identity

BENCHMARK_ID = "arecel-power7"
RELATION = "public.power7"
UPSTREAM_URL = "https://github.com/sfu-db/AreCELearnedYet"
UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
ARCHIVE_SHA256 = "5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79"
CSV_SHA256 = "a58a43b0e9f26e92744c2130053dd343fb6b3bb9f73b05fd39f6a44d355ddba9"
WORKLOAD_PICKLE_SHA256 = "1f270912f529e362545b6da519492a5aff63bdc65ecc10f71674e5779a7247a5"
LABEL_PICKLE_SHA256 = "d7369b34d2ddc866dbb16c3b2f5147cb10a86832dba54c905ef400bc3514cdf5"
CANONICAL_WORKLOAD_SHA256 = "9f1b11d72bcefe9d76cd83b1f995a6d6f4e0922434975550e2d5783425152327"
EXPECTED_ROWS = 2_075_259
EXPECTED_VALID_QUERIES = 10_000
EXPECTED_TEST_QUERIES = 10_000
SCHEMA_CONTRACT_ID = "arecel-power7-postgres-schema-v1"
SOURCE_COLUMNS: tuple[str, ...] = (
    "Global_active_power",
    "Global_reactive_power",
    "Voltage",
    "Global_intensity",
    "Sub_metering_1",
    "Sub_metering_2",
    "Sub_metering_3",
)
COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name.lower(), "DOUBLE PRECISION") for name in SOURCE_COLUMNS
)
SOURCE_TO_CATALOG = dict(zip(SOURCE_COLUMNS, (name for name, _ in COLUMNS), strict=True))


def data_root(value: Path | None = None) -> Path:
    return (
        (value or Path(os.environ.get("EXTSTATS_RESEARCH_DATA_ROOT", "/home/wqts/benchmark-data")))
        .expanduser()
        .resolve()
    )


def audit_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "audit-v1"


def source_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "raw" / "data" / "power7"


def csv_path(value: Path | None = None) -> Path:
    return source_root(value) / "original.csv"


def workload_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base.pkl"


def label_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base-original-label.pkl"


def canonical_workload_path(value: Path | None = None) -> Path:
    return audit_root(value) / "power7.canonical.jsonl.gz"


def archive_path(value: Path | None = None) -> Path:
    return data_root(value) / "dmv" / "incoming" / "data.tar.gz"


def schema_contract() -> dict[str, Any]:
    return {
        "id": SCHEMA_CONTRACT_ID,
        "relation": RELATION,
        "columns": [{"name": name, "postgres_type": type_} for name, type_ in COLUMNS],
        "not_null": False,
        "identifier_contract": "unquoted-upstream-ddl-folds-to-lowercase-catalog-names",
    }


def compute_dataset_content_identity(
    csv_sha256: str,
    *,
    schema_contract_id: str = SCHEMA_CONTRACT_ID,
    columns: tuple[tuple[str, str], ...] = COLUMNS,
    not_null: bool = False,
) -> str:
    return _compute_dataset_content_identity(
        benchmark_id=BENCHMARK_ID,
        relation=RELATION,
        csv_sha256=csv_sha256,
        archive_sha256=ARCHIVE_SHA256,
        upstream_commit=UPSTREAM_COMMIT,
        schema_contract_id=schema_contract_id,
        columns=columns,
        not_null=not_null,
    )


def _audit(value: Path | None = None) -> dict[str, Any]:
    audit = read_json(audit_root(value) / "audit.json")
    if audit.get("archive_expected_sha256") != ARCHIVE_SHA256:
        raise ValueError("audited AreCEL archive SHA256 does not match the frozen contract")
    archive = archive_path(value)
    if not archive.is_file() or sha256_file(archive) != ARCHIVE_SHA256:
        raise FileNotFoundError(f"frozen shared AreCEL archive is missing or mismatched: {archive}")
    if audit.get("upstream", {}).get("AreCELearnedYet", {}).get("commit") != UPSTREAM_COMMIT:
        raise ValueError("audited AreCEL commit does not match the frozen contract")
    forest = audit.get("datasets", {}).get(BENCHMARK_ID.removeprefix("arecel-"), {})
    if forest.get("csv", {}).get("sha256") != CSV_SHA256:
        raise ValueError("audited Power7 CSV SHA256 does not match the frozen contract")
    if forest.get("csv", {}).get("rows") != EXPECTED_ROWS:
        raise ValueError("audited Power7 row count does not match the frozen contract")
    for name, expected in (
        ("base.pkl", WORKLOAD_PICKLE_SHA256),
        ("base-original-label.pkl", LABEL_PICKLE_SHA256),
    ):
        if forest.get("pickles", {}).get(name, {}).get("sha256") != expected:
            raise ValueError(f"audited Power7 {name} SHA256 does not match the frozen contract")
    return audit


def _csv_audit(path: Path) -> dict[str, Any]:
    null_counts = [0] * len(SOURCE_COLUMNS)
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if header != list(SOURCE_COLUMNS):
            raise ValueError("Power7 CSV header/order does not match the frozen source")
        rows = 0
        for row in reader:
            rows += 1
            if len(row) != len(SOURCE_COLUMNS):
                raise ValueError(f"Power7 CSV row {rows} has {len(row)} columns")
            for index, value in enumerate(row):
                if value == "":
                    null_counts[index] += 1
    return {
        "rows": rows,
        "columns": len(SOURCE_COLUMNS),
        "null_counts": dict(zip(SOURCE_COLUMNS, null_counts, strict=True)),
    }


def _source_hashes(value: Path | None = None) -> dict[str, str]:
    paths = {
        "csv": csv_path(value),
        "workload_pickle": workload_pickle_path(value),
        "label_pickle": label_pickle_path(value),
        "canonical_workload": canonical_workload_path(value),
    }
    expected = {
        "csv": CSV_SHA256,
        "workload_pickle": WORKLOAD_PICKLE_SHA256,
        "label_pickle": LABEL_PICKLE_SHA256,
        "canonical_workload": CANONICAL_WORKLOAD_SHA256,
    }
    result = {}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != expected[name]:
            raise ValueError(f"audited Power7 {name} hash mismatch")
        result[name] = digest
    return result


def inspect(value: Path | None = None) -> dict[str, Any]:
    audit = _audit(value)
    hashes = _source_hashes(value)
    csv_meta = _csv_audit(csv_path(value))
    if csv_meta["rows"] != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} Power7 rows, got {csv_meta['rows']}")
    power_audit = audit["datasets"]["power7"]
    return {
        "benchmark_id": BENCHMARK_ID,
        "relation": RELATION,
        "source_present": True,
        "data_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/power7",
        "audit_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/audit-v1",
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": UPSTREAM_COMMIT,
        "archive_sha256": ARCHIVE_SHA256,
        "source_file_sha256": hashes,
        "expected_rows": EXPECTED_ROWS,
        "observed_rows": csv_meta["rows"],
        "schema_contract": schema_contract(),
        "columns": schema_contract()["columns"],
        "null_audit": {
            "source_csv_null_counts": csv_meta["null_counts"],
            "all_columns_null_free": not any(csv_meta["null_counts"].values()),
            "upstream_not_null_constraint": False,
        },
        "workload_splits": power_audit["workload"]["splits"],
        "dataset_content_identity": compute_dataset_content_identity(hashes["csv"]),
    }


def _adapt_relation(sql: str) -> str:
    pattern = r"(?is)^(\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+)([^\s]+)(.*)$"
    match = re.match(pattern, sql)
    if match is None or match.group(2) not in {'public."power7"', "public.power7"}:
        raise ValueError("Power7 SQL does not contain the audited source relation")
    return match.group(1) + RELATION + match.group(3)


def _adapt_identifiers(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return '"' + SOURCE_TO_CATALOG.get(match.group(1), match.group(1)) + '"'

    return re.sub(r'"([A-Za-z][A-Za-z0-9_]*)"', replace, sql)


def adapt_query_sql(source_sql: str) -> str:
    relation_sql = _adapt_relation(source_sql)
    projected = re.sub(
        r"(?is)^\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+",
        "SELECT * FROM ",
        relation_sql,
        count=1,
    )
    return _adapt_identifiers(projected)


def load_records(value: Path | None = None, split: str = "test") -> list[dict[str, Any]]:
    if split not in {"valid", "test"}:
        raise ValueError("canonical Power7 workload split must be valid or test")
    _audit(value)
    _source_hashes(value)
    records = []
    with gzip.open(canonical_workload_path(value), "rt", encoding="utf-8") as stream:
        for raw in map(json.loads, stream):
            if raw.get("split") != split:
                continue
            index = int(raw["index"])
            expected_id = f"arecel:power7:{split}:{index:06d}"
            if raw.get("query_id") != expected_id:
                raise ValueError(f"unexpected Power7 source query identity at index {index}")
            if (
                not isinstance(raw.get("source_query_sha256"), str)
                or len(raw["source_query_sha256"]) != 64
            ):
                raise ValueError(f"missing Power7 source query hash at index {index}")
            records.append(
                {
                    "source_index": index,
                    "query_id": f"arecel_power7_{split}_{index:06d}",
                    "source_query_id": raw["query_id"],
                    "original_sql": raw["sql"],
                    "sql": adapt_query_sql(raw["sql"]),
                    "source_query": raw["source_query"],
                    "truth": int(raw["source_label"]["cardinality"]),
                    "source_label": raw["source_label"],
                    "source_query_sha256": raw["source_query_sha256"],
                }
            )
    if len(records) != EXPECTED_VALID_QUERIES:
        raise ValueError(
            f"expected {EXPECTED_VALID_QUERIES} Power7 {split} queries, got {len(records)}"
        )
    if [record["source_index"] for record in records] != list(range(EXPECTED_VALID_QUERIES)):
        raise ValueError(f"Power7 {split} source order is not contiguous and deterministic")
    return records


def load_test_records(value: Path | None = None) -> list[dict[str, Any]]:
    return load_records(value, split="test")


def load_valid_records(value: Path | None = None) -> list[dict[str, Any]]:
    return load_records(value, split="valid")


def extract_workload(
    output: Path, value: Path | None = None, split: str = "test"
) -> dict[str, Any]:
    records = load_records(value, split=split)
    hashes = _source_hashes(value)
    workload = {
        "workload_id": f"arecel_power7_{split}_v1",
        "provenance": {
            "benchmark_id": BENCHMARK_ID,
            "source_workload": "data/power7/workload/base.pkl",
            "source_labels": "data/power7/workload/base-original-label.pkl",
            "source_split": split,
            "source_workload_sha256": hashes["workload_pickle"],
            "source_label_sha256": hashes["label_pickle"],
            "canonical_source_sha256": hashes["canonical_workload"],
            "upstream_commit": UPSTREAM_COMMIT,
            "query_id_mapping": f"arecel:power7:{split}:<index> -> arecel_power7_{split}_<index>",
            "projection_adapter": "count-star-to-select-star-v1",
            "relation_adapter": "safe-exact-source-relation-to-public.power7-v1",
            "identifier_adapter": "quoted-source-names-to-lowercase-catalog-identifiers-v1",
        },
        "queries": [
            {
                "query_id": record["query_id"],
                "sql": record["sql"],
                "weight": 1.0,
            }
            for record in records
        ],
    }
    write_json(output, workload)
    return {
        "workload_id": workload["workload_id"],
        "query_count": len(records),
        "sha256": sha256_file(output),
    }


def write_dataset_manifest(output: Path, value: Path | None = None) -> dict[str, Any]:
    manifest = inspect(value)
    manifest.update(
        {
            "format": "research-dataset-manifest-v1",
            "source_files": [
                "data/power7/original.csv",
                "data/power7/workload/base.pkl",
                "data/power7/workload/base-original-label.pkl",
            ],
        }
    )
    write_json(output, manifest)
    return manifest
