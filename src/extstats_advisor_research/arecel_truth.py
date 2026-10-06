"""Audited AreCEL exact-label observations and truth-policy gates.

The observation file in this module is deliberately DBMS-neutral.  Binding it
to an ``AdvisorSnapshot`` is delegated to the frozen advisor's
``import_authoritative_ground_truth`` implementation via ``external_truth``.
"""

from __future__ import annotations

import gzip
import json
import subprocess
from pathlib import Path
from typing import Any

from .datasets import census13, dmv11, forest10, power7
from .external_truth import import_audited_authoritative_truth, write_authoritative_observations
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .system_freeze import load_system_freeze

OBSERVATIONS_FORMAT = "authoritative-cardinality-observations-v1"
AUDIT_FORMAT = "arecel-authoritative-observations-audit-v1"
EQUIVALENCE_FORMAT = "arecel-truth-equivalence-v1"
POLICY_FORMAT = "benchmark-truth-policy-v1"
EXPECTED_QUERY_COUNT = 10_000
TEST_SPLIT = "test"
ARECEL_UPSTREAM_URL = "https://github.com/sfu-db/AreCELearnedYet"

_DATASETS = {
    census13.BENCHMARK_ID: census13,
    forest10.BENCHMARK_ID: forest10,
    power7.BENCHMARK_ID: power7,
    dmv11.BENCHMARK_ID: dmv11,
}


def _research_sha(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    if len(value) != 40:
        raise ValueError("research repository HEAD is not a full commit SHA")
    return value


def _canonical_census_records(data_root: Path | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with gzip.open(census13.canonical_workload_path(data_root), "rt", encoding="utf-8") as stream:
        for raw in map(json.loads, stream):
            if raw.get("split") != TEST_SPLIT:
                continue
            index = int(raw["index"])
            records.append(
                {
                    "source_index": index,
                    "source_query_id": raw["query_id"],
                    "query_id": f"arecel_census13_test_{index:06d}",
                    "truth": int(raw["source_label"]["cardinality"]),
                    "source_query_sha256": raw["source_query_sha256"],
                }
            )
    return records


def _records(dataset_id: str, data_root: Path | None) -> list[dict[str, Any]]:
    dataset = _DATASETS.get(dataset_id)
    if dataset is None:
        raise ValueError(f"unsupported AreCEL dataset: {dataset_id}")
    if dataset_id == census13.BENCHMARK_ID:
        records = _canonical_census_records(data_root)
    else:
        records = dataset.load_test_records(data_root)
        for record in records:
            index = int(record["source_index"])
            record["query_id"] = f"arecel_{dataset_id.removeprefix('arecel-')}_test_{index:06d}"
    if len(records) != EXPECTED_QUERY_COUNT:
        raise ValueError(f"{dataset_id} must provide exactly {EXPECTED_QUERY_COUNT} test labels")
    if [int(record["source_index"]) for record in records] != list(range(EXPECTED_QUERY_COUNT)):
        raise ValueError(f"{dataset_id} source label indices are not contiguous")
    expected_source_prefix = f"arecel:{dataset_id.removeprefix('arecel-')}:test:"
    for record in records:
        expected_source_id = f"{expected_source_prefix}{int(record['source_index']):06d}"
        if record["source_query_id"] != expected_source_id:
            raise ValueError(f"{dataset_id} source query mapping is not canonical")
        if not isinstance(record.get("source_query_sha256"), str):
            raise TypeError(f"{dataset_id} source query hash is missing")
    return records


def _source_hashes(dataset: Any, data_root: Path | None) -> dict[str, str]:
    metadata = dataset.inspect(data_root)
    hashes = metadata.get("source_file_sha256")
    if not isinstance(hashes, dict):
        raise TypeError(f"{dataset.BENCHMARK_ID} does not expose audited source hashes")
    required = {"workload_pickle", "label_pickle", "canonical_workload"}
    if not required.issubset(hashes):
        raise ValueError(f"{dataset.BENCHMARK_ID} source hash contract is incomplete")
    paths = {
        "workload_pickle": dataset.workload_pickle_path(data_root),
        "label_pickle": dataset.label_pickle_path(data_root),
        "canonical_workload": dataset.canonical_workload_path(data_root),
    }
    result = {name: str(hashes[name]) for name in required}
    for name, path in paths.items():
        if not path.is_file():
            raise FileNotFoundError(path)
        actual = sha256_file(path)
        if actual != result[name]:
            raise ValueError(f"{dataset.BENCHMARK_ID} {name} SHA256 does not match audit")
    return result


def _mapping_contract(dataset_id: str) -> dict[str, str]:
    if dataset_id == census13.BENCHMARK_ID:
        return {
            "projection": "count-star-to-select-star-v1",
            "relation": "identity-preserving-census13-v1",
            "identifiers": "identity-preserving-census13-v1",
        }
    if dataset_id == forest10.BENCHMARK_ID:
        return {
            "projection": "count-star-to-select-star-v1",
            "relation": "safe-exact-source-relation-to-public.forest10-v1",
            "identifiers": "quoted-source-names-to-lowercase-catalog-identifiers-v1",
        }
    if dataset_id == power7.BENCHMARK_ID:
        return {
            "projection": "count-star-to-select-star-v1",
            "relation": "safe-exact-source-relation-to-public.power7-v1",
            "identifiers": "quoted-source-names-to-lowercase-catalog-identifiers-v1",
        }
    return {
        "projection": "count-star-to-select-star-v1",
        "relation": "safe-exact-dmv11-original-to-public.dmv11-v1",
        "identifiers": "safe-source-identifiers-to-lowercase-catalog-identifiers-v1",
    }


def build_authoritative_observations(
    dataset_id: str, data_root: Path | None = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build the exact production wire value and a separate audit manifest."""

    dataset = _DATASETS.get(dataset_id)
    if dataset is None:
        raise ValueError(f"unsupported AreCEL dataset: {dataset_id}")
    records = _records(dataset_id, data_root)
    hashes = _source_hashes(dataset, data_root)
    workload_id = f"arecel_{dataset_id.removeprefix('arecel-')}_test_v1"
    observations = {
        "format_version": OBSERVATIONS_FORMAT,
        "workload_id": workload_id,
        "truths": [
            {"query_id": record["query_id"], "cardinality": int(record["truth"])}
            for record in records
        ],
    }
    mapping = [
        {
            "source_index": int(record["source_index"]),
            "source_query_id": record["source_query_id"],
            "research_query_id": record["query_id"],
            "source_query_sha256": record["source_query_sha256"],
        }
        for record in records
    ]
    metadata = dataset.inspect(data_root)
    audit = {
        "format_version": AUDIT_FORMAT,
        "benchmark_id": dataset_id,
        "dataset_content_identity": metadata["dataset_content_identity"],
        "authority": "sfu-db/AreCELearnedYet",
        "authority_url": ARECEL_UPSTREAM_URL,
        "upstream_commit": dataset.UPSTREAM_COMMIT,
        "source_split": TEST_SPLIT,
        "workload_id": workload_id,
        "source_workload_pickle_sha256": hashes["workload_pickle"],
        "source_label_pickle_sha256": hashes["label_pickle"],
        "canonical_workload_sha256": hashes["canonical_workload"],
        "query_count": EXPECTED_QUERY_COUNT,
        "query_id_mapping": mapping,
        "mapping_contract": _mapping_contract(dataset_id),
        "label_representation": (
            "audited canonical workload records derived from base-original-label.pkl"
        ),
        "cardinalities_are_not_database_recomputed": True,
    }
    return observations, audit


def validate_observation_wire(value: Any, *, workload_id: str | None = None) -> dict[str, Any]:
    """Reject metadata or malformed records before advisor import."""

    if not isinstance(value, dict) or set(value) != {"format_version", "workload_id", "truths"}:
        raise ValueError("authoritative observation wire value has unexpected fields")
    if value["format_version"] != OBSERVATIONS_FORMAT:
        raise ValueError("unsupported authoritative observation format")
    if workload_id is not None and value["workload_id"] != workload_id:
        raise ValueError("authoritative observation workload ID mismatch")
    truths = value["truths"]
    if not isinstance(truths, list) or len(truths) != EXPECTED_QUERY_COUNT:
        raise ValueError("authoritative observations do not contain 10,000 truths")
    query_ids: set[str] = set()
    for item in truths:
        if not isinstance(item, dict) or set(item) != {"query_id", "cardinality"}:
            raise ValueError("authoritative observation contains metadata or malformed fields")
        query_id = item["query_id"]
        cardinality = item["cardinality"]
        if not isinstance(query_id, str) or not query_id or query_id in query_ids:
            raise ValueError("authoritative observation query IDs must be unique strings")
        if not isinstance(cardinality, int) or isinstance(cardinality, bool) or cardinality < 0:
            raise ValueError(
                "authoritative observation cardinalities must be non-negative integers"
            )
        query_ids.add(query_id)
    return {
        "format_version": OBSERVATIONS_FORMAT,
        "workload_id": value["workload_id"],
        "query_count": len(truths),
        "source_artifact_sha256": None,
    }


def write_dataset_observations(
    dataset_id: str,
    output: Path,
    audit_output: Path,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Write one immutable wire artifact and its separate provenance audit."""

    observations, audit = build_authoritative_observations(dataset_id, data_root)
    if output.exists() or audit_output.exists():
        raise FileExistsError("authoritative observation audit artifacts already exist")
    records = {item["query_id"]: int(item["cardinality"]) for item in observations["truths"]}
    write_authoritative_observations(observations["workload_id"], records, output)
    validate_observation_wire(read_json(output), workload_id=observations["workload_id"])
    audit["observation_sha256"] = sha256_file(output)
    audit["observation_semantic_digest"] = semantic_digest(observations)
    write_json(audit_output, audit)
    return audit


def observation_records(path: Path) -> dict[str, int]:
    value = read_json(path)
    validate_observation_wire(value)
    return {item["query_id"]: int(item["cardinality"]) for item in value["truths"]}


def authoritative_truth_spec(dataset_id: str, research_root: Path | None = None) -> dict[str, Any]:
    """Resolve a dataset's pre-registered external truth input, fail-closed."""

    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    policy_path = root / "paper" / "benchmark-truth-policy-v1.json"
    policy = read_json(policy_path)
    validate_truth_policy(policy, research_root=root)
    entry = next((item for item in policy["datasets"] if item["dataset_id"] == dataset_id), None)
    if entry is None or entry["status"] == "blocked":
        raise ValueError(f"no permitted authoritative truth policy for {dataset_id}")
    slug = dataset_id.removeprefix("arecel-")
    observations_path = (
        root / "truth" / "arecel" / slug / ("authoritative-cardinality-observations-v1.json")
    )
    if not observations_path.is_file():
        raise FileNotFoundError(observations_path)
    observation_metadata = validate_observation_wire(
        read_json(observations_path), workload_id=entry["workload_id"]
    )
    observation_sha256 = sha256_file(observations_path)
    audit_path = root / "truth" / "arecel" / slug / "audit-v1.json"
    audit = read_json(audit_path)
    if audit.get("observation_sha256") != observation_sha256:
        raise ValueError(f"{dataset_id} observation audit does not match wire artifact")
    if audit.get("source_label_pickle_sha256") != entry["source_label_sha256"]:
        raise ValueError(f"{dataset_id} label source does not match truth policy")
    return {
        "kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        "authority": entry["authority"],
        "dataset_identity": entry["dataset_identity"],
        "source_revision": entry["source_revision"],
        "observations_path": observations_path,
        "observations_sha256": observation_sha256,
        "query_count": observation_metadata["query_count"],
        "sanity_check_count": 15,
        "policy_status": entry["status"],
    }


def _locate_ground_truth_artifact(research_root: Path, semantic_sha: str) -> Path:
    matches: list[Path] = []
    for path in sorted(research_root.glob("runs/*/ground-truth-v1.json")):
        try:
            if read_json(path).get("semantic_digest") == semantic_sha:
                matches.append(path)
        except (OSError, json.JSONDecodeError):
            continue
    if len(matches) != 1:
        raise ValueError(
            "canary provenance must resolve to exactly one production-exact GroundTruthSet; "
            f"found {len(matches)} matches"
        )
    return matches[0]


def audit_census13_equivalence(
    canary_artifact: Path,
    observations_path: Path,
    output: Path,
    *,
    research_root: Path | None = None,
) -> dict[str, Any]:
    """Compare audited labels with the existing production-exact canary only."""

    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    canary = read_json(canary_artifact)
    provenance = canary.get("provenance", {})
    truth_digest = provenance.get("truth_artifact_semantic_digest")
    if not isinstance(truth_digest, str):
        raise TypeError("Census13 canary lacks bound production-exact truth provenance")
    truth_path = _locate_ground_truth_artifact(root, truth_digest)
    truth = read_json(truth_path)
    if truth.get("source", {}).get("kind") != "production-exact-execution":
        raise ValueError("Census13 equivalence requires production-exact canary truth")
    external = observation_records(observations_path)
    snapshot_path = truth_path.parent / "advisor-snapshot"
    if not snapshot_path.is_dir():
        raise FileNotFoundError(snapshot_path)
    bound_external = import_audited_authoritative_truth(
        snapshot_path,
        observations_path,
        authority="sfu-db/AreCELearnedYet",
        dataset_identity=str(canary.get("dataset", {}).get("content_identity")),
        source_revision=census13.UPSTREAM_COMMIT,
    )
    if bound_external.source_snapshot_semantic_digest != truth.get(
        "source_snapshot_semantic_digest"
    ):
        raise ValueError("external Census13 observations did not bind to the canary snapshot")
    production = {
        item["query_id"]: int(item["cardinality"])
        for item in truth.get("truths", [])
        if isinstance(item, dict)
    }
    external_ids = set(external)
    production_ids = set(production)
    missing = sorted(production_ids - external_ids)
    extra = sorted(external_ids - production_ids)
    mismatched = sorted(
        query_id
        for query_id in external_ids & production_ids
        if external[query_id] != production[query_id]
    )
    matched = len(external_ids & production_ids) - len(mismatched)
    count = EXPECTED_QUERY_COUNT
    if len(external) != count or len(production) != count:
        raise ValueError("Census13 equivalence requires two complete 10,000-query truth sets")
    dataset = canary.get("dataset", {})
    workload = canary.get("workload", {})
    system = load_system_freeze()
    payload: dict[str, Any] = {
        "format_version": EQUIVALENCE_FORMAT,
        "dataset_id": dataset.get("dataset_id"),
        "dataset_content_identity": dataset.get("content_identity"),
        "workload_id": workload.get("workload_id"),
        "query_count": count,
        "matched": matched,
        "mismatched": mismatched.__len__(),
        "missing": missing.__len__(),
        "extra": extra.__len__(),
        "external_observations_sha256": sha256_file(observations_path),
        "bound_external_ground_truth_semantic_digest": bound_external.computed_semantic_digest,
        "bound_snapshot_semantic_digest": bound_external.source_snapshot_semantic_digest,
        "production_exact_ground_truth_semantic_digest": truth_digest,
        "production_exact_ground_truth_logical_path": str(truth_path.relative_to(root)),
        "canary_artifact_sha256": sha256_file(canary_artifact),
        "research_commit_sha": _research_sha(root),
        "system_freeze_semantic_digest": semantic_digest(system),
        "sut_identity": {
            "advisor_commit_sha": system["advisor"]["commit_sha"],
            "patched_postgresql_commit_sha": system["patched_postgresql"]["source_commit_sha"],
            "stock_postgresql_commit_sha": system["stock_postgresql"]["source_commit_sha"],
        },
    }
    payload["status"] = (
        "validated-full-equivalence"
        if payload["matched"] == count
        and payload["mismatched"] == 0
        and payload["missing"] == 0
        and payload["extra"] == 0
        else "blocked"
    )
    payload["policy"] = (
        "authoritative-external-exact validated against production exact execution"
        if payload["status"] == "validated-full-equivalence"
        else "do not switch Census13 truth policy"
    )
    payload["semantic_digest"] = semantic_digest(
        {key: value for key, value in payload.items() if key != "semantic_digest"}
    )
    if output.exists():
        raise FileExistsError(f"equivalence artifact already exists: {output}")
    write_json(output, payload)
    return payload


def validate_truth_policy(policy: Any, *, research_root: Path | None = None) -> dict[str, Any]:
    """Ensure full-equivalence claims are backed by the compact audit."""

    if not isinstance(policy, dict) or policy.get("format_version") != POLICY_FORMAT:
        raise ValueError("unsupported benchmark truth policy")
    datasets = policy.get("datasets")
    if not isinstance(datasets, list) or len(datasets) != len(_DATASETS):
        raise ValueError("truth policy must cover all four AreCEL datasets")
    seen: set[str] = set()
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    for entry in datasets:
        if not isinstance(entry, dict):
            raise TypeError("truth policy dataset entry must be an object")
        dataset_id = entry.get("dataset_id")
        if dataset_id not in _DATASETS or dataset_id in seen:
            raise ValueError("truth policy has duplicate or unsupported dataset")
        seen.add(dataset_id)
        if (
            entry.get("primary_truth_source")
            != "authoritative-external-exact from audited AreCEL labels"
        ):
            raise ValueError("truth policy does not pre-register audited external truth")
        status = entry.get("status")
        if status not in {"validated-full-equivalence", "validated-provenance", "blocked"}:
            raise ValueError("truth policy has unsupported status")
        evidence = entry.get("equivalence_evidence")
        if not isinstance(evidence, dict):
            raise TypeError("truth policy is missing equivalence evidence")
        if status == "validated-full-equivalence":
            artifact = evidence.get("artifact")
            if not isinstance(artifact, str):
                raise ValueError("full-equivalence policy lacks an audit artifact")
            audit = read_json(root / artifact)
            if not (
                audit.get("dataset_id") == dataset_id
                and audit.get("workload_id") == entry.get("workload_id")
                and audit.get("status") == "validated-full-equivalence"
                and audit.get("matched") == EXPECTED_QUERY_COUNT
                and audit.get("mismatched") == 0
                and audit.get("missing") == 0
                and audit.get("extra") == 0
            ):
                raise ValueError("full-equivalence policy is not backed by a passing audit")
        if status == "validated-provenance" and evidence.get("status") == "full-equivalence":
            raise ValueError("provenance validation cannot claim full equivalence")
    if seen != set(_DATASETS):
        raise ValueError("truth policy does not cover all four AreCEL datasets")
    return {"status": "valid", "format_version": POLICY_FORMAT, "dataset_count": len(seen)}


__all__ = [
    "AUDIT_FORMAT",
    "EQUIVALENCE_FORMAT",
    "EXPECTED_QUERY_COUNT",
    "OBSERVATIONS_FORMAT",
    "POLICY_FORMAT",
    "audit_census13_equivalence",
    "authoritative_truth_spec",
    "build_authoritative_observations",
    "observation_records",
    "validate_observation_wire",
    "validate_truth_policy",
    "write_dataset_observations",
]
