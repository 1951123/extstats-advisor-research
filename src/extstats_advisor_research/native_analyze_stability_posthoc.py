"""Offline, post-hoc analysis of the Native ANALYZE Stability v1 evidence.

This module does not relax the v1 validator and never changes v1 artifacts. It
adds the narrower v2 interpretation in which a present dependency statistic
with an empty reader result may be classified as native-null-supported when
the persisted parent/clone controls and frozen source code support that
interpretation.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .native_analyze_stability import METHOD_ORDER, _dataset_binding
from .paper_baseline import percentile, qerror
from .provenance import read_json, semantic_digest, sha256_file, write_json

V2_FORMAT = "native-analyze-stability-protocol-v2"
POSTHOC_FORMAT = "native-analyze-stability-posthoc-analysis-v2"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
DATASETS = ("arecel-forest10", "arecel-census13", "arecel-dmv11")
SUMMARY_DIRS = {
    dataset: f"experiments/native-analyze-stability-v1/formal-nas-formal-20261010-{dataset[7:]}"
    for dataset in DATASETS
}


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _digest(value: dict[str, Any]) -> dict[str, Any]:
    value["semantic_digest"] = semantic_digest(_body(value))
    return value


def _sql_digest(sql: str) -> str:
    return hashlib.sha256(sql.encode("utf-8")).hexdigest()


def _source_query_binding(
    root: Path, dataset_id: str
) -> tuple[list[str], dict[str, int], list[str]]:
    """Read the already frozen source bundle used by the formal execution."""

    from .native_analyze_stability_formal import _bound_query_records

    return _bound_query_records(root, dataset_id)


def _records(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _metric(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if len(records) != 10000:
        raise ValueError(f"expected 10000 records, got {len(records)}")
    ids = [record.get("query_id") for record in records]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise ValueError("query IDs are missing or duplicated")
    values: list[float] = []
    for record in records:
        estimate = float(record["plan_rows"])
        truth = float(record["truth"])
        observed = float(record["qerror"])
        expected = qerror(estimate, truth)
        if not math.isclose(observed, expected, rel_tol=1e-12, abs_tol=1e-12):
            raise ValueError(f"q-error mismatch for {record['query_id']}")
        if not math.isfinite(estimate) or not math.isfinite(truth) or estimate < 0 or truth < 0:
            raise ValueError(f"invalid observation for {record['query_id']}")
        values.append(expected)
    return {
        "query_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "quantile_method": "linear-interpolation-n-minus-1",
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "lower_is_better": True,
    }


def _rank(method_metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted((float(value["mean"]), method) for method, value in method_metrics.items())
    best = ordered[0][0]
    return {
        "ordered_methods": [method for _, method in ordered],
        "best_methods": [method for value, method in ordered if value == best],
        "best_mean": best,
    }


def _classify_payload(item: Mapping[str, Any], controls: Mapping[str, Any]) -> str:
    if item.get("payload_present"):
        return "nonempty"
    if (
        item.get("kind") == "postgresql.dependencies"
        and item.get("payload_bytes") == 0
        and item.get("payload_sha256") == EMPTY_SHA256
        and controls.get("payloads_exactly_preserved_after_drop") is True
        and controls.get("ordinary_statistics_equal_to_parent") is True
        and controls.get("post_drop_analyze_count") == 0
    ):
        return "native-null-supported"
    return "unverified"


def _validate_query_identity(
    records: Sequence[Mapping[str, Any]],
    expected_ids: Sequence[str],
    truth_by_id: Mapping[str, int],
    sql_digests: Sequence[str],
) -> None:
    if [record.get("query_id") for record in records] != list(expected_ids):
        raise ValueError("query identity/order mismatch")
    for record, expected_sql in zip(records, sql_digests, strict=True):
        query_id = record["query_id"]
        if record.get("truth") != truth_by_id[query_id]:
            raise ValueError(f"truth mismatch for {query_id}")
        if _sql_digest(str(record.get("sql", ""))) != expected_sql:
            raise ValueError(f"SQL digest mismatch for {query_id}")


def _payload_audit(
    realizations: Sequence[Mapping[str, Any]], union: Sequence[str]
) -> dict[str, Any]:
    by_candidate: dict[str, list[dict[str, Any]]] = {candidate: [] for candidate in union}
    for realization in realizations:
        parent = realization["shared_parent"]
        observed = {item["candidate_id"]: item for item in parent["physical_statistics"]}
        if set(observed) != set(union):
            raise ValueError("shared parent statistics inventory differs from canonical union")
        controls = next(iter(realization["methods"].values()))["controls"]
        for candidate in union:
            item = observed[candidate]
            classification = _classify_payload(item, controls)
            by_candidate[candidate].append(
                {
                    "realization_id": realization["realization_id"],
                    "classification": classification,
                    "payload_present": bool(item.get("payload_present")),
                    "payload_sha256": item.get("payload_sha256"),
                    "payload_bytes": item.get("payload_bytes"),
                    "kind": item.get("kind"),
                    "oid": item.get("oid"),
                }
            )
    candidate_results: dict[str, Any] = {}
    for candidate, observations in by_candidate.items():
        fingerprints = [
            f"{item['classification']}:{item['payload_sha256']}" for item in observations
        ]
        candidate_results[candidate] = {
            "classifications": [item["classification"] for item in observations],
            "availability_counts": dict(Counter(item["classification"] for item in observations)),
            "distinct_observed_fingerprints": len(set(fingerprints)),
            "changed_vs_realization_01_fraction": sum(
                fingerprint != fingerprints[0] for fingerprint in fingerprints[1:]
            )
            / 4,
            "observations": observations,
        }
    all_items = [item for values in by_candidate.values() for item in values]
    return {
        "selected_payload_observation_count": len(all_items),
        "classification_counts": dict(Counter(item["classification"] for item in all_items)),
        "by_candidate": candidate_results,
    }


def _analyze_dataset(root: Path, dataset_id: str) -> dict[str, Any]:
    summary_path = root / SUMMARY_DIRS[dataset_id] / "summary-v1.json"
    summary = read_json(summary_path)
    binding = _dataset_binding(root, dataset_id)
    expected_ids, truth_by_id, sql_digests = _source_query_binding(root, dataset_id)
    if summary["dataset_id"] != dataset_id or summary["completed_realizations"] != 5:
        raise ValueError(f"summary identity/completeness mismatch: {dataset_id}")
    if summary["source_binding"]["method_memberships"] != binding["method_memberships"]:
        raise ValueError(f"fixed membership mismatch: {dataset_id}")
    union = list(binding["method_memberships"].values())
    union_ids = sorted({candidate for members in union for candidate in members})
    realizations = summary["realizations"]
    per_realization: list[dict[str, Any]] = []
    method_values: dict[str, list[float]] = {method: [] for method in METHOD_ORDER}
    method_metrics: dict[str, list[dict[str, Any]]] = {method: [] for method in METHOD_ORDER}
    method_records: dict[str, list[list[dict[str, Any]]]] = {method: [] for method in METHOD_ORDER}
    ordinary_fingerprints: list[str] = []
    for realization in realizations:
        if (
            realization["status"] != "complete"
            or realization["oid_order_status"] != "verified"
            or realization["analyze_count"] != 1
            or realization["cleanup_status"] != "complete"
        ):
            raise ValueError(f"realization control mismatch: {realization['realization_id']}")
        if sorted(realization["canonical_union"]) != union_ids:
            raise ValueError("canonical union mismatch")
        parent = realization["shared_parent"]
        ordinary_fingerprints.append(str(parent["ordinary_statistics_fingerprint"]))
        controls_by_method: dict[str, Any] = {}
        current: dict[str, dict[str, Any]] = {}
        for method in METHOD_ORDER:
            arm = realization["methods"].get(method)
            if arm is None or arm["selected_membership"] != binding["method_memberships"][method]:
                raise ValueError(f"method membership mismatch: {dataset_id}/{method}")
            controls = arm["controls"]
            controls_by_method[method] = controls
            if (
                controls.get("analyze_after_drop") is not False
                or controls.get("post_drop_analyze_count") != 0
                or controls.get("ordinary_statistics_equal_to_parent") is not True
                or controls.get("payloads_exactly_preserved_after_drop") is not True
                or controls.get("selected_deployed_membership_equal") is not True
            ):
                raise ValueError(f"clone control mismatch: {dataset_id}/{method}")
            raw_path = summary_path.parent / arm["query_evidence"]["path"]
            if sha256_file(raw_path) != arm["query_evidence"]["sha256"]:
                raise ValueError(f"raw hash mismatch: {raw_path}")
            records = _records(raw_path)
            _validate_query_identity(records, expected_ids, truth_by_id, sql_digests)
            current[method] = _metric(records)
            method_values[method].append(current[method]["mean"])
            method_metrics[method].append(current[method])
            method_records[method].append(records)
        per_realization.append(
            {
                "realization_id": realization["realization_id"],
                "rank": _rank(current),
                "ordinary_statistics_fingerprint": ordinary_fingerprints[-1],
                "clone_controls": controls_by_method,
            }
        )
    payload_audit = _payload_audit(realizations, union_ids)
    by_method = {
        method: {
            "realization_metrics": method_metrics[method],
            "mean_qerror_min": min(method_values[method]),
            "mean_qerror_max": max(method_values[method]),
            "mean_qerror_population_stddev": statistics.pstdev(method_values[method]),
            "first_or_tie_count": sum(
                method in item["rank"]["best_methods"] for item in per_realization
            ),
            "plan_rows_changed_vs_realization_01": [
                {
                    "realization_id": realizations[index]["realization_id"],
                    "count": sum(
                        left["plan_rows"] != right["plan_rows"]
                        for left, right in zip(
                            method_records[method][0], method_records[method][index], strict=True
                        )
                    ),
                    "fraction": sum(
                        left["plan_rows"] != right["plan_rows"]
                        for left, right in zip(
                            method_records[method][0], method_records[method][index], strict=True
                        )
                    )
                    / 10000,
                }
                for index in range(5)
            ],
        }
        for method in METHOD_ORDER
    }
    paired: list[str] = []
    for index in range(5):
        left = method_values["greedy-ADD"][index]
        right = method_values["singleton-utility-top-k"][index]
        paired.append(
            "greedy-better" if left < right else "singleton-better" if left > right else "tie"
        )
    return {
        "dataset_id": dataset_id,
        "comparability_stratum": binding["comparability_stratum"],
        "summary": {
            "path": str(summary_path.relative_to(root)),
            "semantic_digest": summary["semantic_digest"],
            "sha256": sha256_file(summary_path),
        },
        "source_binding": {
            "system_freeze": binding["system_freeze_artifact"],
            "workload": binding["workload"],
            "truth": binding["truth_binding"],
            "method_memberships": binding["method_memberships"],
            "candidate_definitions_digest": summary["source_binding"][
                "candidate_definition_digest"
            ],
        },
        "realization_count": 5,
        "method_arm_count": 45,
        "physical_explain_count": 450000,
        "per_method": by_method,
        "per_realization": per_realization,
        "greedy_vs_singleton": {
            "outcomes": paired,
            "win_tie_loss": {
                "greedy_wins": paired.count("greedy-better"),
                "ties": paired.count("tie"),
                "singleton_wins": paired.count("singleton-better"),
            },
            "mean_difference_greedy_minus_singleton": [
                method_values["greedy-ADD"][index] - method_values["singleton-utility-top-k"][index]
                for index in range(5)
            ],
        },
        "ordinary_statistics": {
            "fingerprints": ordinary_fingerprints,
            "distinct_count": len(set(ordinary_fingerprints)),
            "changed_vs_realization_01_fraction": sum(
                value != ordinary_fingerprints[0] for value in ordinary_fingerprints[1:]
            )
            / 4,
        },
        "payload_audit": payload_audit,
        "qualification": {
            "status": "qualified-descriptive",
            "native_null_directly_verified_count": 0,
            "native_null_supported_count": payload_audit["classification_counts"].get(
                "native-null-supported", 0
            ),
            "v1_nonempty_payload_gate_satisfied": False,
        },
    }


def analyze_posthoc(root: Path, protocol_path: Path, output: Path) -> dict[str, Any]:
    protocol = read_json(protocol_path)
    if protocol.get("format_version") != V2_FORMAT:
        raise ValueError("unsupported v2 protocol")
    if protocol.get("semantic_digest") != semantic_digest(_body(protocol)):
        raise ValueError("v2 protocol semantic digest mismatch")
    failure_path = (
        root / "experiments/native-analyze-stability-v1/formal-campaign-validation-failure-v1.json"
    )
    analyses = {dataset: _analyze_dataset(root, dataset) for dataset in DATASETS}
    result = _digest(
        {
            "format_version": POSTHOC_FORMAT,
            "analysis_id": "native-analyze-stability-posthoc-analysis-v2",
            "status": "qualified-descriptive-posthoc",
            "protocol": {
                "path": str(protocol_path.relative_to(root)),
                "semantic_digest": protocol["semantic_digest"],
                "sha256": sha256_file(protocol_path),
            },
            "v1_status": "failed/ineligible with evidence preserved",
            "v1_failure_artifact": {
                "path": str(failure_path.relative_to(root)),
                "semantic_digest": read_json(failure_path)["semantic_digest"],
                "sha256": sha256_file(failure_path),
            },
            "source_code_interpretation": {
                "classification": "native-null-supported",
                "basis": [
                    "statext_dependencies_build returns NULL when no nonzero dependency is retained",
                    "statext_store marks stxddependencies NULL when the built data pointer is NULL",
                    "v1 payload reader found existing dependency objects but persisted only empty bytes, not is_null metadata",
                ],
            },
            "datasets": analyses,
            "overall_counts": {
                "dataset_count": 3,
                "realization_count": 15,
                "method_arm_count": 135,
                "physical_explain_count": 1350000,
                "raw_observation_count": 1350000,
            },
            "verified_evidence": [
                "all 135 raw gzip files exist and match their recorded byte hashes",
                "all 135 arms contain exactly 10,000 observations",
                "query identity, SQL digest, truth, q-error and metric arithmetic were independently recomputed",
                "all realization OID, one-parent-ANALYZE, zero-clone-ANALYZE, cleanup and clone-control fields pass",
            ],
            "qualified_evidence": [
                "six empty dependency payloads are native-null-supported, not native-null-verified",
                "cross-realization payload and ordinary-statistics drift is descriptive and not decomposed causally",
                "Forest10 historical-v1 remains a separate provenance stratum",
            ],
            "unsupported_claims": [
                "v1 was a complete confirmatory stability experiment",
                "native ANALYZE is deterministic or universally stable",
                "ordinary-statistics and extended-statistics contributions are causally separated",
                "five realizations establish population-level ranking probabilities",
            ],
        }
    )
    write_json(output, result)
    return result


def validate_posthoc_artifact(value: Mapping[str, Any], root: Path) -> dict[str, str]:
    if value.get("format_version") != POSTHOC_FORMAT:
        raise ValueError("unsupported posthoc artifact")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("posthoc artifact semantic digest mismatch")
    if value.get("status") != "qualified-descriptive-posthoc":
        raise ValueError("posthoc artifact status is not qualified")
    if value.get("v1_status") != "failed/ineligible with evidence preserved":
        raise ValueError("v1 status was promoted")
    if value.get("overall_counts", {}).get("raw_observation_count") != 1_350_000:
        raise ValueError("posthoc raw observation count mismatch")
    failure = read_json(root / value["v1_failure_artifact"]["path"])
    if failure.get("status") != "failed-scientific-control":
        raise ValueError("v1 failure artifact status mismatch")
    if failure.get("semantic_digest") != value["v1_failure_artifact"]["semantic_digest"]:
        raise ValueError("v1 failure digest mismatch")
    return {"status": "valid", "semantic_digest": str(value["semantic_digest"])}
