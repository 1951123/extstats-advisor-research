"""Offline RQ1b workload-split audit and preregistration helpers.

This module deliberately stops at source/provenance preparation.  It does not
invoke PostgreSQL, the Advisor, a planner, or any workload generator.
"""

from __future__ import annotations

import gzip
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from .arecel_truth import (
    TEST_SPLIT,
    VALID_SPLIT,
    validate_observation_wire,
)
from .datasets import DATASETS
from .provenance import read_json, semantic_digest, sha256_file, write_json

DATASET_ORDER = (
    "arecel-census13",
    "arecel-forest10",
    "arecel-power7",
    "arecel-dmv11",
)
SPLIT_ORDER = ("train", "valid", "test")
EXPECTED_SPLIT_COUNTS = {"train": 100_000, "valid": 10_000, "test": 10_000}
UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
ADVISOR_SHA = "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3"
PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
POSTGRES_VERSION = "16.14"
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
SCREENING_WIDTH = 8
SEARCH_BUDGET_SECONDS = 300
SOURCE_AUDIT_FORMAT = "rq1-workload-generalization-source-audit-v1"
SOURCE_AUDIT_V2_FORMAT = "rq1-workload-generalization-source-audit-v2"
PROTOCOL_FORMAT = "rq1-workload-generalization-protocol-v1"
PROTOCOL_V2_FORMAT = "rq1-workload-generalization-protocol-v2"
TRUTH_POLICY_FORMAT = "rq1-workload-generalization-truth-policy-v1"
STRICT_UNSEEN_FORMAT = "rq1-workload-generalization-strict-unseen-v1"
PROTOCOL_PATH = Path("paper/rq1-workload-generalization-protocol-v1.json")
PROTOCOL_V2_PATH = Path("paper/rq1-workload-generalization-protocol-v2.json")
TRUTH_POLICY_PATH = Path("paper/rq1-workload-generalization-truth-policy-v1.json")
SOURCE_AUDIT_PATH = Path("experiments/rq1-workload-generalization-source-audit-v1.json")
SOURCE_AUDIT_V2_PATH = Path("experiments/rq1-workload-generalization-source-audit-v2.json")
STRICT_UNSEEN_PATH = Path("experiments/rq1-workload-generalization-strict-unseen-v1.json")
SOURCE_AUDIT_V1_DIGEST = "36345c562b97d07b05e3d96c4218634b5e15b9a977f2b55deb2fc0c4b6ebcda6"
PROTOCOL_V1_DIGEST = "7c8d959a4388fc484013e3736140e9b49aa1060bad22736065a60522c0b3e83d"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _without_digest(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _slug(dataset_id: str) -> str:
    _require(dataset_id in DATASETS, f"unsupported AreCEL dataset: {dataset_id}")
    return dataset_id.removeprefix("arecel-")


def _split_workload_id(dataset_id: str, split: str) -> str:
    _require(split in {VALID_SPLIT, TEST_SPLIT}, "RQ1b workload split must be valid or test")
    return f"arecel_{_slug(dataset_id)}_{split}_v1"


def _source_record_summary(dataset: Any, data_root: Path | None) -> dict[str, list[dict[str, Any]]]:
    """Read audited canonical JSONL records without regenerating workloads."""

    path = dataset.canonical_workload_path(data_root)
    _require(path.is_file(), f"canonical workload is missing: {path}")
    result = {split: [] for split in SPLIT_ORDER}
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for raw in map(json.loads, stream):
            split = raw.get("split")
            if split not in result:
                raise ValueError(f"{dataset.BENCHMARK_ID} contains unsupported workload split")
            index = raw.get("index")
            query_id = raw.get("query_id")
            source_hash = raw.get("source_query_sha256")
            _require(
                isinstance(index, int) and not isinstance(index, bool),
                "source index is invalid",
            )
            expected_source_id = f"{dataset.BENCHMARK_ID.removeprefix('arecel-')}"
            expected_query_id = f"arecel:{expected_source_id}:{split}:{index:06d}"
            _require(query_id == expected_query_id, f"{dataset.BENCHMARK_ID} source query ID drift")
            _require(
                isinstance(source_hash, str) and len(source_hash) == 64,
                f"{dataset.BENCHMARK_ID} source query hash is missing or malformed",
            )
            _require(isinstance(raw.get("source_label"), dict), "source label is missing")
            _require("cardinality" in raw["source_label"], "source label cardinality is missing")
            sql = raw.get("sql")
            _require(isinstance(sql, str) and sql.strip(), "canonical source SQL is missing")
            result[split].append(
                {
                    "index": index,
                    "query_id": query_id,
                    "source_query_sha256": source_hash,
                    "normalized_sql": re.sub(r"\s+", " ", sql.strip()),
                    "has_label": True,
                }
            )
    for split, records in result.items():
        expected = EXPECTED_SPLIT_COUNTS[split]
        _require(
            len(records) == expected,
            f"{dataset.BENCHMARK_ID} {split} count {len(records)} != {expected}",
        )
        _require(
            [record["index"] for record in records] == list(range(expected)),
            f"{dataset.BENCHMARK_ID} {split} source indices are not contiguous",
        )
    return result


def _workload_identity(
    dataset: Any, data_root: Path | None, split: str, directory: Path
) -> dict[str, Any]:
    output = directory / f"{_slug(dataset.BENCHMARK_ID)}-{split}.json"
    identity = dataset.extract_workload(output, data_root, split=split)
    _require(
        identity["workload_id"] == _split_workload_id(dataset.BENCHMARK_ID, split),
        "workload ID drift",
    )
    _require(identity["query_count"] == SAMPLE_ROWS, "workload query count drift")
    _require(
        isinstance(identity.get("sha256"), str) and len(identity["sha256"]) == 64,
        "workload hash missing",
    )
    return {
        "workload_id": identity["workload_id"],
        "workload_sha256": identity["sha256"],
        "query_count": identity["query_count"],
    }


def _source_hashes(dataset: Any, data_root: Path | None) -> dict[str, str]:
    metadata = dataset.inspect(data_root)
    hashes = metadata.get("source_file_sha256")
    _require(isinstance(hashes, dict), f"{dataset.BENCHMARK_ID} source hashes are unavailable")
    required = {"workload_pickle", "label_pickle", "canonical_workload"}
    _require(
        required.issubset(hashes),
        f"{dataset.BENCHMARK_ID} source hash contract is incomplete",
    )
    result = {name: str(hashes[name]) for name in required}
    for name, path_getter in (
        ("workload_pickle", dataset.workload_pickle_path),
        ("label_pickle", dataset.label_pickle_path),
        ("canonical_workload", dataset.canonical_workload_path),
    ):
        path = path_getter(data_root)
        _require(path.is_file(), f"missing audited source file: {path}")
        _require(sha256_file(path) == result[name], f"{dataset.BENCHMARK_ID} {name} hash drift")
    return result


def _frozen_source_hashes(dataset: Any) -> dict[str, str]:
    """Return source hashes pinned by the adapter contract, without I/O."""

    return {
        "workload_pickle": dataset.WORKLOAD_PICKLE_SHA256,
        "label_pickle": dataset.LABEL_PICKLE_SHA256,
        "canonical_workload": dataset.CANONICAL_WORKLOAD_SHA256,
    }


def build_truth_policy(research_root: Path, data_root: Path | None = None) -> dict[str, Any]:
    """Build the split-aware RQ1b truth policy from audited source metadata."""

    root = research_root.resolve()
    datasets: list[dict[str, Any]] = []
    for dataset_id in DATASET_ORDER:
        dataset = DATASETS[dataset_id]
        metadata = dataset.inspect(data_root)
        hashes = _source_hashes(dataset, data_root)
        slug = _slug(dataset_id)
        valid_path = Path(
            f"truth/arecel/{slug}/authoritative-cardinality-observations-valid-v1.json"
        )
        valid_audit_path = Path(f"truth/arecel/{slug}/audit-valid-v1.json")
        test_path = Path(f"truth/arecel/{slug}/authoritative-cardinality-observations-v1.json")
        test_audit_path = Path(f"truth/arecel/{slug}/audit-v1.json")
        _require((root / valid_path).is_file(), f"missing valid truth wire: {valid_path}")
        _require(
            (root / valid_audit_path).is_file(),
            f"missing valid truth audit: {valid_audit_path}",
        )
        valid_wire = read_json(root / valid_path)
        validate_observation_wire(
            valid_wire, workload_id=_split_workload_id(dataset_id, VALID_SPLIT)
        )
        valid_audit = read_json(root / valid_audit_path)
        _require(
            valid_audit.get("observation_sha256") == sha256_file(root / valid_path),
            "valid truth audit digest drift",
        )
        _require(valid_audit.get("source_split") == VALID_SPLIT, "valid truth audit split drift")
        _require(
            (root / test_path).is_file() and (root / test_audit_path).is_file(),
            "existing test truth is missing",
        )
        test_wire = read_json(root / test_path)
        validate_observation_wire(test_wire, workload_id=_split_workload_id(dataset_id, TEST_SPLIT))
        datasets.append(
            {
                "dataset_id": dataset_id,
                "dataset_content_identity": metadata["dataset_content_identity"],
                "upstream_commit": dataset.UPSTREAM_COMMIT,
                "source_workload_pickle_sha256": hashes["workload_pickle"],
                "source_label_pickle_sha256": hashes["label_pickle"],
                "canonical_workload_sha256": hashes["canonical_workload"],
                "valid_workload_id": _split_workload_id(dataset_id, VALID_SPLIT),
                "valid_query_count": SAMPLE_ROWS,
                "valid_observations_path": valid_path.as_posix(),
                "valid_observations_sha256": sha256_file(root / valid_path),
                "valid_audit_path": valid_audit_path.as_posix(),
                "test_workload_id": _split_workload_id(dataset_id, TEST_SPLIT),
                "test_query_count": SAMPLE_ROWS,
                "test_observations_path": test_path.as_posix(),
                "test_observations_sha256": sha256_file(root / test_path),
                "test_audit_path": test_audit_path.as_posix(),
                "truth_source": "authoritative-external-exact from audited AreCEL labels",
                "authority": "sfu-db/AreCELearnedYet",
            }
        )
    body = {
        "format_version": TRUTH_POLICY_FORMAT,
        "experiment_id": "rq1-held-out-workload-generalization",
        "status": "preregistered",
        "truth_source": "authoritative-external-exact from audited AreCEL labels",
        "upstream": {"repository": "sfu-db/AreCELearnedYet", "commit": UPSTREAM_COMMIT},
        "design_split": VALID_SPLIT,
        "evaluation_split": TEST_SPLIT,
        "datasets": datasets,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_truth_policy(path: Path, research_root: Path | None = None) -> dict[str, Any]:
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    value = read_json(path)
    _require(value.get("format_version") == TRUTH_POLICY_FORMAT, "unsupported RQ1b truth policy")
    _require(value.get("status") == "preregistered", "RQ1b truth policy must be preregistered")
    _require(value.get("design_split") == VALID_SPLIT, "RQ1b design split drift")
    _require(value.get("evaluation_split") == TEST_SPLIT, "RQ1b evaluation split drift")
    _require(value.get("upstream", {}).get("commit") == UPSTREAM_COMMIT, "upstream commit drift")
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "truth policy digest mismatch",
    )
    _require(
        [item.get("dataset_id") for item in value.get("datasets", [])] == list(DATASET_ORDER),
        "truth policy dataset order drift",
    )
    audit_path = root / SOURCE_AUDIT_PATH
    audit = validate_source_audit(audit_path, root)
    audit_value = read_json(audit_path)
    audit_by_dataset = {row["dataset_id"]: row for row in audit_value["datasets"]}
    for row in value["datasets"]:
        dataset = DATASETS[row["dataset_id"]]
        source = audit_by_dataset[row["dataset_id"]]
        _require(
            row.get("dataset_content_identity")
            == dataset.compute_dataset_content_identity(dataset.CSV_SHA256),
            "truth policy dataset identity drift",
        )
        _require(
            row.get("source_workload_pickle_sha256") == dataset.WORKLOAD_PICKLE_SHA256,
            "truth policy workload source hash drift",
        )
        _require(
            row.get("source_label_pickle_sha256") == dataset.LABEL_PICKLE_SHA256,
            "truth policy label source hash drift",
        )
        _require(
            row.get("canonical_workload_sha256") == dataset.CANONICAL_WORKLOAD_SHA256,
            "truth policy canonical workload hash drift",
        )
        for split in (VALID_SPLIT, TEST_SPLIT):
            _require(
                row.get(f"{split}_workload_id") == source[f"{split}_workload_id"],
                "truth policy workload ID drift",
            )
            _require(
                row.get(f"{split}_query_count") == source[f"{split}_query_count"] == SAMPLE_ROWS,
                "truth policy workload count drift",
            )
    _require(audit["dataset_count"] == 4, "truth policy source audit is incomplete")
    return {"status": "valid", "semantic_digest": value["semantic_digest"], "dataset_count": 4}


def build_source_audit(research_root: Path, data_root: Path | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="rq1b-source-audit-") as temporary:
        temporary_path = Path(temporary)
        for dataset_id in DATASET_ORDER:
            dataset = DATASETS[dataset_id]
            metadata = dataset.inspect(data_root)
            source_hashes = _source_hashes(dataset, data_root)
            records = _source_record_summary(dataset, data_root)
            identities = {
                split: _workload_identity(dataset, data_root, split, temporary_path)
                for split in (VALID_SPLIT, TEST_SPLIT)
            }
            valid_hashes = {record["source_query_sha256"] for record in records[VALID_SPLIT]}
            test_hashes = {record["source_query_sha256"] for record in records[TEST_SPLIT]}
            valid_sql = {record["normalized_sql"] for record in records[VALID_SPLIT]}
            test_sql = {record["normalized_sql"] for record in records[TEST_SPLIT]}
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "benchmark_id": metadata["benchmark_id"],
                    "dataset_content_identity": metadata["dataset_content_identity"],
                    "relation": metadata["relation"],
                    "schema_contract_id": metadata["schema_contract"]["id"],
                    "upstream_commit": dataset.UPSTREAM_COMMIT,
                    "source_file_sha256": source_hashes,
                    "split_counts": {split: len(records[split]) for split in SPLIT_ORDER},
                    "valid_workload_id": identities[VALID_SPLIT]["workload_id"],
                    "valid_workload_sha256": identities[VALID_SPLIT]["workload_sha256"],
                    "valid_query_count": identities[VALID_SPLIT]["query_count"],
                    "test_workload_id": identities[TEST_SPLIT]["workload_id"],
                    "test_workload_sha256": identities[TEST_SPLIT]["workload_sha256"],
                    "test_query_count": identities[TEST_SPLIT]["query_count"],
                    "valid_label_coverage": {
                        "covered": len(records[VALID_SPLIT]),
                        "expected": SAMPLE_ROWS,
                        "complete": True,
                    },
                    "test_label_coverage": {
                        "covered": len(records[TEST_SPLIT]),
                        "expected": SAMPLE_ROWS,
                        "complete": True,
                    },
                    "valid_exact_source_query_hash_set_size": len(valid_hashes),
                    "test_exact_source_query_hash_set_size": len(test_hashes),
                    "valid_test_exact_query_hash_overlap_count": len(valid_hashes & test_hashes),
                    "valid_test_normalized_sql_overlap_count": len(valid_sql & test_sql),
                    "query_hash_overlap_semantics": (
                        "set intersection of audited source_query_sha256 values"
                    ),
                    "normalized_sql_overlap_semantics": (
                        "set intersection after collapsing whitespace in audited source SQL"
                    ),
                    "source_split_identity": {
                        "valid": f"arecel:{_slug(dataset_id)}:valid:<index>",
                        "test": f"arecel:{_slug(dataset_id)}:test:<index>",
                    },
                }
            )
    body = {
        "format_version": SOURCE_AUDIT_FORMAT,
        "experiment_id": "rq1-workload-generalization-source-audit",
        "status": "source-audit-complete",
        "upstream": {
            "repository": "sfu-db/AreCELearnedYet",
            "commit": UPSTREAM_COMMIT,
            "generator_contract": {
                "attribute_selection": {"pred_number": 1.0},
                "center": {"distribution": 0.9, "vocab_ood": 0.1},
                "width": {"uniform": 0.5, "exponential": 0.5},
                "counts": dict(EXPECTED_SPLIT_COUNTS),
            },
        },
        "design_workload": {"source_split": VALID_SPLIT, "query_count": SAMPLE_ROWS},
        "evaluation_workload": {"source_split": TEST_SPLIT, "query_count": SAMPLE_ROWS},
        "underlying_data_fixed": True,
        "same_generator_contract": True,
        "no_workload_drift_claim": True,
        "datasets": rows,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def write_source_audit(
    research_root: Path, output: Path | None = None, data_root: Path | None = None
) -> dict[str, Any]:
    root = research_root.resolve()
    destination = output or (root / SOURCE_AUDIT_PATH)
    _require(not destination.exists(), f"source audit already exists: {destination}")
    value = build_source_audit(root, data_root)
    write_json(destination, value)
    return value


def validate_source_audit(path: Path, research_root: Path | None = None) -> dict[str, Any]:
    value = read_json(path)
    _require(value.get("format_version") == SOURCE_AUDIT_FORMAT, "unsupported RQ1b source audit")
    _require(value.get("status") == "source-audit-complete", "source audit is not complete")
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "source audit digest mismatch",
    )
    _require(
        value.get("upstream", {}).get("commit") == UPSTREAM_COMMIT,
        "source audit upstream drift",
    )
    _require(
        value.get("datasets", [])
        and [item.get("dataset_id") for item in value["datasets"]] == list(DATASET_ORDER),
        "source audit dataset order drift",
    )
    _require(
        value.get("design_workload") == {"source_split": VALID_SPLIT, "query_count": SAMPLE_ROWS},
        "source audit design split drift",
    )
    _require(
        value.get("evaluation_workload")
        == {"source_split": TEST_SPLIT, "query_count": SAMPLE_ROWS},
        "source audit evaluation split drift",
    )
    for row in value["datasets"]:
        dataset = DATASETS[row["dataset_id"]]
        _require(
            row.get("benchmark_id") == dataset.BENCHMARK_ID,
            "source audit benchmark identity drift",
        )
        _require(
            row.get("dataset_content_identity")
            == dataset.compute_dataset_content_identity(dataset.CSV_SHA256),
            "source audit dataset identity drift",
        )
        _require(row.get("relation") == dataset.RELATION, "source audit relation drift")
        _require(
            row.get("schema_contract_id") == dataset.SCHEMA_CONTRACT_ID,
            "source audit schema drift",
        )
        recorded_source_hashes = row.get("source_file_sha256")
        _require(
            isinstance(recorded_source_hashes, dict)
            and all(
                recorded_source_hashes.get(key) == _frozen_source_hashes(dataset).get(key)
                for key in ("workload_pickle", "label_pickle", "canonical_workload")
            ),
            "source audit source hash drift",
        )
        _require(
            row.get("split_counts") == dict(EXPECTED_SPLIT_COUNTS),
            "source audit split counts drift",
        )
        for split in (VALID_SPLIT, TEST_SPLIT):
            _require(
                row.get(f"{split}_query_count") == SAMPLE_ROWS,
                "source audit workload count drift",
            )
            _require(
                row.get(f"{split}_workload_id") == _split_workload_id(dataset.BENCHMARK_ID, split),
                "source audit workload ID drift",
            )
        for field in (
            "valid_exact_source_query_hash_set_size",
            "test_exact_source_query_hash_set_size",
            "valid_test_exact_query_hash_overlap_count",
            "valid_test_normalized_sql_overlap_count",
        ):
            _require(
                isinstance(row.get(field), int) and row[field] >= 0,
                f"source audit {field} is invalid",
            )
    return {"status": "valid", "semantic_digest": value["semantic_digest"], "dataset_count": 4}


def build_source_audit_v2(research_root: Path, data_root: Path | None = None) -> dict[str, Any]:
    """Build instance-level overlap accounting from the audited source records."""

    base = build_source_audit(research_root, data_root)
    _require(
        base["semantic_digest"] == SOURCE_AUDIT_V1_DIGEST,
        "recomputed v1 source audit does not match the immutable audit",
    )
    for dataset_id, row in zip(DATASET_ORDER, base["datasets"], strict=True):
        dataset = DATASETS[dataset_id]
        records = _source_record_summary(dataset, data_root)
        valid_hashes = {record["source_query_sha256"] for record in records[VALID_SPLIT]}
        test_hashes = {record["source_query_sha256"] for record in records[TEST_SPLIT]}
        seen_count = sum(
            record["source_query_sha256"] in valid_hashes for record in records[TEST_SPLIT]
        )
        strict_count = len(records[TEST_SPLIT]) - seen_count
        row.update(
            {
                "valid_instance_count": len(records[VALID_SPLIT]),
                "test_instance_count": len(records[TEST_SPLIT]),
                "valid_unique_query_hash_count": len(valid_hashes),
                "test_unique_query_hash_count": len(test_hashes),
                "valid_test_unique_hash_overlap_count": len(valid_hashes & test_hashes),
                "test_instance_count_with_hash_seen_in_valid": seen_count,
                "strict_unseen_test_instance_count": strict_count,
                "strict_unseen_test_fraction": strict_count / len(records[TEST_SPLIT]),
                "valid_duplicate_instance_count": len(records[VALID_SPLIT]) - len(valid_hashes),
                "test_duplicate_instance_count": len(records[TEST_SPLIT]) - len(test_hashes),
            }
        )
    body = {
        **{key: item for key, item in base.items() if key != "semantic_digest"},
        "format_version": SOURCE_AUDIT_V2_FORMAT,
        "supersedes": {
            "path": SOURCE_AUDIT_PATH.as_posix(),
            "semantic_digest": SOURCE_AUDIT_V1_DIGEST,
        },
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_source_audit_v2(path: Path, research_root: Path | None = None) -> dict[str, Any]:
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    value = read_json(path)
    _require(
        value.get("format_version") == SOURCE_AUDIT_V2_FORMAT, "unsupported RQ1b source audit v2"
    )
    _require(value.get("status") == "source-audit-complete", "source audit v2 is not complete")
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "source audit v2 digest mismatch",
    )
    _require(
        value.get("supersedes")
        == {"path": SOURCE_AUDIT_PATH.as_posix(), "semantic_digest": SOURCE_AUDIT_V1_DIGEST},
        "source audit v2 supersession drift",
    )
    validate_source_audit(root / SOURCE_AUDIT_PATH, root)
    v1 = read_json(root / SOURCE_AUDIT_PATH)
    for v1_row, v2_row in zip(v1["datasets"], value.get("datasets", []), strict=True):
        for key, item in v1_row.items():
            _require(v2_row.get(key) == item, f"source audit v2 dropped or changed v1 field: {key}")
        valid_count = v2_row.get("valid_instance_count")
        test_count = v2_row.get("test_instance_count")
        valid_unique = v2_row.get("valid_unique_query_hash_count")
        test_unique = v2_row.get("test_unique_query_hash_count")
        seen = v2_row.get("test_instance_count_with_hash_seen_in_valid")
        strict = v2_row.get("strict_unseen_test_instance_count")
        for name, item in (
            ("valid_instance_count", valid_count),
            ("test_instance_count", test_count),
            ("valid_unique_query_hash_count", valid_unique),
            ("test_unique_query_hash_count", test_unique),
            (
                "valid_test_unique_hash_overlap_count",
                v2_row.get("valid_test_unique_hash_overlap_count"),
            ),
            ("test_instance_count_with_hash_seen_in_valid", seen),
            ("strict_unseen_test_instance_count", strict),
            ("valid_duplicate_instance_count", v2_row.get("valid_duplicate_instance_count")),
            ("test_duplicate_instance_count", v2_row.get("test_duplicate_instance_count")),
        ):
            _require(
                isinstance(item, int) and not isinstance(item, bool) and item >= 0,
                f"invalid {name}",
            )
        _require(valid_count == EXPECTED_SPLIT_COUNTS[VALID_SPLIT], "valid instance count drift")
        _require(test_count == EXPECTED_SPLIT_COUNTS[TEST_SPLIT], "test instance count drift")
        _require(
            valid_unique == v2_row["valid_exact_source_query_hash_set_size"],
            "valid unique count drift",
        )
        _require(
            test_unique == v2_row["test_exact_source_query_hash_set_size"],
            "test unique count drift",
        )
        _require(
            v2_row["valid_test_unique_hash_overlap_count"]
            == v2_row["valid_test_exact_query_hash_overlap_count"],
            "unique overlap count drift",
        )
        _require(
            seen + strict == test_count, "test instance partition does not cover 10000 records"
        )
        _require(
            v2_row["valid_duplicate_instance_count"] == valid_count - valid_unique,
            "valid duplicate count drift",
        )
        _require(
            v2_row["test_duplicate_instance_count"] == test_count - test_unique,
            "test duplicate count drift",
        )
        _require(
            v2_row["strict_unseen_test_fraction"] == strict / test_count,
            "strict-unseen fraction drift",
        )
    return {"status": "valid", "semantic_digest": value["semantic_digest"], "dataset_count": 4}


def build_strict_unseen_membership(
    research_root: Path, data_root: Path | None = None
) -> dict[str, Any]:
    """Build evaluation-only membership using exact audited source query hashes."""

    root = research_root.resolve()
    audit_path = root / SOURCE_AUDIT_V2_PATH
    validate_source_audit_v2(audit_path, root)
    audit = read_json(audit_path)
    datasets: list[dict[str, Any]] = []
    for dataset_id in DATASET_ORDER:
        dataset = DATASETS[dataset_id]
        records = _source_record_summary(dataset, data_root)
        valid_hashes = {record["source_query_sha256"] for record in records[VALID_SPLIT]}
        seen_ids = [
            f"arecel_{_slug(dataset_id)}_test_{record['index']:06d}"
            for record in records[TEST_SPLIT]
            if record["source_query_sha256"] in valid_hashes
        ]
        strict_ids = [
            f"arecel_{_slug(dataset_id)}_test_{record['index']:06d}"
            for record in records[TEST_SPLIT]
            if record["source_query_sha256"] not in valid_hashes
        ]
        row = next(item for item in audit["datasets"] if item["dataset_id"] == dataset_id)
        datasets.append(
            {
                "dataset_id": dataset_id,
                "valid_workload_id": row["valid_workload_id"],
                "test_workload_id": row["test_workload_id"],
                "strict_unseen_test_query_ids": strict_ids,
                "seen_in_valid_test_query_ids": seen_ids,
                "strict_unseen_count": len(strict_ids),
                "seen_in_valid_count": len(seen_ids),
            }
        )
    body = {
        "format_version": STRICT_UNSEEN_FORMAT,
        "experiment_id": "rq1-held-out-workload-generalization",
        "status": "source-membership-complete",
        "source_audit": {
            "path": SOURCE_AUDIT_V2_PATH.as_posix(),
            "semantic_digest": audit["semantic_digest"],
        },
        "membership_key": "exact source_query_sha256",
        "adapted_sql_not_used": True,
        "truth_not_included": True,
        "datasets": datasets,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_strict_unseen_membership(
    path: Path, research_root: Path | None = None
) -> dict[str, Any]:
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    value = read_json(path)
    _require(
        value.get("format_version") == STRICT_UNSEEN_FORMAT, "unsupported strict-unseen artifact"
    )
    _require(
        value.get("status") == "source-membership-complete", "strict-unseen artifact is incomplete"
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "strict-unseen artifact digest mismatch",
    )
    validate_source_audit_v2(root / SOURCE_AUDIT_V2_PATH, root)
    audit = read_json(root / SOURCE_AUDIT_V2_PATH)
    _require(
        value.get("source_audit")
        == {"path": SOURCE_AUDIT_V2_PATH.as_posix(), "semantic_digest": audit["semantic_digest"]},
        "strict-unseen source audit binding drift",
    )
    _require(value.get("membership_key") == "exact source_query_sha256", "strict-unseen key drift")
    _require(value.get("adapted_sql_not_used") is True, "adapted SQL membership is forbidden")
    _require(value.get("truth_not_included") is True, "truth must not be included in membership")
    _require(
        [item.get("dataset_id") for item in value.get("datasets", [])] == list(DATASET_ORDER),
        "strict-unseen dataset order drift",
    )
    for row, audit_row in zip(value["datasets"], audit["datasets"], strict=True):
        expected_ids = [
            f"arecel_{_slug(row['dataset_id'])}_test_{index:06d}" for index in range(SAMPLE_ROWS)
        ]
        seen = row.get("seen_in_valid_test_query_ids")
        strict = row.get("strict_unseen_test_query_ids")
        _require(
            row.get("valid_workload_id") == audit_row["valid_workload_id"],
            "valid workload binding drift",
        )
        _require(
            row.get("test_workload_id") == audit_row["test_workload_id"],
            "test workload binding drift",
        )
        _require(
            isinstance(seen, list) and isinstance(strict, list), "membership lists are missing"
        )
        _require(
            seen == [item for item in expected_ids if item in set(seen)],
            "seen IDs are not canonical",
        )
        _require(
            strict == [item for item in expected_ids if item in set(strict)],
            "strict IDs are not canonical",
        )
        _require(set(seen).isdisjoint(strict), "membership sets overlap")
        _require(
            set(seen) | set(strict) == set(expected_ids), "membership does not cover test split"
        )
        _require(row.get("seen_in_valid_count") == len(seen), "seen count drift")
        _require(row.get("strict_unseen_count") == len(strict), "strict count drift")
        _require(
            row["seen_in_valid_count"] == audit_row["test_instance_count_with_hash_seen_in_valid"],
            "seen instance count does not match source audit",
        )
        _require(
            row["strict_unseen_count"] == audit_row["strict_unseen_test_instance_count"],
            "strict-unseen count does not match source audit",
        )
    return {"status": "valid", "semantic_digest": value["semantic_digest"], "dataset_count": 4}


def protocol_value() -> dict[str, Any]:
    body = {
        "format_version": PROTOCOL_FORMAT,
        "experiment_id": "rq1-held-out-workload-generalization",
        "rq": "RQ1b",
        "status": "preregistered",
        "formal_execution_status": "not-started",
        "research_question": (
            "How well do native extended-statistics definitions selected from the "
            "AreCEL validation workload generalize to the held-out test workload "
            "when the underlying database is unchanged?"
        ),
        "datasets": list(DATASET_ORDER),
        "upstream": {
            "repository": "sfu-db/AreCELearnedYet",
            "commit": UPSTREAM_COMMIT,
            "generator_contract": {
                "attribute_selection": {"pred_number": 1.0},
                "center": {"distribution": 0.9, "vocab_ood": 0.1},
                "width": {"uniform": 0.5, "exponential": 0.5},
                "counts": dict(EXPECTED_SPLIT_COUNTS),
            },
        },
        "design_workload": {
            "source_split": VALID_SPLIT,
            "upstream_split_name": "valid",
            "query_count": SAMPLE_ROWS,
            "role": "design workload",
        },
        "evaluation_workload": {
            "source_split": TEST_SPLIT,
            "upstream_split_name": "test",
            "query_count": SAMPLE_ROWS,
            "role": "held-out evaluation workload",
        },
        "underlying_data_fixed": True,
        "same_generator_contract": True,
        "no_workload_drift_claim": True,
        "leakage_contract": {
            "before_recommendation_sealed": [
                "valid workload",
                "valid truth",
                "database snapshot/sample",
                "schema/native candidate information",
            ],
            "forbidden": [
                "test workload SQL",
                "test truth",
                "test planner estimates",
                "test q-errors",
                "existing test recommendation",
                "test overlap for selection or tuning",
            ],
            "stage_boundary": "seal S_valid before any test workload or test truth access",
        },
        "system": {
            "advisor_sha": ADVISOR_SHA,
            "patched_postgres_sha": PATCHED_POSTGRES_SHA,
            "stock_postgres_sha": STOCK_POSTGRES_SHA,
            "postgres_version": POSTGRES_VERSION,
        },
        "design_parameters": {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "K_s": SCREENING_WIDTH,
            "B": SCREENING_WIDTH,
            "T_seconds": SEARCH_BUDGET_SECONDS,
        },
        "primary_comparison": [
            "PG16-default vs S_valid on W_test",
            "PG16-target10000 vs S_valid on W_test",
        ],
        "primary_metrics": [
            "aggregate q-error",
            "p50",
            "mean",
            "tail metrics consistent with RQ1a",
            "paired improved",
            "paired unchanged",
            "paired worsened",
            "absolute change versus each baseline",
        ],
        "secondary_metrics": [
            "S_valid definition count",
            "S_valid versus S_test intersection",
            "S_valid versus S_test union",
            "S_valid versus S_test Jaccard",
            "J_test(S_valid)",
            "J_test(S_test) when identity-compatible",
        ],
        "completion_semantics": [
            "valid-only design inputs verified",
            "S_valid sealed before test access",
            "fresh stock physical deployment succeeded",
            "all 10,000 test queries evaluated",
            "test truth mapping exact",
            "metrics reported regardless of direction",
            "cleanup passed",
        ],
        "no_acceptance_target": True,
        "forbidden_work": [
            "train workload as Advisor input",
            "new workload generation",
            "workload random split",
            "production exact COUNT",
            "RQ5 work",
        ],
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_protocol(path: Path) -> dict[str, Any]:
    value = read_json(path)
    expected = protocol_value()
    _require(value == expected, "RQ1b protocol drifted from the preregistered contract")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def protocol_v2_value() -> dict[str, Any]:
    base = protocol_value()
    body = _without_digest(base)
    body["format_version"] = PROTOCOL_V2_FORMAT
    body["supersedes"] = {
        "path": PROTOCOL_PATH.as_posix(),
        "semantic_digest": PROTOCOL_V1_DIGEST,
    }
    body["research_question"] = (
        "How well do native extended-statistics definitions selected from the AreCEL "
        "validation workload generalize to the benchmark's held-out test workload, "
        "and how much of that benefit remains on test queries whose exact query "
        "identity never appeared in the design workload?"
    )
    body["primary_evaluation_population"] = {
        "source_split": TEST_SPLIT,
        "upstream_split_name": "test",
        "query_count": SAMPLE_ROWS,
        "scope": "full upstream base:test split",
    }
    body["secondary_evaluation_population"] = {
        "source": STRICT_UNSEEN_PATH.as_posix(),
        "definition": (
            "test records whose exact source_query_sha256 does not occur in any valid record"
        ),
        "scope": "strict-unseen test query subset",
        "evaluation_only": True,
    }
    body["evaluation_stage"] = {
        "design_stage": {
            "inputs": ["valid workload", "valid truth", "database snapshot/sample"],
            "output": "sealed Recommendation S_valid",
        },
        "evaluation_stage": {
            "inputs": [
                "full test workload",
                "test truth",
                "sealed S_valid",
                "strict-unseen membership",
            ],
            "rule": "evaluate all 10000 test queries once, then filter strict-unseen metrics offline",
        },
    }
    body["leakage_contract"]["forbidden"] = [
        *body["leakage_contract"]["forbidden"],
        "strict-unseen membership before Recommendation is sealed",
        "valid/test overlap counts before Recommendation is sealed",
    ]
    body["secondary_metrics"] = [
        *body["secondary_metrics"],
        "strict-unseen test q-error metrics",
        "strict-unseen versus full-test metric comparison",
    ]
    body["future_evaluation_efficiency"] = (
        "evaluate the full test split exactly once and derive strict-unseen metrics by offline filtering"
    )
    body["no_workload_drift_claim"] = True
    body["no_acceptance_target"] = True
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_protocol_v2(path: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(
        value == protocol_v2_value(), "RQ1b protocol v2 drifted from the preregistered contract"
    )
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def write_protocol_v2(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    destination = output or research_root.resolve() / PROTOCOL_V2_PATH
    _require(not destination.exists(), f"RQ1b protocol v2 already exists: {destination}")
    value = protocol_v2_value()
    write_json(destination, value)
    return value


def write_protocol(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    destination = output or research_root.resolve() / PROTOCOL_PATH
    _require(not destination.exists(), f"RQ1b protocol already exists: {destination}")
    value = protocol_value()
    write_json(destination, value)
    return value
