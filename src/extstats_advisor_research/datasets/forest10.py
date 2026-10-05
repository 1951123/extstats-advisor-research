"""Audited AreCELearnedYet Forest10 dataset adapter."""

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

BENCHMARK_ID = "arecel-forest10"
RELATION = "public.forest10"
UPSTREAM_URL = "https://github.com/sfu-db/AreCELearnedYet"
UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
ARCHIVE_SHA256 = "5cd33cba7f3d7182ef497e60e7346fb2a7546941590a90a4444913a944958f79"
CSV_SHA256 = "3484f3037f890f6bae46ae51fb387ee883351119667139c5894654276789a73c"
WORKLOAD_PICKLE_SHA256 = "501a3dcfe82ec5742fce4c3dc3b298eabcf78b82dab3a8ff8ad2914a61f4eaea"
LABEL_PICKLE_SHA256 = "34d830e6a30b48228ae41e6ae5643fb199e9794b3f36c915fb742af58fdfb974"
CANONICAL_WORKLOAD_SHA256 = "a922d87660b2dec431a77c2e4a054c4f5528b0f25040859d76d0148a9ed73e74"
EXPECTED_ROWS = 581_012
EXPECTED_TEST_QUERIES = 10_000
SCHEMA_CONTRACT_ID = "arecel-forest10-postgres-schema-v1"
UPSTREAM_DDL_RELATION = '"forest10_original"'
AUDITED_CANONICAL_RELATIONS = ('public."forest10"', 'public."forest10_original"')

SOURCE_COLUMNS: tuple[str, ...] = (
    "Elevation",
    "Aspect",
    "Slope",
    "Horizontal_Distance_To_Hydrology",
    "Vertical_Distance_To_Hydrology",
    "Horizontal_Distance_To_Roadways",
    "Hillshade_9am",
    "Hillshade_Noon",
    "Hillshade_3pm",
    "Horizontal_Distance_To_Fire_Points",
)
COLUMNS: tuple[tuple[str, str], ...] = tuple(
    (name.lower(), "DOUBLE PRECISION") for name in SOURCE_COLUMNS
)
SOURCE_TO_CATALOG = dict(zip(SOURCE_COLUMNS, (name for name, _ in COLUMNS)))


def data_root(value: Path | None = None) -> Path:
    return (
        (value or Path(os.environ.get("EXTSTATS_RESEARCH_DATA_ROOT", "/home/wqts/benchmark-data")))
        .expanduser()
        .resolve()
    )


def audit_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "audit-v1"


def source_root(value: Path | None = None) -> Path:
    return data_root(value) / "arecel" / "raw" / "data" / "forest10"


def csv_path(value: Path | None = None) -> Path:
    return source_root(value) / "original.csv"


def workload_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base.pkl"


def label_pickle_path(value: Path | None = None) -> Path:
    return source_root(value) / "workload" / "base-original-label.pkl"


def canonical_workload_path(value: Path | None = None) -> Path:
    return audit_root(value) / "forest10.canonical.jsonl.gz"


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
    expected_members = {
        "data/forest10/original.csv",
        "data/forest10/workload/base.pkl",
        "data/forest10/workload/base-original-label.pkl",
    }
    if not expected_members.issubset(set(audit.get("archive_members", []))):
        raise ValueError("audited AreCEL archive is missing a Forest10 source member")
    if audit.get("upstream", {}).get("AreCELearnedYet", {}).get("commit") != UPSTREAM_COMMIT:
        raise ValueError("audited AreCEL commit does not match the frozen contract")
    forest = audit.get("datasets", {}).get("forest10", {})
    if forest.get("csv", {}).get("sha256") != CSV_SHA256:
        raise ValueError("audited Forest10 CSV SHA256 does not match the frozen contract")
    if forest.get("csv", {}).get("rows") != EXPECTED_ROWS:
        raise ValueError("audited Forest10 row count does not match the frozen contract")
    if forest.get("pickles", {}).get("base.pkl", {}).get("sha256") != WORKLOAD_PICKLE_SHA256:
        raise ValueError(
            "audited Forest10 workload pickle SHA256 does not match the frozen contract"
        )
    if (
        forest.get("pickles", {}).get("base-original-label.pkl", {}).get("sha256")
        != LABEL_PICKLE_SHA256
    ):
        raise ValueError("audited Forest10 label pickle SHA256 does not match the frozen contract")
    return audit


def _csv_audit(path: Path) -> dict[str, Any]:
    null_counts = [0] * len(SOURCE_COLUMNS)
    with path.open("r", encoding="utf-8", newline="") as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if header != list(SOURCE_COLUMNS):
            raise ValueError("Forest10 CSV header/order does not match the frozen source")
        rows = 0
        for row in reader:
            rows += 1
            if len(row) != len(SOURCE_COLUMNS):
                raise ValueError(f"Forest10 CSV row {rows} has {len(row)} columns")
            for index, value in enumerate(row):
                if value == "":
                    null_counts[index] += 1
    return {
        "rows": rows,
        "columns": len(header),
        "null_counts": dict(zip(SOURCE_COLUMNS, null_counts)),
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
            raise ValueError(f"audited Forest10 {name} hash mismatch")
        result[name] = digest
    return result


def inspect(value: Path | None = None) -> dict[str, Any]:
    audit = _audit(value)
    hashes = _source_hashes(value)
    csv_meta = _csv_audit(csv_path(value))
    if csv_meta["rows"] != EXPECTED_ROWS:
        raise ValueError(f"expected {EXPECTED_ROWS} Forest10 rows, got {csv_meta['rows']}")
    forest_audit = audit["datasets"]["forest10"]
    return {
        "benchmark_id": BENCHMARK_ID,
        "relation": RELATION,
        "source_relation": UPSTREAM_DDL_RELATION,
        "source_present": True,
        "data_root_logical": "EXTSTATS_RESEARCH_DATA_ROOT/arecel/raw/data/forest10",
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
        "workload_splits": forest_audit["workload"]["splits"],
        "dataset_content_identity": compute_dataset_content_identity(hashes["csv"]),
    }


def _adapt_relation(sql: str) -> str:
    relation_pattern = r"(?is)^(\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+)([^\s]+)(.*)$"
    match = re.match(relation_pattern, sql)
    if match is None or match.group(2) not in AUDITED_CANONICAL_RELATIONS:
        raise ValueError("Forest10 SQL does not contain the audited source relation")
    return match.group(1) + RELATION + match.group(3)


def _adapt_identifiers(sql: str) -> str:
    def replace(match: re.Match[str]) -> str:
        return '"' + SOURCE_TO_CATALOG.get(match.group(1), match.group(1)) + '"'

    return re.sub(r'"([A-Za-z][A-Za-z0-9_]*)"', replace, sql)


def adapt_query_sql(source_sql: str) -> str:
    """Adapt only projection, relation identity, and required catalog identifier case."""
    relation_sql = _adapt_relation(source_sql)
    projected = re.sub(
        r"(?is)^\s*SELECT\s+COUNT\s*\(\s*\*\s*\)\s+FROM\s+",
        "SELECT * FROM ",
        relation_sql,
        count=1,
    )
    return _adapt_identifiers(projected)


def load_test_records(value: Path | None = None) -> list[dict[str, Any]]:
    _audit(value)
    _source_hashes(value)
    records = []
    with gzip.open(canonical_workload_path(value), "rt", encoding="utf-8") as stream:
        for record in map(json.loads, stream):
            if record.get("split") != "test":
                continue
            index = int(record["index"])
            if record.get("query_id") != f"arecel:forest10:test:{index:06d}":
                raise ValueError(f"unexpected Forest10 source query identity at index {index}")
            records.append(
                {
                    "source_index": index,
                    "source_query_id": record["query_id"],
                    "original_sql": record["sql"],
                    "sql": adapt_query_sql(record["sql"]),
                    "source_query": record["source_query"],
                    "truth": int(record["source_label"]["cardinality"]),
                    "source_label": record["source_label"],
                    "source_query_sha256": record["source_query_sha256"],
                }
            )
    if len(records) != EXPECTED_TEST_QUERIES:
        raise ValueError(
            f"expected {EXPECTED_TEST_QUERIES} Forest10 test queries, got {len(records)}"
        )
    if [record["source_index"] for record in records] != list(range(EXPECTED_TEST_QUERIES)):
        raise ValueError("Forest10 test source order is not contiguous and deterministic")
    return records


def extract_workload(
    output: Path, value: Path | None = None, split: str = "test"
) -> dict[str, Any]:
    if split != "test":
        raise ValueError("Forest10 adapter currently audits only the base:test reproduction split")
    records = load_test_records(value)
    hashes = _source_hashes(value)
    workload = {
        "workload_id": "arecel_forest10_test_v1",
        "provenance": {
            "benchmark_id": BENCHMARK_ID,
            "source_workload": "data/forest10/workload/base.pkl",
            "source_labels": "data/forest10/workload/base-original-label.pkl",
            "source_split": "test",
            "source_workload_sha256": hashes["workload_pickle"],
            "source_label_sha256": hashes["label_pickle"],
            "canonical_source_sha256": hashes["canonical_workload"],
            "upstream_commit": UPSTREAM_COMMIT,
            "query_id_mapping": "arecel:forest10:test:<index> -> arecel_forest10_test_<index>",
            "projection_adapter": "count-star-to-select-star-v1",
            "relation_adapter": "safe-exact-source-relation-to-public.forest10-v1",
            "identifier_adapter": "quoted-source-names-to-lowercase-catalog-identifiers-v1",
        },
        "queries": [
            {
                "query_id": f"arecel_forest10_test_{record['source_index']:06d}",
                "sql": record["sql"],
                "weight": 1.0,
            }
            for record in records
        ],
    }
    write_json(output, workload)
    return {
        "workload_id": workload["workload_id"],
        "query_count": len(workload["queries"]),
        "sha256": sha256_file(output),
    }


def write_dataset_manifest(output: Path, value: Path | None = None) -> dict[str, Any]:
    manifest = inspect(value)
    manifest.update(
        {
            "format": "research-dataset-manifest-v1",
            "source_files": [
                "data/forest10/original.csv",
                "data/forest10/workload/base.pkl",
                "data/forest10/workload/base-original-label.pkl",
            ],
        }
    )
    write_json(output, manifest)
    return manifest
