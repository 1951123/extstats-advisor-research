"""Offline inventory of already-tracked RQ5 cost evidence.

This module is intentionally evidence-only.  It reads immutable JSON artifacts,
projects measurements that are explicitly present there, and never imports the
Advisor bridge or opens PostgreSQL.  Missing values are represented as ``None``
and are never inferred by subtraction or by combining incomparable runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, reject_credentials, semantic_digest, write_json

FORMAT_VERSION = "rq5-existing-trace-cost-inventory-v1"
EXPERIMENT_ID = "rq5-existing-trace-cost-inventory"
RQ5_STATUS = "planned"
DATASETS = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
STAGES = (
    "snapshot capture",
    "authoritative external truth import/validation",
    "production exact truth acquisition",
    "sample/native statistics construction",
    "candidate profiling",
    "planner search",
    "deployment DDL",
    "deployment ANALYZE",
    "catalog/storage",
    "post-deployment planning",
    "refresh",
)
STAGE_STATUSES = {
    "formal-measured",
    "component-measured",
    "historical-only",
    "evidence-exists-timing-missing",
    "missing",
}

RQ2_CHILDREN = {
    "arecel-census13": {
        "path": "experiments/arecel-census13/rq2-confirmatory/rq2-transfer-v1.json",
        "semantic_digest": "a68f96e77eab1bdc0884fe78ec5bde77e026aba3cdc7830cbc70d76b7b512790",
    },
    "arecel-forest10": {
        "path": "experiments/arecel-forest10/rq2-confirmatory/rq2-transfer-v1.json",
        "semantic_digest": "2961e77149a40d5b1a27c06fe15c8177377a630787234139bd44313eac9651e4",
    },
    "arecel-power7": {
        "path": "experiments/arecel-power7/rq2-confirmatory/rq2-transfer-v1.json",
        "semantic_digest": "8bbb606cf2b6e650896adf9c9c104879b1214b82612a7361cb603480fbedd600",
    },
    "arecel-dmv11": {
        "path": "experiments/arecel-dmv11/rq2-confirmatory/rq2-transfer-v1.json",
        "semantic_digest": "0655c18ae95d1bb8a80c35deb9e4a93dd7b0592fcfcb3721234f9a0e4c02facc",
    },
}
RQ2_SUMMARY = {
    "path": "experiments/rq2-cross-dataset-transfer-summary-v1.json",
    "semantic_digest": "dbf6fe6f734039e6de7040406d24aa05baec4b81d1fec1ecc5280b8998a5dfc7",
}
SINGLETON_SOURCES = {
    "arecel-power7": {
        "path": "experiments/arecel-power7/singleton-equivalence/advisor-singleton-incremental-equivalence-v1.json",
        "artifact_digest": "a735add5b4c5eaff5a9c61964a5f629ba20d3872eb9708d33047eb9693bb712c",
    },
    "arecel-forest10": {
        "path": "experiments/arecel-forest10/singleton-equivalence/advisor-singleton-incremental-historical-equivalence-v2.json",
        "artifact_digest": "c523c8617d2169e1886874d8fd31359320f0c8cec00817030b4afac0f60b0e41",
    },
}
KS_SUMMARY = {
    "path": "experiments/rq4-ks-sensitivity-cross-dataset-summary-v1.json",
    "semantic_digest": "3985c2b17b1af48a8aa9142e1e6cbd376215b1d458e7843a44960e34d6b6f3ed",
}
KS_PROTOCOL_DIGEST = "55c29212eabbd59dcc3539d9b4f538390305431a35181126866c383f1f15faec"
KS_B = 4
KS_WIDTHS = (4, 8, 16, 32, "all")


class RQ5CostInventoryValidationError(ValueError):
    """Raised when tracked RQ5 evidence or an inventory is not fail-closed."""


def default_inventory_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-existing-trace-cost-inventory-v1.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ5CostInventoryValidationError(message)


def _finite_number(value: Any, label: str) -> float | int:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{label} must be a finite number",
    )
    return value


def _without_digest(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != field}


def _read_pinned(root: Path, spec: Mapping[str, str], label: str) -> dict[str, Any]:
    path = root / spec["path"]
    _require(path.is_file(), f"missing pinned {label}: {spec['path']}")
    value = read_json(path)
    field = "artifact_digest" if "artifact_digest" in spec else "semantic_digest"
    computed = semantic_digest(_without_digest(value, field))
    _require(computed == spec[field], f"{label} digest does not match pinned digest")
    if field in value:
        _require(value[field] == spec[field], f"{label} embedded digest drifted")
    return value


def _rq2_values(root: Path) -> dict[str, dict[str, Any]]:
    values: dict[str, dict[str, Any]] = {}
    for dataset_id in DATASETS:
        child = _read_pinned(root, RQ2_CHILDREN[dataset_id], f"RQ2 {dataset_id}")
        _require(
            child.get("format_version") == "rq2-transfer-v1", f"{dataset_id} RQ2 format drifted"
        )
        _require(child.get("execution_status") == "complete", f"{dataset_id} RQ2 is not complete")
        _require(
            child.get("truth_source_kind") == "authoritative-external-exact",
            f"{dataset_id} RQ2 truth source drifted",
        )
        truth = child["source_run"]["truth_capture"]
        sample = child["sample_evaluation"]["runtime"]
        full = child["full_data"]["runtime"]
        canonical = child["stage_timings_seconds"]["canonical_source_run"]
        values[dataset_id] = {
            "child": child,
            "truth_import_seconds": _finite_number(
                truth["observations_and_import_elapsed_seconds"],
                f"{dataset_id} external truth import",
            ),
            "snapshot_capture_seconds": _finite_number(
                truth["snapshot_capture_elapsed_seconds"], f"{dataset_id} snapshot capture"
            ),
            "sample_baseline_seconds": _finite_number(
                sample["baseline"]["elapsed_seconds"], f"{dataset_id} sample baseline"
            ),
            "sample_baseline_calls": _finite_number(
                sample["baseline"]["planner_calls"], f"{dataset_id} sample baseline calls"
            ),
            "sample_final_seconds": _finite_number(
                sample["final"]["elapsed_seconds"], f"{dataset_id} sample final"
            ),
            "sample_final_calls": _finite_number(
                sample["final"]["planner_calls"], f"{dataset_id} sample final calls"
            ),
            "sample_pair_seconds": _finite_number(
                sample["runtime_seconds"], f"{dataset_id} sample pair"
            ),
            "sample_pair_calls": _finite_number(
                sample["planner_query_calls"], f"{dataset_id} sample pair calls"
            ),
            "stock_load_seconds": _finite_number(
                full["stock_load_seconds"], f"{dataset_id} stock load"
            ),
            "p0_seconds": _finite_number(full["p0_explain"], f"{dataset_id} P0 EXPLAIN"),
            "p1_seconds": _finite_number(full["p1_explain"], f"{dataset_id} P1 EXPLAIN"),
            "p2_seconds": _finite_number(
                full["p2_transaction_evaluation_rollback"], f"{dataset_id} P2 evaluation"
            ),
            "restoration_seconds": _finite_number(
                full["restoration_verification"], f"{dataset_id} restoration verification"
            ),
            "deployment_seconds": _finite_number(
                full["deployment_including_final_analyze"], f"{dataset_id} deployment"
            ),
            "live_seconds": _finite_number(
                child["live_experiment_elapsed_seconds"], f"{dataset_id} live experiment"
            ),
            "selected_k": _finite_number(child["actual_selected_k"], f"{dataset_id} selected k"),
            "native_materialization_seconds": _finite_number(
                canonical["native_materialization"], f"{dataset_id} native materialization"
            ),
            "candidate_derivation_seconds": _finite_number(
                canonical["candidate_derivation"], f"{dataset_id} candidate derivation"
            ),
            "singleton_profiling_seconds": _finite_number(
                canonical["singleton_profiling"], f"{dataset_id} singleton profiling"
            ),
            "search_seconds": _finite_number(
                canonical["optimization_search"], f"{dataset_id} optimization search"
            ),
            "physical_statistics": child["full_data"].get("physical_statistics", []),
            "preflight": child.get("preflight", {}),
        }
    return values


def _formal_rows(values: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for dataset_id in DATASETS:
        value = values[dataset_id]
        rows.append(
            {
                "dataset_id": dataset_id,
                "authoritative_truth_import_seconds": value["truth_import_seconds"],
                "sample_baseline_eval_seconds": value["sample_baseline_seconds"],
                "sample_baseline_planner_calls": value["sample_baseline_calls"],
                "sample_final_eval_seconds": value["sample_final_seconds"],
                "sample_final_planner_calls": value["sample_final_calls"],
                "sample_pair_total_seconds": value["sample_pair_seconds"],
                "sample_pair_total_planner_calls": value["sample_pair_calls"],
                "stock_load_seconds": value["stock_load_seconds"],
                "p0_explain_seconds": value["p0_seconds"],
                "p1_explain_seconds": value["p1_seconds"],
                "p2_transaction_eval_rollback_seconds": value["p2_seconds"],
                "deployment_plus_analyze_seconds": value["deployment_seconds"],
                "restoration_verification_seconds": value["restoration_seconds"],
                "live_experiment_elapsed_seconds": value["live_seconds"],
                "selected_k": value["selected_k"],
                "full_workload_planner_evaluation_delta_seconds": value["p1_seconds"]
                - value["p0_seconds"],
                "full_workload_planner_evaluation_ratio": value["p1_seconds"] / value["p0_seconds"],
            }
        )
    return rows


def _stage(
    stage_id: str,
    name: str,
    status: str,
    evidence_class: str,
    paths: list[str],
    digests: list[str],
    datasets: list[str],
    measured: Mapping[str, Any],
    missing: list[str],
    scope: str,
    limitations: list[str],
    eligible: bool,
) -> dict[str, Any]:
    _require(status in STAGE_STATUSES, f"unknown stage status: {status}")
    return {
        "stage_id": stage_id,
        "protocol_stage_name": name,
        "measurement_status": status,
        "evidence_class": evidence_class,
        "source_paths": paths,
        "source_semantic_digests": digests,
        "datasets_covered": datasets,
        "measured_metrics": dict(measured),
        "unmeasured_required_metrics": missing,
        "scope": scope,
        "limitations": limitations,
        "eligible_for_primary_rq5_table": eligible,
    }


def _coverage(stages: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    return {
        stage_name: {
            dataset_id: stages[stage_name]["measurement_status"]
            if dataset_id in stages[stage_name]["datasets_covered"]
            else "missing"
            for dataset_id in DATASETS
        }
        for stage_name in STAGES
    }


def _historical_preflight(values: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for dataset_id in DATASETS:
        for check in values[dataset_id]["preflight"].get("checks", []):
            if check.get("historical_seconds") is not None:
                result.append(
                    {
                        "dataset_id": dataset_id,
                        "stage": check.get("stage"),
                        "historical_seconds": check["historical_seconds"],
                        "evidence_class": "historical-preflight-estimate",
                        "included_in_primary_cost_table": False,
                        "source_path": RQ2_CHILDREN[dataset_id]["path"],
                    }
                )
    return result


def _production_exact_status(root: Path) -> tuple[str, list[str], list[str]]:
    paths = [
        "experiments/arecel-census13/rq1-canary/rq1-matched-comparison-v1.json",
        "experiments/arecel-census13/arecel-truth-equivalence-v1.json",
    ]
    present = [path for path in paths if (root / path).is_file()]
    _require(present, "tracked Census13 production-exact evidence is missing")
    digests = [
        semantic_digest(_without_digest(read_json(root / path), "semantic_digest"))
        for path in present
    ]
    elapsed_found = False
    for path in present:
        value = read_json(root / path)

        def visit(node: Any, exact_context: bool = False) -> None:
            nonlocal elapsed_found
            if isinstance(node, Mapping):
                context = exact_context or node.get("source_kind") == "production-exact-execution"
                for key, item in node.items():
                    if context and "elapsed" in str(key).lower() and isinstance(item, (int, float)):
                        elapsed_found = True
                    visit(item, context)
            elif isinstance(node, list):
                for item in node:
                    visit(item, exact_context)

        visit(value)
    return (
        "formal-measured" if elapsed_found else "evidence-exists-timing-missing",
        present,
        digests,
    )


def _singleton_evidence(root: Path) -> list[dict[str, Any]]:
    rows = []
    for dataset_id, spec in SINGLETON_SOURCES.items():
        value = _read_pinned(root, spec, f"{dataset_id} singleton equivalence")
        reference_key = "reference" if "reference" in value else "historical_oracle"
        reference = value[reference_key]["runtime_metadata"]
        incremental = value["incremental"]["runtime_metadata"]
        row = {
            "dataset_id": dataset_id,
            "evidence_class": "component-microbenchmark / equivalence-validation",
            "source_path": spec["path"],
            "source_artifact_digest": spec["artifact_digest"],
            "candidate_count": incremental.get("singleton_configuration_count"),
            "query_count": incremental.get("baseline_planner_query_estimate_count"),
            "reference_planner_calls": reference.get("planner_query_estimate_count"),
            "incremental_planner_calls": incremental.get("planner_query_estimate_count"),
            "saved_planner_calls": incremental.get("saved_planner_query_estimate_count"),
            "planner_call_reduction": incremental.get("planner_query_reduction_fraction"),
            "reference_wall_seconds": reference.get("profiling_wall_clock_seconds"),
            "incremental_wall_seconds": incremental.get("profiling_wall_clock_seconds"),
            "wall_speedup": (
                reference["profiling_wall_clock_seconds"]
                / incremental["profiling_wall_clock_seconds"]
                if reference.get("profiling_wall_clock_seconds") is not None
                and incremental.get("profiling_wall_clock_seconds") is not None
                else None
            ),
            "canonical_end_to_end_profiling_cost": False,
            "unmeasured_metrics": [],
        }
        for key, label in (
            ("reference_wall_seconds", "reference_wall_clock_seconds"),
            ("wall_speedup", "wall_speedup"),
        ):
            if row[key] is None:
                row["unmeasured_metrics"].append(label)
        rows.append(row)
    return rows


def _search_scaling(root: Path) -> list[dict[str, Any]]:
    summary = _read_pinned(root, KS_SUMMARY, "RQ4 K_s cross-dataset summary")
    rows = []
    for dataset_id, dataset in summary["datasets"].items():
        for point in dataset["points"]:
            width = point["K_s"]
            rows.append(
                {
                    "dataset_id": dataset_id,
                    "K_s": width,
                    "B": KS_B,
                    "selected_k": point["selected_k"],
                    "termination": point["termination_reason"],
                    "objective": point["final_objective"],
                    "planner_calls": point["incremental_planner_calls"],
                    "selection_wall_seconds": point["selection_wall_clock_seconds"],
                    "proposal_configuration_evaluations": point[
                        "proposal_configuration_objective_evaluations"
                    ],
                    "all_reference_semantics": dataset["all_reference_semantics"],
                    "evidence_class": "formal-search-sensitivity-B4",
                    "canonical_production_cost_measurement": False,
                }
            )
    return rows


def _build_inventory_without_digest(research_root: Path) -> dict[str, Any]:
    paper_spec = read_json(research_root / "paper/paper-experiment-v1.json")
    rq5_specs = [
        item
        for item in paper_spec.get("experiments", [])
        if item.get("experiment_id") == "rq5-cost-accounting"
    ]
    _require(len(rq5_specs) == 1, "paper specification has no unique RQ5 cost-accounting entry")
    _require(rq5_specs[0].get("cost_stages") == list(STAGES), "RQ5 stage taxonomy drifted")
    _require(rq5_specs[0].get("status") == RQ5_STATUS, "RQ5 registry status drifted")
    values = _rq2_values(research_root)
    _read_pinned(research_root, RQ2_SUMMARY, "RQ2 cross-dataset summary")
    exact_status, exact_paths, exact_digests = _production_exact_status(research_root)
    singleton_rows = _singleton_evidence(research_root)
    search_rows = _search_scaling(research_root)
    child_paths = [RQ2_CHILDREN[dataset_id]["path"] for dataset_id in DATASETS]
    child_digests = [RQ2_CHILDREN[dataset_id]["semantic_digest"] for dataset_id in DATASETS]
    all_child_digests = child_digests

    stage_map = {
        "snapshot capture": _stage(
            "snapshot-capture",
            "snapshot capture",
            "formal-measured",
            "RQ2 formal stage timing",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "snapshot_capture_elapsed_seconds": {
                    d: values[d]["snapshot_capture_seconds"] for d in DATASETS
                }
            },
            ["snapshot_bytes"],
            "RQ2 canonical source run snapshot capture elapsed time",
            ["sample_rows=10000 is a protocol quantity, not a measured snapshot size"],
            True,
        ),
        "authoritative external truth import/validation": _stage(
            "authoritative-external-truth-import",
            "authoritative external truth import/validation",
            "formal-measured",
            "RQ2 formal truth binding",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "observations_and_import_elapsed_seconds": {
                    d: values[d]["truth_import_seconds"] for d in DATASETS
                }
            },
            [],
            "external observation import plus validation/binding",
            ["This is not production exact truth acquisition"],
            True,
        ),
        "production exact truth acquisition": _stage(
            "production-exact-truth-acquisition",
            "production exact truth acquisition",
            exact_status,
            "historical production-exact truth/equivalence evidence",
            exact_paths,
            exact_digests,
            ["arecel-census13"],
            {},
            ["exact_count_elapsed_seconds", "exact_count_planner_or_executor_calls"],
            "Census13 production-exact truth existed and was used for equivalence auditing",
            ["No exact-count timing is inferred from an RQ1 total or external-label import"],
            False,
        ),
        "sample/native statistics construction": _stage(
            "sample-native-statistics-construction",
            "sample/native statistics construction",
            "formal-measured",
            "RQ2 formal stage timing",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "native_materialization_seconds": {
                    d: values[d]["native_materialization_seconds"] for d in DATASETS
                },
                "candidate_derivation_seconds": {
                    d: values[d]["candidate_derivation_seconds"] for d in DATASETS
                },
            },
            ["sample_native_payload_bytes", "sample_construction_bytes"],
            "sample-side native materialization and candidate derivation",
            ["sample payload bytes are not deployed catalog/storage bytes"],
            True,
        ),
        "candidate profiling": _stage(
            "candidate-profiling",
            "candidate profiling",
            "formal-measured",
            "RQ2 formal stage timing",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "singleton_profiling_seconds": {
                    d: values[d]["singleton_profiling_seconds"] for d in DATASETS
                }
            },
            [],
            "canonical RQ2 source-run singleton profiling",
            [
                "Separate singleton equivalence artifacts are component microbenchmarks, not canonical end-to-end cost"
            ],
            True,
        ),
        "planner search": _stage(
            "planner-search",
            "planner search",
            "formal-measured",
            "RQ2 formal stage timing",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {"optimization_search_seconds": {d: values[d]["search_seconds"] for d in DATASETS}},
            [],
            "canonical RQ2 B=8/K_s=8 source-run search",
            [
                "The separate K_s scaling evidence below uses B=4 and is not canonical production cost"
            ],
            True,
        ),
        "deployment DDL": _stage(
            "deployment-ddl",
            "deployment DDL",
            "evidence-exists-timing-missing",
            "RQ2 combined deployment trace",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {},
            ["DDL_only_seconds"],
            "DDL is included in deployment_including_final_analyze",
            ["No arbitrary decomposition of the combined wall time"],
            False,
        ),
        "deployment ANALYZE": _stage(
            "deployment-analyze",
            "deployment ANALYZE",
            "evidence-exists-timing-missing",
            "RQ2 combined deployment trace",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {},
            ["ANALYZE_only_seconds"],
            "ANALYZE is included in deployment_including_final_analyze",
            ["No arbitrary decomposition of the combined wall time"],
            False,
        ),
        "catalog/storage": _stage(
            "catalog-storage",
            "catalog/storage",
            "evidence-exists-timing-missing",
            "RQ2 deployment structural verification",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "physical_statistics_object_count": {
                    d: len(values[d]["physical_statistics"]) for d in DATASETS
                },
                "payload_present_object_count": {
                    d: sum(
                        1
                        for item in values[d]["physical_statistics"]
                        if item.get("payload") is True
                    )
                    for d in DATASETS
                },
            },
            ["catalog_bytes", "storage_bytes"],
            "Tracked deployment artifacts expose object/kind/target/payload metadata",
            ["OID, object count, and nonempty payload do not measure catalog/storage bytes"],
            False,
        ),
        "post-deployment planning": _stage(
            "post-deployment-planning",
            "post-deployment planning",
            "formal-measured",
            "RQ2 full-data EXPLAIN trace",
            child_paths,
            all_child_digests,
            list(DATASETS),
            {
                "p0_explain_seconds": {d: values[d]["p0_seconds"] for d in DATASETS},
                "p1_explain_seconds": {d: values[d]["p1_seconds"] for d in DATASETS},
                "p2_transaction_evaluation_rollback_seconds": {
                    d: values[d]["p2_seconds"] for d in DATASETS
                },
            },
            ["production_online_query_latency"],
            "offline 10,000-query PostgreSQL planner/EXPLAIN workload",
            ["Not a production online latency benchmark"],
            True,
        ),
        "refresh": _stage(
            "refresh",
            "refresh",
            "missing",
            "none",
            [],
            [],
            [],
            {},
            ["refresh_elapsed_seconds", "refresh_storage_change", "refresh_quality_change"],
            "No tracked refresh experiment",
            ["Initial deployment plus ANALYZE is not a refresh experiment"],
            False,
        ),
    }

    gaps = [
        {
            "stage": "snapshot capture",
            "missing_metric": "snapshot_bytes",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "RQ2 records elapsed capture time but no tracked snapshot byte measurement",
            "would_require_new_live_measurement": True,
            "priority": "appendix-useful",
        },
        {
            "stage": "production exact truth acquisition",
            "missing_metric": "exact_count_elapsed_seconds",
            "datasets": ["arecel-census13"],
            "why_existing_trace_is_insufficient": "The tracked equivalence evidence proves labels existed and matched, but records no exact-count timing",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "deployment DDL",
            "missing_metric": "DDL_only_seconds",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "Only combined deployment plus final ANALYZE wall time is measured",
            "would_require_new_live_measurement": True,
            "priority": "appendix-useful",
        },
        {
            "stage": "deployment ANALYZE",
            "missing_metric": "ANALYZE_only_seconds",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "Only combined deployment plus final ANALYZE wall time is measured",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "catalog/storage",
            "missing_metric": "catalog_bytes and storage_bytes",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "Object metadata and payload presence are not byte measurements",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "post-deployment planning",
            "missing_metric": "production_online_query_latency",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "P0/P1 are offline 10,000-query EXPLAIN wall times, not online latency",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "refresh",
            "missing_metric": "refresh cost and quality trend",
            "datasets": list(DATASETS),
            "why_existing_trace_is_insufficient": "No tracked refresh experiment exists",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
    ]
    coverage = _coverage(stage_map)
    return {
        "format_version": FORMAT_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "status": "existing-trace-inventory-complete",
        "rq5_completion_status": "incomplete",
        "rq5_registry_status": RQ5_STATUS,
        "generated_from_tracked_json_only": True,
        "new_postgresql_execution": False,
        "new_planner_execution": False,
        "new_benchmark_run": False,
        "no_synthetic_total": True,
        "stage_taxonomy": list(STAGES),
        "source_evidence": {
            "rq2_children": [{"dataset_id": d, **RQ2_CHILDREN[d]} for d in DATASETS],
            "rq2_cross_dataset_summary": dict(RQ2_SUMMARY),
            "singleton_equivalence": [
                {"dataset_id": d, **SINGLETON_SOURCES[d]} for d in SINGLETON_SOURCES
            ],
            "rq4_ks_cross_dataset_summary": dict(KS_SUMMARY),
            "top_k_protocol_semantic_digest": KS_PROTOCOL_DIGEST,
        },
        "formal_operational_costs": _formal_rows(values),
        "historical_preflight_timings": _historical_preflight(values),
        "candidate_profiling_component_evidence": singleton_rows,
        "search_scaling_evidence": search_rows,
        "stages": stage_map,
        "coverage_matrix": coverage,
        "remaining_measurement_gaps": gaps,
        "claim_readiness": {
            "can_claim_external_truth_import_cost": True,
            "can_claim_combined_deployment_analyze_cost": True,
            "can_claim_search_scaling_cost": True,
            "can_claim_incremental_singleton_savings": True,
            "can_claim_end_to_end_advisor_cost": False,
            "can_claim_refresh_cost": False,
            "can_claim_catalog_storage_cost": False,
            "can_claim_production_exact_truth_cost": False,
        },
        "interpretation": {
            "external_truth_import_is_not_production_exact_cost": True,
            "deployment_including_final_analyze_is_combined": True,
            "p0_p1_are_offline_explain_workload_timings": True,
            "ks_sensitivity_is_B4_not_canonical_B8": True,
            "canonical_production_configuration": {"K_s": 8, "B": 8},
            "component_timings_must_not_be_summed": True,
        },
    }


def build_inventory(research_root: Path) -> dict[str, Any]:
    """Build the deterministic inventory from pinned tracked JSON evidence."""
    body = _build_inventory_without_digest(research_root)
    body["semantic_digest"] = semantic_digest(body)
    return body


def write_inventory(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    output_path = output or default_inventory_path(research_root)
    value = build_inventory(research_root)
    write_json(output_path, value)
    return value


def validate_inventory(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(value.get("format_version") == FORMAT_VERSION, "unsupported RQ5 inventory format")
    digest = value.get("semantic_digest")
    _require(isinstance(digest, str), "RQ5 inventory lacks semantic_digest")
    _require(
        digest == semantic_digest(_without_digest(value, "semantic_digest")),
        "RQ5 inventory digest mismatch",
    )
    _require("total_advisor_seconds" not in value, "synthetic end-to-end total is forbidden")
    _require(value.get("rq5_completion_status") == "incomplete", "RQ5 must remain incomplete")
    _require(
        value.get("rq5_registry_status") == RQ5_STATUS, "RQ5 registry status must remain planned"
    )
    _require(
        value.get("new_postgresql_execution") is False, "inventory claims new PostgreSQL execution"
    )
    _require(value.get("new_planner_execution") is False, "inventory claims new planner execution")
    _require(value.get("no_synthetic_total") is True, "inventory must forbid synthetic totals")
    expected = build_inventory(research_root)
    _require(value == expected, "RQ5 inventory does not match immutable source-derived evidence")
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "semantic_digest": digest,
        "rq5_completion_status": value["rq5_completion_status"],
        "dataset_count": len(value["formal_operational_costs"]),
        "remaining_measurement_gap_count": len(value["remaining_measurement_gaps"]),
    }
