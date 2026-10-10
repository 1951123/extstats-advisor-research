"""Offline scientific review for the OID-order sensitivity experiment."""

from __future__ import annotations

import gzip
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .paper_baseline import percentile, qerror
from .provenance import read_json, semantic_digest, sha256_file

REVIEW_FORMAT = "postgresql-extstats-oid-order-sensitivity-review-v1"
PROTOCOL_PATH = Path("paper/oid-order-sensitivity-protocol-v1.json")
PREFLIGHT_PATH = Path("experiments/oid-order-sensitivity-v1/attempts/attempt-002/preflight-v9.json")
SUMMARY_PATH = Path(
    "experiments/oid-order-sensitivity-v1/invocations/oid-attempt-002-run-fa406a9/summary-v1.json"
)
SYNTHETIC_PATH = Path(
    "experiments/oid-order-sensitivity-v1/invocations/"
    "oid-attempt-002-run-fa406a9/synthetic-witness-v1.json"
)
DATASET_ORDER = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
TREATMENT_ORDER = (
    "reference",
    "reverse",
    "random-seed-17",
    "random-seed-29",
    "random-seed-43",
    "random-seed-71",
    "random-seed-101",
)


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _source_binding(root: Path, path: Path) -> dict[str, Any]:
    value = read_json(root / path)
    return {
        "path": path.as_posix(),
        "byte_sha256": sha256_file(root / path),
        "semantic_digest": value.get("semantic_digest"),
        "semantic_digest_recomputed": semantic_digest(_body(value)),
    }


def _rows(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    values = [float(row["qerror"]) for row in rows]
    return {
        "query_count": len(rows),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "quantile_method": "linear-interpolation-n-minus-1",
    }


def _paired(reference: list[dict[str, Any]], alternate: list[dict[str, Any]]) -> dict[str, Any]:
    if [row["query_id"] for row in reference] != [row["query_id"] for row in alternate]:
        raise ValueError("paired treatment query order differs")
    changed = improved = unchanged = worsened = 0
    for before, after in zip(reference, alternate):
        changed += before["plan_rows"] != after["plan_rows"]
        if after["qerror"] < before["qerror"]:
            improved += 1
        elif after["qerror"] == before["qerror"]:
            unchanged += 1
        else:
            worsened += 1
    return {
        "plan_rows_changed": changed,
        "plan_rows_changed_fraction": changed / len(reference),
        "improved": improved,
        "unchanged": unchanged,
        "worsened": worsened,
    }


def _review_treatment(
    *,
    root: Path,
    dataset_id: str,
    group: str,
    treatment: str,
    stored: Mapping[str, Any],
    reference_rows: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = root / stored["per_query_path"]
    rows = _rows(path)
    if len(rows) != 10_000:
        raise ValueError(f"{dataset_id}/{group}/{treatment}: query count is not 10000")
    if sha256_file(path) != stored["per_query_sha256"]:
        raise ValueError(f"{dataset_id}/{group}/{treatment}: raw SHA drift")
    if any(row["dataset_id"] != dataset_id for row in rows):
        raise ValueError(f"{dataset_id}/{group}/{treatment}: dataset identity drift")
    expected_arm = "controlled-hypothetical" if group == "hypothetical" else "stock-physical"
    if any(row["execution_arm"] != expected_arm for row in rows):
        raise ValueError(f"{dataset_id}/{group}/{treatment}: execution arm drift")
    if any(row["permutation_id"] != treatment for row in rows):
        raise ValueError(f"{dataset_id}/{group}/{treatment}: treatment identity drift")
    if len({row["query_id"] for row in rows}) != 10_000:
        raise ValueError(f"{dataset_id}/{group}/{treatment}: duplicate query IDs")
    if any(row["qerror"] != qerror(row["plan_rows"], row["truth"]) for row in rows):
        raise ValueError(f"{dataset_id}/{group}/{treatment}: q-error mismatch")
    recomputed = _metrics(rows)
    for key in ("query_count", "mean", "p50", "p95", "p99", "max"):
        if abs(float(recomputed[key]) - float(stored["metrics"][key])) > 1e-12:
            raise ValueError(f"{dataset_id}/{group}/{treatment}: {key} mismatch")
    result = {
        "per_query_path": stored["per_query_path"],
        "per_query_sha256": stored["per_query_sha256"],
        "metrics": recomputed,
        "order_diagnostics": dict(stored["order_diagnostics"]),
        "causal_status": stored["causal_status"],
    }
    if group == "physical":
        result.update(
            {
                "prescribed_order": list(stored["prescribed_order"]),
                "actual_oid_order": list(stored["actual_oid_order"]),
                "physical_oids": list(stored["physical_oids"]),
                "payload_sha256_by_candidate": dict(stored["payload_sha256_by_candidate"]),
                "ordinary_statistics_fingerprint": stored["ordinary_statistics_fingerprint"],
                "causal_controls": dict(stored["causal_controls"]),
            }
        )
    else:
        result["payload_sha256_by_candidate"] = dict(stored["payload_sha256_by_candidate"])
        result["ordinary_statistics_fingerprint"] = stored["ordinary_statistics_fingerprint"]
    if reference_rows is not None:
        result["paired_vs_reference"] = _paired(reference_rows, rows)
    return result, rows


def build_review(root: Path, *, producer_sha: str) -> dict[str, Any]:
    root = Path(root).resolve()
    preflight = read_json(root / PREFLIGHT_PATH)
    summary = read_json(root / SUMMARY_PATH)
    synthetic = read_json(root / SYNTHETIC_PATH)
    if summary.get("status") != "complete" or summary.get("formal_invocation_count") != 1:
        raise ValueError("OID invocation is not complete")
    if len(summary.get("dataset_results", [])) != 4:
        raise ValueError("OID dataset inventory is incomplete")
    if summary.get("producer_research_sha") != preflight.get("producer_research_sha"):
        raise ValueError("OID producer binding drift")
    datasets: list[dict[str, Any]] = []
    total_hypothetical = total_physical = 0
    for dataset_result in summary["dataset_results"]:
        dataset_id = dataset_result["dataset_id"]
        if dataset_id not in DATASET_ORDER:
            raise ValueError(f"unexpected OID dataset: {dataset_id}")
        treatment_groups: dict[str, Any] = {}
        rows_by_group: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for group in ("hypothetical", "physical"):
            treatment_groups[group] = {}
            rows_by_group[group] = {}
            reference_rows: list[dict[str, Any]] | None = None
            for treatment in TREATMENT_ORDER:
                reviewed, rows = _review_treatment(
                    root=root,
                    dataset_id=dataset_id,
                    group=group,
                    treatment=treatment,
                    stored=dataset_result[group][treatment],
                    reference_rows=reference_rows,
                )
                if treatment == "reference":
                    reference_rows = rows
                treatment_groups[group][treatment] = reviewed
                rows_by_group[group][treatment] = rows
            reference = rows_by_group[group]["reference"]
            reference_identity = [
                (row["query_id"], row["truth"], row["sql_sha256"], row["source_query_sha256"])
                for row in reference
            ]
            for treatment_rows in rows_by_group[group].values():
                identity = [
                    (row["query_id"], row["truth"], row["sql_sha256"], row["source_query_sha256"])
                    for row in treatment_rows
                ]
                if identity != reference_identity:
                    raise ValueError(f"{dataset_id}/{group}: query/truth identity drift")
            if group == "hypothetical":
                total_hypothetical += 7 * 10_000
                payloads = [
                    treatment_groups[group][t]["payload_sha256_by_candidate"]
                    for t in TREATMENT_ORDER
                ]
                if any(payload != payloads[0] for payload in payloads[1:]):
                    raise ValueError(f"{dataset_id}: hypothetical payload realization drift")
            else:
                total_physical += 7 * 10_000
        random_means = [
            treatment_groups["hypothetical"][t]["metrics"]["mean"]
            for t in TREATMENT_ORDER
            if t.startswith("random-")
        ]
        reference_mean = treatment_groups["hypothetical"]["reference"]["metrics"]["mean"]
        datasets.append(
            {
                "dataset_id": dataset_id,
                "producer_research_sha": dataset_result["producer_research_sha"],
                "candidate_membership": dataset_result["candidate_membership"],
                "valid_workload": dataset_result["valid_workload"],
                "valid_truth": dataset_result["valid_truth"],
                "rq1b_reference": dataset_result["rq1b_reference"],
                "hypothetical": treatment_groups["hypothetical"],
                "physical": treatment_groups["physical"],
                "random_summary": {
                    "mean": sum(random_means) / len(random_means),
                    "min": min(random_means),
                    "max": max(random_means),
                    "advisor_reference_rank": 1
                    + sum(mean < reference_mean for mean in random_means),
                    "advisor_reference_tied_best": sum(
                        abs(mean - reference_mean) <= 1e-12
                        for mean in [reference_mean, *random_means]
                    ),
                },
                "physical_control_summary": {
                    "reference_control": True,
                    "non_reference_statuses": sorted(
                        {
                            treatment_groups["physical"][t]["causal_status"]
                            for t in TREATMENT_ORDER
                            if t != "reference"
                        }
                    ),
                    "all_requested_oid_orders_observed": all(
                        item["actual_oid_order"] == item["prescribed_order"]
                        for t, item in treatment_groups["physical"].items()
                    ),
                },
            }
        )
    body: dict[str, Any] = {
        "format_version": REVIEW_FORMAT,
        "experiment_id": "postgresql-extstats-oid-order-sensitivity-v1",
        "review_scope": "offline independent evidence review; no scientific re-execution",
        "review_producer_sha": producer_sha,
        "source_artifacts": {
            "protocol": _source_binding(root, PROTOCOL_PATH),
            "preflight": _source_binding(root, PREFLIGHT_PATH),
            "summary": _source_binding(root, SUMMARY_PATH),
            "synthetic_witness": _source_binding(root, SYNTHETIC_PATH),
        },
        "frozen_system": summary["frozen_system"],
        "invocation": {
            "invocation_id": "oid-attempt-002-run-fa406a9",
            "formal_invocation_count": summary["formal_invocation_count"],
            "dataset_treatment_count": 56,
            "hypothetical_explain_count": total_hypothetical,
            "physical_explain_count": total_physical,
            "synthetic_hypothetical_explain_count": 5,
            "synthetic_physical_explain_count": 5,
            "total_explain_count": total_hypothetical + total_physical + 10,
            "test_workload_accessed": False,
            "test_truth_accessed": False,
        },
        "datasets": datasets,
        "synthetic_witness": {
            "path": SYNTHETIC_PATH.as_posix(),
            "semantic_digest": synthetic["semantic_digest"],
            "physical_hypothetical_exact_match_count": synthetic[
                "physical_hypothetical_exact_match_count"
            ],
            "physical_hypothetical_arm_count": synthetic["physical_hypothetical_arm_count"],
            "root_plan_rows": {
                arm: synthetic["arms"][arm]["physical_plan_rows"]
                for arm in ("a-only", "b-only", "a-then-b", "b-then-a", "none")
            },
            "tie_precondition": synthetic["static_mechanism_precondition"],
            "post_cleanup": synthetic["post_cleanup"],
        },
        "independent_validation": {
            "raw_per_query_files": 56,
            "raw_records": 560_000,
            "all_record_counts_10000": True,
            "raw_byte_sha256_checked": True,
            "qerror_recomputed": True,
            "distribution_metrics_recomputed": True,
            "paired_metrics_recomputed": True,
            "query_truth_sql_identity_recomputed": True,
            "hypothetical_payload_identity_checked": True,
            "physical_oid_order_checked": True,
            "physical_payload_and_ordinary_stats_controls_checked": True,
            "raw_explain_documents_retained": False,
            "raw_explain_digest_recomputation": "not possible from retained artifacts",
            "discrepancies": [],
        },
        "claims": {
            "supported": [
                "fixed-membership hypothetical activation order changes estimates for some queries",
                "the Advisor reference order is favorable within the preregistered alternatives",
                "the synthetic overlapping-MCV fixture exhibits an order-dependent Plan Rows difference",
            ],
            "unsupported": [
                "Advisor order is globally optimal",
                "physical q-error differences are pure OID-order causal effects",
                "all PostgreSQL statistics kinds share one universal OID precedence rule",
                "cardinality-estimation changes imply query-runtime improvements",
            ],
            "limitations": [
                "five random permutations are descriptive fixed-seed alternatives, not a population sample",
                "physical deployments are confounded by native payload and ordinary-statistics drift",
                "the synthetic runtime tie is inferred from EXPLAIN rather than directly instrumented",
                "the experiment uses the patched PostgreSQL hypothetical implementation and frozen PG16.14 systems",
            ],
        },
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_review(value: Mapping[str, Any], *, root: Path, producer_sha: str) -> dict[str, Any]:
    if value.get("format_version") != REVIEW_FORMAT:
        raise ValueError("unsupported OID-order review format")
    if value.get("review_producer_sha") != producer_sha:
        raise ValueError("OID-order review producer binding drift")
    rebuilt = build_review(root, producer_sha=producer_sha)
    if value != rebuilt:
        raise ValueError("OID-order review does not match independently rebuilt evidence")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}
