"""Audited AreCELearnedYet DMV11 dataset adapter."""

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

BENCHMARK_ID = "arecel-dmv11"
RELATION = "public.dmv11"
UPSTREAM_URL = "https://github.com/sfu-db/AreCELearnedYet"
UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
ARCHIVE_SHA256 = "5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79"
CSV_SHA256 = "ae310972b7ac08629d135a1da7c580e3fe603bfc2e665e1969005bd481d4605a"
WORKLOAD_PICKLE_SHA256 = "e37136725ace8f60a6dfcf55529da328ab202d74988538726cff8589ccec65dc"
LABEL_PICKLE_SHA256 = "14b29cc7f28aab5dd85f3bec2e49fe841d4194b7417dcc881459a1d9d9f98d65"
CANONICAL_WORKLOAD_SHA256 = "71bb6e3c61da5ab1096cc52d41e7f696d486e4a3ba1c4586c01119df26fe9f40"
EXPECTED_ROWS = 11_591_877
EXPECTED_VALID_QUERIES = 10_000
EXPECTED_TEST_QUERIES = 10_000
SCHEMA_CONTRACT_ID = "arecel-dmv11-postgres-schema-v1"
AUTHORITATIVE_TRUTH_AUTHORITY = "sfu-db/AreCELearnedYet"
AUTHORITATIVE_TRUTH_DATASET_IDENTITY = (
    "6fc636211b53bc29993c0ffa6e6c2cd13444ce1166d7e0b2af534563f2edeef8"
)
AUTHORITATIVE_TRUTH_SOURCE_REVISION = "aa52da7768023270bad884232972e0b77ec6534a"
AUTHORITATIVE_TRUTH_SANITY_CHECK_COUNT = 15

SOURCE_COLUMNS: tuple[str, ...] = (
    "Record_Type",
    "Registration_Class",
    "State",
    "County",
    "Body_Type",
    "Fuel_Type",
    "Reg_Valid_Date",
    "Color",
    "Scofflaw_Indicator",
    "Suspension_Indicator",
    "Revocation_Indicator",
)
COLUMNS: tuple[tuple[str, str], ...] = (
    ("record_type", "VARCHAR(64)"),
    ("registration_class", "VARCHAR(64)"),
    ("state", "VARCHAR(64)"),
    ("county", "VARCHAR(64)"),
    ("body_type", "VARCHAR(64)"),
    ("fuel_type", "VARCHAR(64)"),
    ("reg_valid_date", "DOUBLE PRECISION"),
    ("color", "VARCHAR(64)"),
    ("scofflaw_indicator", "VARCHAR(64)"),
    ("suspension_indicator", "VARCHAR(64)"),
    ("revocation_indicator", "VARCHAR(64)"),
)
SOURCE_TO_CATALOG = dict(zip(SOURCE_COLUMNS, (name for name, _ in COLUMNS), strict=True))
SOURCE_RELATIONS = frozenset(
    {
        'public."dmv11_original"',
        'public."dmv11"',
        "public.dmv11_original",
        "public.dmv11",
    }
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
    return data_root(value) / "arecel" / "raw" / "data" / "dmv11"


def csv_path(value: Path | None = None) -> Path:
    return source_root(value) / "original.csv"


def workload_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base.pkl"


def label_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base-original-label.pkl"


def canonical_workload_path(value: Path | None = None) -> Path:
    return audit_root(value) / "dmv11.canonical.jsonl.gz"


def archive_path(value: Path | None = None) -> Path:
    return data_root(value) / "dmv" / "incoming" / "data.tar.gz"


def schema_contract() -> dict[str, Any]:
    return {
        "id": SCHEMA_CONTRACT_ID,
        "relation": RELATION,
        "columns": [{"name": name, "postgres_type": type_} for name, type_ in COLUMNS],
        "not_null": False,
        "identifier_contract": "unquoted-upstream-ddl-folds-to-lowercase-catalog-names",
        "collation_contract": "capture-native-postgresql-collation-for-varchar-columns",
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
    dmv = audit.get("datasets", {}).get("dmv11", {})
    if dmv.get("csv", {}).get("sha256") != CSV_SHA256:
        raise ValueError("audited DMV11 CSV SHA256 does not match the frozen contract")
    if dmv.get("csv", {}).get("rows") != EXPECTED_ROWS:
        raise ValueError("audited DMV11 row count does not match the frozen contract")
    if dmv.get("csv", {}).get("column_names") != list(SOURCE_COLUMNS):
        raise ValueError("audited DMV11 column order does not match the frozen contract")
    for name, expected in (
        ("base.pkl", WORKLOAD_PICKLE_SHA256),
        ("base-original-label.pkl", LABEL_PICKLE_SHA256),
    ):
        if dmv.get("pickles", {}).get(name, {}).get("sha256") != expected:
            raise ValueError(f"audited DMV11 {name} SHA256 does not match the frozen contract")
    return audit


def _csv_audit(path: Path) -> dict[str, Any]:
    null_counts = [0] * len(SOURCE_COLUMNS)
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError("DMV11 CSV is empty") from exc
        if header != list(SOURCE_COLUMNS):
            raise ValueError("DMV11 CSV header/order does not match the frozen source")
        rows = 0
        for row in reader:
            rows += 1
            if len(row) != len(SOURCE_COLUMNS):
                raise ValueError(f"DMV11 CSV row {rows} has {len(row)} columns")
            for index, cell in enumerate(row):
                if cell == "":
                    null_counts[index] += 1
    return {
        "rows": rows,
        "columns": len(header),
        "column_order": list(header),
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
    result: dict[str, str] = {}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        digest = sha256_file(path)
        if digest != expected[name]:
            raise ValueError(f"audited DMV11 {name} hash mismatch")
        result[name] = digest
    return result


def inspect(value: Path | None = None) -> dict[str, Any]:
    audit = _audit(value)
    hashes = _source_hashes(value)
    csv_meta = _csv_audit(csv_path(value))
    if csv_meta["rows"] != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} DMV11 rows, got {csv_meta['rows']}")
    if csv_meta["columns"] != len(COLUMNS):
        raise ValueError("DMV11 CSV column count does not match the schema contract")
    dmv_audit = audit["datasets"]["dmv11"]
    return {
        "benchmark_id": BENCHMARK_ID,
        "relation": RELATION,
        "source_relation": 'public."dmv11_original"',
        "source_present": True,
        "data_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/dmv11",
        "audit_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/audit-v1",
        "upstream_url": UPSTREAM_URL,
        "upstream_commit": UPSTREAM_COMMIT,
        "archive_sha256": ARCHIVE_SHA256,
        "source_file_sha256": hashes,
        "expected_rows": EXPECTED_ROWS,
        "observed_rows": csv_meta["rows"],
        "column_count": csv_meta["columns"],
        "column_order": csv_meta["column_order"],
        "schema_contract": schema_contract(),
        "columns": schema_contract()["columns"],
        "null_audit": {
            "source_csv_null_counts": csv_meta["null_counts"],
            "all_columns_null_free": not any(csv_meta["null_counts"].values()),
            "upstream_not_null_constraint": False,
        },
        "workload_splits": dmv_audit["workload"]["splits"],
        "dataset_content_identity": compute_dataset_content_identity(hashes["csv"]),
    }


def _adapt_relation(sql: str) -> str:
    pattern = r"(?is)^(\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+)([^\s]+)(.*)$"
    match = re.match(pattern, sql)
    if match is None or match.group(2) not in SOURCE_RELATIONS:
        raise ValueError("DMV11 SQL does not contain an audited source relation")
    return match.group(1) + RELATION + match.group(3)


_SQL_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z0-9_]*")


def _adapt_identifiers(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.startswith("'"):
            return token
        if token.startswith('"'):
            name = token[1:-1].replace('""', '"')
            catalog_name = SOURCE_TO_CATALOG.get(name)
            return f'"{catalog_name}"' if catalog_name else token
        return SOURCE_TO_CATALOG.get(token, token)

    return _SQL_TOKEN.sub(replace, sql)


def adapt_query_sql(source_sql: str) -> str:
    """Apply only projection, audited relation, and audited identifier adaptation."""
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
        raise ValueError("canonical DMV11 workload split must be valid or test")
    _audit(value)
    _source_hashes(value)
    records: list[dict[str, Any]] = []
    with gzip.open(canonical_workload_path(value), "rt", encoding="utf-8") as stream:
        for raw in map(json.loads, stream):
            if raw.get("split") != split:
                continue
            index = int(raw["index"])
            if raw.get("query_id") != f"arecel:dmv11:{split}:{index:06d}":
                raise ValueError(f"unexpected DMV11 source query identity at index {index}")
            if (
                not isinstance(raw.get("source_query_sha256"), str)
                or len(raw["source_query_sha256"]) != 64
            ):
                raise ValueError(f"missing DMV11 source query hash at index {index}")
            records.append(
                {
                    "source_index": index,
                    "query_id": f"arecel_dmv11_{split}_{index:06d}",
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
            f"expected {EXPECTED_VALID_QUERIES} DMV11 {split} queries, got {len(records)}"
        )
    if [record["source_index"] for record in records] != list(range(EXPECTED_VALID_QUERIES)):
        raise ValueError(f"DMV11 {split} source order is not contiguous and deterministic")
    return records


def load_test_records(value: Path | None = None) -> list[dict[str, Any]]:
    return load_records(value, split="test")


def load_valid_records(value: Path | None = None) -> list[dict[str, Any]]:
    return load_records(value, split="valid")


def extract_workload(
    output: Path, value: Path | None = None, split: str = "test"
) -> dict[str, Any]:
    records = load_records(value, split=split)
    metadata = inspect(value)
    hashes = metadata["source_file_sha256"]
    workload = _build_workload(records, metadata, hashes, split)
    write_json(output, workload)
    return {
        "workload_id": workload["workload_id"],
        "query_count": len(workload["queries"]),
        "sha256": sha256_file(output),
    }


def _build_workload(
    records: list[dict[str, Any]], metadata: dict[str, Any], hashes: dict[str, str], split: str
) -> dict[str, Any]:
    return {
        "workload_id": f"arecel_dmv11_{split}_v1",
        "provenance": {
            "benchmark_id": BENCHMARK_ID,
            "dataset_content_identity": metadata["dataset_content_identity"],
            "source_workload": "data/dmv11/workload/base.pkl",
            "source_labels": "data/dmv11/workload/base-original-label.pkl",
            "source_split": split,
            "source_workload_sha256": hashes["workload_pickle"],
            "source_label_sha256": hashes["label_pickle"],
            "canonical_source_sha256": hashes["canonical_workload"],
            "upstream_commit": UPSTREAM_COMMIT,
            "query_id_mapping": f"arecel:dmv11:{split}:<index> -> arecel_dmv11_{split}_<index>",
            "projection_adapter": "count-star-to-select-star-v1",
            "relation_adapter": "safe-exact-dmv11-original-to-public.dmv11-v1",
            "identifier_adapter": "safe-source-identifiers-to-lowercase-catalog-identifiers-v1",
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


def write_dataset_manifest(output: Path, value: Path | None = None) -> dict[str, Any]:
    manifest = inspect(value)
    manifest.update(
        {
            "format": "research-dataset-manifest-v1",
            "source_files": [
                "data/dmv11/original.csv",
                "data/dmv11/workload/base.pkl",
                "data/dmv11/workload/base-original-label.pkl",
            ],
        }
    )
    write_json(output, manifest)
    return manifest
