"""Offline inventory of already-tracked RQ5 cost evidence.

This module is intentionally evidence-only.  It reads immutable JSON artifacts,
projects measurements that are explicitly present there, and never imports the
Advisor bridge or opens PostgreSQL.  Missing values are represented as ``None``
and are never inferred by subtraction or by combining incomparable runs.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from statistics import median
from typing import Any

from .provenance import read_json, reject_credentials, semantic_digest, write_json

FORMAT_VERSION = "rq5-existing-trace-cost-inventory-v1"
FORMAT_VERSION_V2 = "rq5-existing-trace-cost-inventory-v2"
FORMAT_VERSION_V3 = "rq5-existing-trace-cost-inventory-v3"
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
V1_INVENTORY = {
    "path": "experiments/rq5-existing-trace-cost-inventory-v1.json",
    "semantic_digest": "54f846c0c50e8db787b87b0ad5caa50a2e180e7983ea963a9ee4d823cb0e5eab",
}
STATIC_DEPLOYMENT_SUMMARY = {
    "path": "experiments/rq5-static-deployment-cost-v1.json",
    "semantic_digest": "deb3ce1ae43a66fb79c949aca08ffb34a96bb421f789463dbf4a1a0d804c11d0",
    "producer_sha": "70fa08ded50bb49ec3f19a4c57a3f9895e70f822",
    "preflight_path": "experiments/rq5-static-deployment-cost-preflight-v1.json",
    "preflight_semantic_digest": "cbaf04a9a3958ba4c74f70e5a90ffbca725f82cb54a5d572849591b6b5007224",
}
STATIC_DEPLOYMENT_RAW = {
    "arecel-census13": {
        "path": "experiments/rq5-static-deployment-cost-v1/raw/arecel-census13.json",
        "semantic_digest": "e24ca4280ad6d53e276d19527a86d26de2216a6c5533171807cac75f82cb316a",
    },
    "arecel-forest10": {
        "path": "experiments/rq5-static-deployment-cost-v1/raw/arecel-forest10.json",
        "semantic_digest": "a010aa37a0443a0fe7029b3d5e8334f26c5d5b327d9f3403ea09f34d4b0d006a",
    },
    "arecel-power7": {
        "path": "experiments/rq5-static-deployment-cost-v1/raw/arecel-power7.json",
        "semantic_digest": "6f8b1b8928d3619392df13993e86f93c9bc4b04deb2b80026e5d0163d0928409",
    },
    "arecel-dmv11": {
        "path": "experiments/rq5-static-deployment-cost-v1/raw/arecel-dmv11.json",
        "semantic_digest": "434e3fd98fec7063f87ae0fa6ce07d571d9cfcfc3b8d55120b9588dd9a93d847",
    },
}
STATIC_DEPLOYMENT_RQ2_SOURCES = {
    "arecel-census13": {
        "path": RQ2_CHILDREN["arecel-census13"]["path"],
        "semantic_digest": RQ2_CHILDREN["arecel-census13"]["semantic_digest"],
        "deployment_path": "experiments/arecel-census13/rq2-confirmatory/deployment-result-v1.json",
        "deployment_digest": "51c6ab5eaace99629b8c51662633cd8d58f4798c4cd3914e13b03755319a79bf",
    },
    "arecel-forest10": {
        "path": RQ2_CHILDREN["arecel-forest10"]["path"],
        "semantic_digest": RQ2_CHILDREN["arecel-forest10"]["semantic_digest"],
        "deployment_path": "experiments/arecel-forest10/rq2-confirmatory/deployment-result-v1.json",
        "deployment_digest": "0d3eea2ea72e8d4283203fbe397f2d0b203f67f696d1182061f88278df7269e9",
    },
    "arecel-power7": {
        "path": RQ2_CHILDREN["arecel-power7"]["path"],
        "semantic_digest": RQ2_CHILDREN["arecel-power7"]["semantic_digest"],
        "deployment_path": "experiments/arecel-power7/rq2-confirmatory/deployment-result-v1.json",
        "deployment_digest": "1574371282ef4427569ac1159777433ac7874a64a5e8fc9eb9477ca2d0925fa7",
    },
    "arecel-dmv11": {
        "path": RQ2_CHILDREN["arecel-dmv11"]["path"],
        "semantic_digest": RQ2_CHILDREN["arecel-dmv11"]["semantic_digest"],
        "deployment_path": "experiments/arecel-dmv11/rq2-confirmatory/deployment-result-v1.json",
        "deployment_digest": "ab46e20c8a51b6e2e35ce7f41d3d1224d3af91dcaeee4aa5595d8d25c3d0734a",
    },
}
STATIC_PROTOCOL_DIGEST = "01475d7979fb9c4bf027fbe43abd57c9cd06541134533712f3f258b47cc0f406"
V2_INVENTORY = {
    "path": "experiments/rq5-existing-trace-cost-inventory-v2.json",
    "semantic_digest": "de6d4f3291735dfdf03007adfc92678cfc31f11c8ef3f813b25b3b0ac271230b",
}
SNAPSHOT_PROTOCOL_DIGEST = "e339986dc8960b4632da504d563de9d32a8e08e22762c54033d41a5ce3eb12c9"
SNAPSHOT_FORMAL = {
    "path": "experiments/rq5-snapshot-footprint-v1.json",
    "semantic_digest": "1b8a4279f668d9c5468bf68748d14ebe0b3f702b2aa2142a1a99a37ada23e4e4",
    "producer_sha": "a5e0577451df833a39698fbadd219ed404baa7a7",
    "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
    "preflight_semantic_digest": "ed300226bfcff3c2804500b526e1a3d9b36c817e1b2972f99c8ce96242f8759a",
}
SNAPSHOT_RAW = {
    "arecel-census13": {
        "path": "experiments/rq5-snapshot-footprint-v1/raw/arecel-census13.json",
        "semantic_digest": "13d915ef82bf6fa077dce46b86e7bc4d34c66e095b39712f0ca48b65813bd2d5",
    },
    "arecel-forest10": {
        "path": "experiments/rq5-snapshot-footprint-v1/raw/arecel-forest10.json",
        "semantic_digest": "20f818a9c102228c2cfaeeeae29f190e92023d86572d5bd54ae153f517fe188d",
    },
    "arecel-power7": {
        "path": "experiments/rq5-snapshot-footprint-v1/raw/arecel-power7.json",
        "semantic_digest": "f1a649a71546a6a2b18d4141b703d403f102e68a56a594c983349dc8ddf2b553",
    },
    "arecel-dmv11": {
        "path": "experiments/rq5-snapshot-footprint-v1/raw/arecel-dmv11.json",
        "semantic_digest": "b38a176b394a7ea99add341e3c49c2b1d7c0fabb56918af7b81bee0bd6b0e817",
    },
}
SNAPSHOT_SIZE_DEFINITION = (
    "sealed_snapshot_logical_bytes is the sum of st_size of all regular files "
    "recursively inside a validated sealed advisor-snapshot-v1 directory"
)


class RQ5CostInventoryValidationError(ValueError):
    """Raised when tracked RQ5 evidence or an inventory is not fail-closed."""


def default_inventory_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-existing-trace-cost-inventory-v1.json"


def default_inventory_v2_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-existing-trace-cost-inventory-v2.json"


def default_inventory_v3_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-existing-trace-cost-inventory-v3.json"


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
    if value.get("format_version") == FORMAT_VERSION_V3:
        return validate_inventory_v3(path, research_root)
    if value.get("format_version") == FORMAT_VERSION_V2:
        return validate_inventory_v2(path, research_root)
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


def _stats(values: list[float | int]) -> dict[str, float | int]:
    _require(values, "cannot summarize an empty measurement list")
    return {"median": median(values), "min": min(values), "max": max(values)}


def _static_protocol(root: Path) -> dict[str, Any]:
    path = root / "paper/rq5-static-deployment-cost-protocol-v1.json"
    _require(path.is_file(), "static deployment protocol is missing")
    value = read_json(path)
    _require(
        value.get("format_version") == "rq5-static-deployment-cost-protocol-v1",
        "static deployment protocol format drifted",
    )
    _require(
        value.get("status") == "preregistered", "static deployment protocol is not preregistered"
    )
    _require(
        value.get("semantic_digest") == STATIC_PROTOCOL_DIGEST,
        "static deployment protocol digest drifted",
    )
    _require(
        semantic_digest(_without_digest(value, "semantic_digest")) == STATIC_PROTOCOL_DIGEST,
        "static deployment protocol semantic digest mismatch",
    )
    return value


def _validate_static_summary(
    root: Path, summary: Mapping[str, Any], raw_by_dataset: Mapping[str, Mapping[str, Any]]
) -> None:
    _require(
        summary.get("format_version") == "rq5-static-deployment-cost-v1",
        "static deployment summary format drifted",
    )
    _require(summary.get("status") == "complete", "static deployment summary is not complete")
    _require(
        summary.get("static_deployment_cost_subexperiment") is True,
        "static deployment flag drifted",
    )
    _require(
        summary.get("rq5_completion_status") == "incomplete",
        "static summary completed RQ5 unexpectedly",
    )
    _require(
        summary.get("rq5_registry_status") == RQ5_STATUS, "static summary registry status drifted"
    )
    _require(
        summary.get("research_commit_sha") == STATIC_DEPLOYMENT_SUMMARY["producer_sha"],
        "static deployment producer drifted",
    )
    _require(
        summary.get("stock_postgresql_sha") == "0d1c00c624fa7367d4a895f44381887757289682",
        "static deployment stock identity drifted",
    )
    _require(
        summary.get("stock_postgresql_version") == "16.14",
        "static deployment stock version drifted",
    )
    _require(
        summary.get("protocol_semantic_digest") == STATIC_PROTOCOL_DIGEST,
        "static summary protocol digest drifted",
    )
    _require(summary.get("dataset_order") == list(DATASETS), "static summary dataset order drifted")
    refs = summary.get("raw_repetition_artifacts")
    expected_refs = [{"dataset_id": d, **STATIC_DEPLOYMENT_RAW[d]} for d in DATASETS]
    _require(refs == expected_refs, "static summary raw child references drifted")
    _require(summary.get("no_advisor_selection") is True, "static summary claims Advisor selection")
    _require(summary.get("no_patched_postgres") is True, "static summary claims patched PostgreSQL")
    _require(
        summary.get("no_planner_evaluation") is True, "static summary claims planner evaluation"
    )
    _require(summary.get("no_truth_acquisition") is True, "static summary claims truth acquisition")
    _require(
        summary.get("no_snapshot_bytes_measurement") is True,
        "static summary claims snapshot-byte measurement",
    )
    _require(
        summary.get("preflight_path") == STATIC_DEPLOYMENT_SUMMARY["preflight_path"],
        "static summary preflight path drifted",
    )
    _require(
        summary.get("preflight_semantic_digest")
        == STATIC_DEPLOYMENT_SUMMARY["preflight_semantic_digest"],
        "static summary preflight digest drifted",
    )
    _require(
        summary.get("protocol_path") == "paper/rq5-static-deployment-cost-protocol-v1.json",
        "static summary protocol path drifted",
    )
    for dataset_id in DATASETS:
        raw = raw_by_dataset[dataset_id]
        dataset = summary.get("datasets", {}).get(dataset_id)
        _require(isinstance(dataset, Mapping), f"static summary lacks {dataset_id}")
        repetitions = raw["repetitions"]
        ddl = [rep["ddl"]["elapsed_seconds"] for rep in repetitions]
        analyze = [rep["analyze"]["elapsed_seconds"] for rep in repetitions]
        sequential = [rep["derived"]["sequential_ddl_plus_analyze_seconds"] for rep in repetitions]
        after_ddl = [rep["logical_storage"]["after_ddl"]["total"] for rep in repetitions]
        after_analyze = [rep["logical_storage"]["after_analyze"]["total"] for rep in repetitions]
        physical_ddl = [
            rep["physical_catalog_allocation"]["deltas"]["after_ddl_minus_baseline"][
                "combined_catalog_total_relation_bytes"
            ]
            for rep in repetitions
        ]
        physical_final = [
            rep["physical_catalog_allocation"]["deltas"]["after_analyze_minus_baseline"][
                "combined_catalog_total_relation_bytes"
            ]
            for rep in repetitions
        ]
        expected = {
            "selected_k": raw["object_count"],
            "selected_candidate_ids": raw["selected_candidate_ids"],
            "object_count": raw["object_count"],
            "statistics_kinds": raw["statistics_kinds"],
            "statistics_target": raw["statistics_target"],
            "ddl_elapsed_seconds": _stats(ddl),
            "analyze_elapsed_seconds": _stats(analyze),
            "sequential_ddl_plus_analyze_seconds": _stats(sequential),
            "logical_catalog_row_bytes_after_ddl": _stats(after_ddl),
            "logical_catalog_row_bytes_after_analyze": _stats(after_analyze),
            "physical_catalog_allocation_delta_after_ddl": _stats(physical_ddl),
            "physical_catalog_allocation_delta_after_analyze": _stats(physical_final),
        }
        _require(
            all(dataset.get(key) == value for key, value in expected.items()),
            f"static summary projection drifted for {dataset_id}",
        )
        _require(dataset.get("cleanup") is True, f"static summary cleanup drifted for {dataset_id}")
        _require(
            dataset.get("payload_verification") is True,
            f"static summary payload verification drifted for {dataset_id}",
        )


def _read_static_deployment_sources(
    root: Path,
) -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]
]:
    v1 = _read_pinned(root, V1_INVENTORY, "RQ5 inventory v1")
    _require(v1.get("format_version") == FORMAT_VERSION, "pinned RQ5 inventory is not v1")
    _require(v1.get("rq5_completion_status") == "incomplete", "pinned RQ5 inventory completed RQ5")
    summary = _read_pinned(root, STATIC_DEPLOYMENT_SUMMARY, "RQ5 static deployment summary")
    protocol = _static_protocol(root)
    preflight = _read_pinned(
        root,
        {
            "path": STATIC_DEPLOYMENT_SUMMARY["preflight_path"],
            "semantic_digest": STATIC_DEPLOYMENT_SUMMARY["preflight_semantic_digest"],
        },
        "RQ5 static deployment preflight",
    )
    _require(
        preflight.get("status") == "ready-to-run", "static deployment preflight is not ready-to-run"
    )
    _require(
        preflight.get("formal_execution_started") is False,
        "static deployment preflight claims execution started",
    )
    _require(
        preflight.get("research_commit_sha") == STATIC_DEPLOYMENT_SUMMARY["producer_sha"],
        "preflight producer drifted",
    )
    _require(
        preflight.get("stock_postgres_sha") == "0d1c00c624fa7367d4a895f44381887757289682",
        "preflight stock identity drifted",
    )
    _require(preflight.get("repetitions") == 3, "preflight repetition count drifted")
    _require(preflight.get("dataset_order") == list(DATASETS), "preflight dataset order drifted")

    raw_by_dataset: dict[str, dict[str, Any]] = {}
    for dataset_id in DATASETS:
        raw = _read_pinned(
            root, STATIC_DEPLOYMENT_RAW[dataset_id], f"static deployment raw {dataset_id}"
        )
        raw_by_dataset[dataset_id] = raw
        source = STATIC_DEPLOYMENT_RQ2_SOURCES[dataset_id]
        _require(
            raw.get("format_version") == "rq5-static-deployment-cost-dataset-v1",
            f"{dataset_id} raw format drifted",
        )
        _require(
            raw.get("status") == "complete" and raw.get("formal_execution") is True,
            f"{dataset_id} raw is not complete",
        )
        _require(raw.get("dataset_id") == dataset_id, f"{dataset_id} raw dataset identity drifted")
        _require(
            raw.get("research_commit_sha") == STATIC_DEPLOYMENT_SUMMARY["producer_sha"],
            f"{dataset_id} raw producer drifted",
        )
        _require(
            raw.get("stock_postgresql_sha") == "0d1c00c624fa7367d4a895f44381887757289682",
            f"{dataset_id} raw stock identity drifted",
        )
        _require(
            raw.get("protocol_semantic_digest") == STATIC_PROTOCOL_DIGEST,
            f"{dataset_id} raw protocol digest drifted",
        )
        _require(
            raw.get("preflight_path") == STATIC_DEPLOYMENT_SUMMARY["preflight_path"],
            f"{dataset_id} raw preflight path drifted",
        )
        _require(
            raw.get("preflight_semantic_digest")
            == STATIC_DEPLOYMENT_SUMMARY["preflight_semantic_digest"],
            f"{dataset_id} raw preflight digest drifted",
        )
        _require(
            raw.get("source_rq2_child") == source["path"], f"{dataset_id} raw RQ2 path drifted"
        )
        _require(
            raw.get("source_rq2_digest") == source["semantic_digest"],
            f"{dataset_id} raw RQ2 digest drifted",
        )
        _require(
            raw.get("source_deployment_artifact") == source["deployment_path"],
            f"{dataset_id} raw deployment path drifted",
        )
        _require(
            raw.get("source_deployment_digest") == source["deployment_digest"],
            f"{dataset_id} raw deployment digest drifted",
        )
        _require(
            isinstance(raw.get("selected_candidate_ids"), list),
            f"{dataset_id} raw candidates missing",
        )
        _require(
            raw.get("object_count") == len(raw["selected_candidate_ids"]),
            f"{dataset_id} raw object count drifted",
        )
        _require(raw.get("statistics_target") == 100, f"{dataset_id} raw statistics target drifted")
        _require(len(raw.get("repetitions", [])) == 3, f"{dataset_id} raw repetition count drifted")
        for expected_id, rep in enumerate(raw["repetitions"], 1):
            _require(
                rep.get("repetition_id") == expected_id, f"{dataset_id} repetition order drifted"
            )
            for key in (
                "research_commit_sha",
                "source_rq2_child",
                "source_rq2_digest",
                "source_deployment_artifact",
                "source_deployment_digest",
            ):
                _require(
                    rep.get(key) == raw.get(key),
                    f"{dataset_id} repetition provenance drifted: {key}",
                )
            _require(rep.get("status") == "complete", f"{dataset_id} repetition is not complete")
            _require(
                rep.get("ddl", {}).get("committed") is True, f"{dataset_id} DDL was not committed"
            )
            _require(
                rep.get("ddl", {}).get("verification_passed") is True,
                f"{dataset_id} DDL verification failed",
            )
            _require(
                rep.get("analyze", {}).get("completed") is True,
                f"{dataset_id} ANALYZE did not complete",
            )
            _require(
                rep.get("analyze", {}).get("payload_verification_passed") is True,
                f"{dataset_id} payload verification failed",
            )
            _require(
                rep.get("derived", {}).get("direct_combined_wall_clock_measurement") is False,
                f"{dataset_id} combined timing semantics drifted",
            )
            _require(
                rep.get("cleanup", {}).get("database_dropped") is True,
                f"{dataset_id} cleanup failed",
            )
            _require(rep["ddl"]["elapsed_seconds"] <= 300, f"{dataset_id} DDL cap exceeded")
            _require(rep["analyze"]["elapsed_seconds"] <= 300, f"{dataset_id} ANALYZE cap exceeded")
            baseline = rep["logical_storage"]["baseline"]
            _require(
                all(
                    baseline.get(key) == 0
                    for key in (
                        "pg_statistic_ext",
                        "pg_statistic_ext_data",
                        "total",
                        "mcv_payload_bytes",
                        "dependencies_payload_bytes",
                        "ndistinct_payload_bytes",
                    )
                ),
                f"{dataset_id} baseline logical storage is not zero",
            )
            after_ddl = rep["logical_storage"]["after_ddl"]
            _require(
                all(
                    after_ddl.get(key) == 0
                    for key in (
                        "mcv_payload_bytes",
                        "dependencies_payload_bytes",
                        "ndistinct_payload_bytes",
                    )
                ),
                f"{dataset_id} pre-ANALYZE payload is present",
            )
    _validate_static_summary(root, summary, raw_by_dataset)
    reject_credentials(
        {
            "v1": v1,
            "summary": summary,
            "preflight": preflight,
            "raw": raw_by_dataset,
            "protocol": protocol,
        }
    )
    return v1, summary, preflight, raw_by_dataset, protocol


def _static_projection(
    raw_by_dataset: Mapping[str, Mapping[str, Any]], protocol: Mapping[str, Any]
) -> dict[str, Any]:
    logical_keys = (
        "pg_statistic_ext",
        "pg_statistic_ext_data",
        "total",
        "mcv_payload_bytes",
        "dependencies_payload_bytes",
        "ndistinct_payload_bytes",
    )
    physical_keys = (
        "metadata_catalog_total_relation_bytes",
        "data_catalog_total_relation_bytes",
        "combined_catalog_total_relation_bytes",
    )
    per_dataset: dict[str, Any] = {}
    headline_rows: list[dict[str, Any]] = []
    fractions: dict[str, Any] = {}
    for dataset_id in DATASETS:
        raw = raw_by_dataset[dataset_id]
        reps = raw["repetitions"]
        ddl = [rep["ddl"]["elapsed_seconds"] for rep in reps]
        analyze = [rep["analyze"]["elapsed_seconds"] for rep in reps]
        sequential = [rep["derived"]["sequential_ddl_plus_analyze_seconds"] for rep in reps]
        stats = lambda values: _stats(values)
        logical = {
            state: {
                key: [rep["logical_storage"][state][key] for rep in reps] for key in logical_keys
            }
            for state in ("after_ddl", "after_analyze")
        }
        physical_deltas = {
            delta: {
                key: [rep["physical_catalog_allocation"]["deltas"][delta][key] for rep in reps]
                for key in physical_keys
            }
            for delta in (
                "after_ddl_minus_baseline",
                "after_analyze_minus_after_ddl",
                "after_analyze_minus_baseline",
            )
        }
        per_dataset[dataset_id] = {
            "selected_k": raw["object_count"],
            "selected_candidate_ids": raw["selected_candidate_ids"],
            "object_count": raw["object_count"],
            "statistics_kinds": raw["statistics_kinds"],
            "statistics_target": raw["statistics_target"],
            "ddl": {
                "raw_seconds": ddl,
                "statistics_seconds": stats(ddl),
                "raw_statement_counts": [rep["ddl"]["statement_count"] for rep in reps],
                "timer_semantics": protocol["stages"]["ddl"],
                "measurement_scope": "DDL-only formal stock PostgreSQL measurement",
            },
            "analyze": {
                "raw_seconds": analyze,
                "statistics_seconds": stats(analyze),
                "raw_statement_counts": [rep["analyze"]["statement_count"] for rep in reps],
                "relation_level_statement_count": [
                    rep["analyze"]["statement_count"] for rep in reps
                ],
                "timer_semantics": protocol["stages"]["analyze"],
                "measurement_scope": "single relation-level ANALYZE on stock PostgreSQL",
                "payload_verification": [
                    rep["analyze"]["payload_verification_passed"] for rep in reps
                ],
            },
            "sequential_ddl_plus_analyze": {
                "raw_seconds": sequential,
                "statistics_seconds": stats(sequential),
                "semantics": "derived sum of separately measured sequential stages",
            },
            "logical_catalog_row_bytes": logical,
            "physical_catalog_relation_allocation_deltas": {
                "raw_bytes": physical_deltas,
                "semantics": protocol["storage"]["physical_delta_semantics"],
            },
            "base_relation_total_bytes_context": [
                rep["base_relation_total_bytes_context"] for rep in reps
            ],
            "payload_verification": all(
                rep["analyze"]["payload_verification_passed"] for rep in reps
            ),
            "cleanup": all(rep["cleanup"]["database_dropped"] for rep in reps),
        }
        ddl_median = median(ddl)
        analyze_median = median(analyze)
        sequential_median = median(sequential)
        fractions[dataset_id] = {
            "ddl_fraction_of_sequential_deployment": ddl_median / sequential_median,
            "analyze_fraction_of_sequential_deployment": analyze_median / sequential_median,
            "ddl_median_less_than_analyze_median": ddl_median < analyze_median,
        }
        headline_rows.append(
            {
                "dataset_id": dataset_id,
                "selected_k": raw["object_count"],
                "ddl_seconds_median": ddl_median,
                "analyze_seconds_median": analyze_median,
                "sequential_ddl_plus_analyze_seconds_median": sequential_median,
                "logical_after_ddl_bytes_median": median(logical["after_ddl"]["total"]),
                "logical_after_analyze_bytes_median": median(logical["after_analyze"]["total"]),
                "physical_after_ddl_delta_bytes_median": median(
                    physical_deltas["after_ddl_minus_baseline"][
                        "combined_catalog_total_relation_bytes"
                    ]
                ),
                "physical_after_analyze_incremental_delta_bytes_median": median(
                    physical_deltas["after_analyze_minus_after_ddl"][
                        "combined_catalog_total_relation_bytes"
                    ]
                ),
                "physical_final_delta_bytes_median": median(
                    physical_deltas["after_analyze_minus_baseline"][
                        "combined_catalog_total_relation_bytes"
                    ]
                ),
            }
        )
    return {
        "per_dataset": per_dataset,
        "headline_rows": headline_rows,
        "deployment_fractions": fractions,
    }


def _build_inventory_v2_without_digest(research_root: Path) -> dict[str, Any]:
    _v1, _summary, _preflight, raw_by_dataset, protocol = _read_static_deployment_sources(
        research_root
    )
    base = _build_inventory_without_digest(research_root)
    projection = _static_projection(raw_by_dataset, protocol)
    body = dict(base)
    body["format_version"] = FORMAT_VERSION_V2
    body["supersedes_inventory"] = dict(V1_INVENTORY)
    body["source_evidence"] = dict(body["source_evidence"])
    body["source_evidence"]["static_deployment"] = {
        "summary": dict(STATIC_DEPLOYMENT_SUMMARY),
        "preflight": {
            "path": STATIC_DEPLOYMENT_SUMMARY["preflight_path"],
            "semantic_digest": STATIC_DEPLOYMENT_SUMMARY["preflight_semantic_digest"],
        },
        "raw_children": [{"dataset_id": d, **STATIC_DEPLOYMENT_RAW[d]} for d in DATASETS],
        "producer_sha": STATIC_DEPLOYMENT_SUMMARY["producer_sha"],
        "protocol_path": "paper/rq5-static-deployment-cost-protocol-v1.json",
        "protocol_semantic_digest": STATIC_PROTOCOL_DIGEST,
    }
    body["stages"] = dict(body["stages"])
    stages = body["stages"]
    stages["snapshot capture"] = dict(stages["snapshot capture"])
    stages["snapshot capture"]["unmeasured_required_metrics"] = ["snapshot_bytes"]
    stages["snapshot capture"]["measured_metrics"] = {
        "snapshot_capture_elapsed_seconds": {
            d: _rq2_values(research_root)[d]["snapshot_capture_seconds"] for d in DATASETS
        },
        "metric_status": {"capture_time": "formal-measured", "snapshot_bytes": "missing"},
    }
    static_paths = [
        STATIC_DEPLOYMENT_SUMMARY["path"],
        *[STATIC_DEPLOYMENT_RAW[d]["path"] for d in DATASETS],
    ]
    static_digests = [
        STATIC_DEPLOYMENT_SUMMARY["semantic_digest"],
        *[STATIC_DEPLOYMENT_RAW[d]["semantic_digest"] for d in DATASETS],
    ]
    static_metrics = {
        "headline_rows": projection["headline_rows"],
        "per_dataset": projection["per_dataset"],
    }
    stages["deployment DDL"] = _stage(
        "deployment-ddl",
        "deployment DDL",
        "formal-measured",
        "formal-static-deployment-stock",
        static_paths,
        static_digests,
        list(DATASETS),
        static_metrics,
        [],
        "DDL-only recommendation definition creation on stock PostgreSQL; ANALYZE and verification excluded",
        ["This is not total deployment cost"],
        True,
    )
    stages["deployment ANALYZE"] = _stage(
        "deployment-analyze",
        "deployment ANALYZE",
        "formal-measured",
        "formal-static-deployment-stock",
        static_paths,
        static_digests,
        list(DATASETS),
        static_metrics,
        [],
        "One relation-level ANALYZE after exact DDL verification",
        ["Payload verification is recorded; this is not online query latency"],
        True,
    )
    stages["catalog/storage"] = _stage(
        "catalog-storage",
        "catalog/storage",
        "formal-measured",
        "formal-static-deployment-stock",
        static_paths,
        static_digests,
        list(DATASETS),
        static_metrics,
        [],
        "Recommendation-local logical catalog row bytes and page-granular physical catalog relation allocation",
        [
            "Logical and physical views are not additive",
            "Physical allocation deltas are not exact per-object attribution",
            "Object count and payload presence are not byte estimators",
        ],
        True,
    )
    stages["post-deployment planning"] = dict(stages["post-deployment planning"])
    stages["post-deployment planning"]["unmeasured_required_metrics"] = []
    stages["post-deployment planning"]["optional_external_validity_gaps"] = [
        "production_online_query_latency"
    ]
    stages["post-deployment planning"]["limitations"] = [
        "P0/P1 are offline 10,000-query EXPLAIN workload timings, not production online latency"
    ]
    body["coverage_matrix"] = _coverage(stages)
    body["formal_operational_costs"] = body["formal_operational_costs"]
    body["static_deployment_cost"] = {
        "summary": dict(STATIC_DEPLOYMENT_SUMMARY),
        "preflight": {
            "path": STATIC_DEPLOYMENT_SUMMARY["preflight_path"],
            "semantic_digest": STATIC_DEPLOYMENT_SUMMARY["preflight_semantic_digest"],
        },
        "producer_sha": STATIC_DEPLOYMENT_SUMMARY["producer_sha"],
        "protocol_semantic_digest": STATIC_PROTOCOL_DIGEST,
        **projection,
        "logical_and_physical_storage_are_not_additive": True,
        "physical_delta_not_per_object_attribution": True,
        "no_canonical_end_to_end_elapsed": True,
    }
    body["protocol_required_unresolved"] = [
        {
            "stage": "snapshot capture",
            "missing_metric": "snapshot_bytes",
            "reason": "snapshot size representation requires a protocol decision; file/JSON size is not silently substituted",
            "completion_blocker": True,
            "would_require_new_live_measurement": True,
        },
        {
            "stage": "production exact truth acquisition",
            "missing_metric": "exact_count_elapsed_seconds",
            "reason": "equivalence evidence does not contain exact-count acquisition timing",
            "completion_blocker": True,
            "would_require_new_live_measurement": True,
        },
        {
            "stage": "refresh",
            "missing_metric": "refresh_elapsed_seconds",
            "reason": "no tracked refresh protocol or execution exists",
            "completion_blocker": True,
            "would_require_new_live_measurement": True,
        },
        {
            "stage": "refresh",
            "missing_metric": "refresh_quality_trend",
            "reason": "no tracked refresh quality trend exists",
            "completion_blocker": True,
            "would_require_new_live_measurement": True,
        },
    ]
    body["optional_external_validity_gaps"] = [
        {
            "stage": "post-deployment planning",
            "missing_metric": "production_online_query_latency",
            "reason": "offline EXPLAIN timing is not online latency",
            "completion_blocker": False,
        }
    ]
    body["remaining_measurement_gaps"] = body["protocol_required_unresolved"]
    body["claim_readiness"] = {
        "can_claim_external_truth_import_cost": True,
        "can_claim_incremental_singleton_savings": True,
        "can_claim_search_scaling_cost": True,
        "can_claim_deployment_ddl_cost": True,
        "can_claim_deployment_analyze_cost": True,
        "can_claim_catalog_storage_cost": True,
        "can_claim_post_deployment_planning_cost": True,
        "can_claim_production_exact_truth_cost": False,
        "can_claim_refresh_cost": False,
        "can_claim_refresh_quality_trend": False,
        "can_claim_end_to_end_advisor_cost": False,
    }
    body["interpretation"] = {
        "external_truth_import_is_not_production_exact_cost": True,
        "production_exact_truth_cost_is_unmeasured": True,
        "historical_rq2_combined_deployment_is_context_only": True,
        "p0_p1_are_offline_explain_workload_timings": True,
        "production_online_query_latency_is_optional_external_validity": True,
        "ks_sensitivity_is_B4_not_canonical_B8": True,
        "canonical_production_configuration": {"K_s": 8, "B": 8},
        "static_repetitions_are_not_refresh": True,
        "component_timings_must_not_be_summed": True,
        "logical_and_physical_storage_are_not_additive": True,
        "physical_delta_not_per_object_attribution": True,
        "ddl_median_less_than_analyze_median_all_datasets": all(
            item["ddl_median_less_than_analyze_median"]
            for item in projection["deployment_fractions"].values()
        ),
        "no_canonical_end_to_end_elapsed": True,
    }
    return body


def build_inventory_v2(research_root: Path) -> dict[str, Any]:
    body = _build_inventory_v2_without_digest(research_root)
    body["semantic_digest"] = semantic_digest(body)
    return body


def write_inventory_v2(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    output_path = output or default_inventory_v2_path(research_root)
    value = build_inventory_v2(research_root)
    write_json(output_path, value)
    return value


def validate_inventory_v2(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(
        value.get("format_version") == FORMAT_VERSION_V2, "unsupported RQ5 inventory v2 format"
    )
    digest = value.get("semantic_digest")
    _require(isinstance(digest, str), "RQ5 inventory v2 lacks semantic_digest")
    _require(
        digest == semantic_digest(_without_digest(value, "semantic_digest")),
        "RQ5 inventory v2 digest mismatch",
    )
    _require(value.get("rq5_completion_status") == "incomplete", "RQ5 must remain incomplete")
    _require(
        value.get("rq5_registry_status") == RQ5_STATUS, "RQ5 registry status must remain planned"
    )
    _require(
        value.get("generated_from_tracked_json_only") is True, "v2 inventory is not offline-only"
    )
    _require(
        value.get("new_postgresql_execution") is False,
        "v2 inventory claims new PostgreSQL execution",
    )
    _require(
        value.get("new_planner_execution") is False, "v2 inventory claims new planner execution"
    )
    _require(value.get("new_benchmark_run") is False, "v2 inventory claims a new benchmark run")
    _require(value.get("no_synthetic_total") is True, "v2 inventory must forbid synthetic totals")
    _require("total_advisor_seconds" not in value, "synthetic end-to-end total is forbidden")
    _require(value.get("supersedes_inventory") == V1_INVENTORY, "v1 supersession gate drifted")
    expected = build_inventory_v2(research_root)
    _require(value == expected, "RQ5 inventory v2 does not match immutable source-derived evidence")
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION_V2,
        "experiment_id": EXPERIMENT_ID,
        "semantic_digest": digest,
        "rq5_completion_status": value["rq5_completion_status"],
        "dataset_count": len(DATASETS),
        "remaining_measurement_gap_count": len(value["remaining_measurement_gaps"]),
    }


def _snapshot_stats(values: list[float | int]) -> dict[str, Any]:
    return _stats(values)


def _snapshot_component_projection(
    raw_by_dataset: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    component_keys = (
        "manifest_json_bytes",
        "schema_json_bytes",
        "population_json_bytes",
        "workload_json_bytes",
        "sample_payload_bytes_total",
        "sealed_snapshot_logical_bytes",
        "regular_file_count",
    )
    per_dataset: dict[str, Any] = {}
    headline_rows: list[dict[str, Any]] = []
    for dataset_id in DATASETS:
        raw = raw_by_dataset[dataset_id]
        repetitions = raw["repetitions"]
        component_values = {
            key: [rep["component_bytes"][key] for rep in repetitions] for key in component_keys
        }
        capture_values = [rep["snapshot_capture_elapsed_seconds"] for rep in repetitions]
        semantic_digests = [rep["snapshot_semantic_digest"] for rep in repetitions]
        sample_hashes = [
            next(
                item["sha256"]
                for item in rep["per_file_inventory"]
                if item["component_role"] == "sample-payload"
            )
            for rep in repetitions
        ]
        workload_hashes = [
            next(
                item["sha256"]
                for item in rep["per_file_inventory"]
                if item["component_role"] == "workload"
            )
            for rep in repetitions
        ]
        sample_fractions = [
            sample / total
            for sample, total in zip(
                component_values["sample_payload_bytes_total"],
                component_values["sealed_snapshot_logical_bytes"],
                strict=True,
            )
        ]
        workload_fractions = [
            workload / total
            for workload, total in zip(
                component_values["workload_json_bytes"],
                component_values["sealed_snapshot_logical_bytes"],
                strict=True,
            )
        ]
        component_summary = {
            key: {
                "raw": values,
                **_snapshot_stats(values),
            }
            for key, values in component_values.items()
        }
        per_dataset[dataset_id] = {
            "relation": raw["relation"],
            "workload_id": raw["workload_id"],
            "sealed_snapshot_logical_bytes": component_summary["sealed_snapshot_logical_bytes"],
            "sample_payload_bytes_total": component_summary["sample_payload_bytes_total"],
            "snapshot_capture_elapsed_seconds": {
                "raw": capture_values,
                **_snapshot_stats(capture_values),
            },
            "component_bytes": component_summary,
            "snapshot_semantic_digests": semantic_digests,
            "snapshot_semantic_digests_equal": len(set(semantic_digests)) == 1,
            "sample_payload_sha256": sample_hashes,
            "sample_payload_sha256_stable": len(set(sample_hashes)) == 1,
            "workload_sha256": workload_hashes,
            "workload_sha256_stable": len(set(workload_hashes)) == 1,
            "composition": {
                "sample_payload_fraction_of_snapshot": {
                    "raw": sample_fractions,
                    **_snapshot_stats(sample_fractions),
                },
                "workload_json_fraction_of_snapshot": {
                    "raw": workload_fractions,
                    **_snapshot_stats(workload_fractions),
                },
            },
            "validation_passed": all(rep["validation_passed"] for rep in repetitions),
            "cleanup_passed": all(rep["cleanup_passed"] for rep in repetitions),
        }
        headline_rows.append(
            {
                "dataset_id": dataset_id,
                "sealed_snapshot_logical_bytes": component_summary["sealed_snapshot_logical_bytes"],
                "sample_payload_bytes_total": component_summary["sample_payload_bytes_total"],
                "snapshot_capture_elapsed_seconds": {
                    "raw": capture_values,
                    **_snapshot_stats(capture_values),
                },
                "sample_payload_fraction_median": median(sample_fractions),
                "workload_json_fraction_median": median(workload_fractions),
            }
        )
    return per_dataset, headline_rows


def _read_snapshot_sources(
    research_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    summary = _read_pinned(research_root, SNAPSHOT_FORMAL, "RQ5 snapshot footprint summary")
    _require(
        summary.get("format_version") == "rq5-snapshot-footprint-v1",
        "snapshot footprint summary format drifted",
    )
    _require(summary.get("status") == "complete", "snapshot footprint summary is not complete")
    _require(
        summary.get("research_commit_sha") == SNAPSHOT_FORMAL["producer_sha"],
        "snapshot footprint producer drifted",
    )
    _require(
        summary.get("protocol_semantic_digest") == SNAPSHOT_PROTOCOL_DIGEST,
        "snapshot footprint protocol digest drifted",
    )
    _require(
        summary.get("preflight_path") == SNAPSHOT_FORMAL["preflight_path"],
        "snapshot footprint preflight path drifted",
    )
    _require(
        summary.get("preflight_semantic_digest") == SNAPSHOT_FORMAL["preflight_semantic_digest"],
        "snapshot footprint preflight digest drifted",
    )
    _require(summary.get("dataset_order") == list(DATASETS), "snapshot dataset order drifted")
    _require(summary.get("repetitions") == 3, "snapshot repetition count drifted")
    _require(summary.get("no_snapshot_contents_tracked") is True, "snapshot contents are tracked")
    _require(summary.get("not_refresh") is True, "snapshot evidence was relabeled as refresh")
    expected_raw_refs = [{"dataset_id": d, **SNAPSHOT_RAW[d]} for d in DATASETS]
    _require(
        summary.get("raw_children") == expected_raw_refs,
        "snapshot raw child references drifted",
    )

    preflight = _read_pinned(
        research_root,
        {
            "path": SNAPSHOT_FORMAL["preflight_path"],
            "semantic_digest": SNAPSHOT_FORMAL["preflight_semantic_digest"],
        },
        "RQ5 snapshot footprint preflight",
    )
    _require(
        preflight.get("research_commit_sha") == SNAPSHOT_FORMAL["producer_sha"],
        "snapshot preflight producer drifted",
    )
    _require(
        preflight.get("advisor_sha") == "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3",
        "snapshot preflight Advisor identity drifted",
    )
    _require(
        preflight.get("stock_postgres_sha") == "0d1c00c624fa7367d4a895f44381887757289682",
        "snapshot preflight stock identity drifted",
    )
    _require(preflight.get("sample_rows") == 10_000, "snapshot sample row count drifted")
    _require(preflight.get("sample_seed") == 42, "snapshot sample seed drifted")
    _require(preflight.get("repetitions") == 3, "snapshot preflight repetitions drifted")
    _require(
        preflight.get("dataset_order") == list(DATASETS),
        "snapshot preflight dataset order drifted",
    )
    source_specs = preflight.get("datasets")
    _require(isinstance(source_specs, list), "snapshot preflight dataset specs are missing")
    _require(
        [item.get("dataset_id") for item in source_specs] == list(DATASETS),
        "snapshot preflight source dataset order drifted",
    )
    source_by_dataset = {item["dataset_id"]: item for item in source_specs}

    raw_by_dataset: dict[str, dict[str, Any]] = {}
    source_keys = (
        "benchmark_id",
        "dataset_content_identity",
        "relation",
        "schema_contract_id",
        "workload_id",
        "workload_sha256",
        "workload_query_count",
        "canonical_workload_sha256",
    )
    for dataset_id in DATASETS:
        raw = _read_pinned(research_root, SNAPSHOT_RAW[dataset_id], f"snapshot raw {dataset_id}")
        raw_by_dataset[dataset_id] = raw
        _require(
            raw.get("format_version") == "rq5-snapshot-footprint-dataset-v1",
            f"{dataset_id} snapshot raw format drifted",
        )
        _require(raw.get("status") == "complete", f"{dataset_id} snapshot raw is not complete")
        _require(raw.get("dataset_id") == dataset_id, f"{dataset_id} snapshot identity drifted")
        _require(
            raw.get("research_commit_sha") == SNAPSHOT_FORMAL["producer_sha"],
            f"{dataset_id} snapshot producer drifted",
        )
        _require(
            raw.get("advisor_sha") == preflight["advisor_sha"],
            f"{dataset_id} snapshot Advisor identity drifted",
        )
        _require(
            raw.get("stock_postgres_sha") == preflight["stock_postgres_sha"],
            f"{dataset_id} snapshot stock identity drifted",
        )
        _require(
            raw.get("protocol_semantic_digest") == SNAPSHOT_PROTOCOL_DIGEST,
            f"{dataset_id} snapshot protocol digest drifted",
        )
        _require(
            raw.get("preflight_path") == SNAPSHOT_FORMAL["preflight_path"]
            and raw.get("preflight_semantic_digest")
            == SNAPSHOT_FORMAL["preflight_semantic_digest"],
            f"{dataset_id} snapshot preflight provenance drifted",
        )
        source = source_by_dataset[dataset_id]
        for key in source_keys:
            _require(
                raw.get(key) == source.get(key),
                f"{dataset_id} snapshot source field drifted: {key}",
            )
        repetitions = raw.get("repetitions")
        _require(
            isinstance(repetitions, list) and len(repetitions) == 3,
            f"{dataset_id} snapshot repetition count drifted",
        )
        for expected_id, repetition in enumerate(repetitions, 1):
            _require(
                repetition.get("repetition_id") == expected_id,
                f"{dataset_id} snapshot repetition order drifted",
            )
            for key in (
                "research_commit_sha",
                "advisor_sha",
                "stock_postgres_sha",
                "benchmark_id",
                "dataset_content_identity",
                "relation",
                "schema_contract_id",
                "workload_id",
                "workload_sha256",
                "workload_query_count",
                "canonical_workload_sha256",
                "sample_rows",
                "sample_seed",
            ):
                _require(
                    repetition.get(key) == raw.get(key),
                    f"{dataset_id} snapshot repetition provenance drifted: {key}",
                )
            _require(repetition.get("status") == "complete", f"{dataset_id} repetition incomplete")
            _require(
                repetition.get("validation_passed") is True,
                f"{dataset_id} snapshot validation failed",
            )
            _require(
                repetition.get("cleanup_passed") is True,
                f"{dataset_id} snapshot cleanup failed",
            )
            components = repetition.get("component_bytes")
            files = repetition.get("per_file_inventory")
            _require(isinstance(components, Mapping), f"{dataset_id} component bytes missing")
            _require(isinstance(files, list), f"{dataset_id} file inventory missing")
            _require(
                components.get("regular_file_count") == len(files),
                f"{dataset_id} regular file count drifted",
            )
            _require(
                components.get("sealed_snapshot_logical_bytes")
                == sum(item.get("logical_bytes", -1) for item in files),
                f"{dataset_id} sealed snapshot byte sum drifted",
            )
            sample_files = [
                item for item in files if item.get("component_role") == "sample-payload"
            ]
            workload_files = [item for item in files if item.get("component_role") == "workload"]
            _require(sample_files, f"{dataset_id} sample payload file is missing")
            _require(workload_files, f"{dataset_id} workload file is missing")
            _require(
                components.get("sample_payload_bytes_total")
                == sum(item.get("logical_bytes", -1) for item in sample_files),
                f"{dataset_id} sample payload subtotal drifted",
            )
        per_file_roles = [
            item.get("component_role") for item in repetitions[0]["per_file_inventory"]
        ]
        _require("manifest" in per_file_roles, f"{dataset_id} manifest component is missing")
        _require("schema" in per_file_roles, f"{dataset_id} schema component is missing")
        _require("population" in per_file_roles, f"{dataset_id} population component is missing")
    per_dataset, headline_rows = _snapshot_component_projection(raw_by_dataset)
    for dataset_id in DATASETS:
        expected = summary["datasets"][dataset_id]
        projected = per_dataset[dataset_id]
        for key in (
            "relation",
            "workload_id",
            "sealed_snapshot_logical_bytes",
            "sample_payload_bytes_total",
            "snapshot_capture_elapsed_seconds",
            "snapshot_semantic_digests",
            "snapshot_semantic_digests_equal",
            "validation_passed",
            "cleanup_passed",
        ):
            _require(
                expected.get(key) == projected.get(key),
                f"snapshot summary projection drifted for {dataset_id}: {key}",
            )
    return (
        summary,
        preflight,
        raw_by_dataset,
        {
            "per_dataset": per_dataset,
            "headline_rows": headline_rows,
        },
    )


def _build_inventory_v3_without_digest(research_root: Path) -> dict[str, Any]:
    v2 = _read_pinned(research_root, V2_INVENTORY, "RQ5 inventory v2")
    _require(v2.get("format_version") == FORMAT_VERSION_V2, "pinned RQ5 inventory is not v2")
    _require(
        v2 == build_inventory_v2(research_root),
        "RQ5 inventory v2 source-derived content drifted",
    )
    _, _, _, snapshot_projection = _read_snapshot_sources(research_root)
    body = deepcopy(v2)
    body.pop("semantic_digest", None)
    body["format_version"] = FORMAT_VERSION_V3
    body["supersedes_inventory"] = dict(V2_INVENTORY)

    snapshot_paths = [SNAPSHOT_FORMAL["path"], SNAPSHOT_FORMAL["preflight_path"]]
    snapshot_paths.extend(SNAPSHOT_RAW[d]["path"] for d in DATASETS)
    snapshot_digests = [
        SNAPSHOT_FORMAL["semantic_digest"],
        SNAPSHOT_FORMAL["preflight_semantic_digest"],
    ]
    snapshot_digests.extend(SNAPSHOT_RAW[d]["semantic_digest"] for d in DATASETS)
    body["source_evidence"] = deepcopy(body["source_evidence"])
    body["source_evidence"]["snapshot_footprint"] = {
        "summary": dict(SNAPSHOT_FORMAL),
        "preflight": {
            "path": SNAPSHOT_FORMAL["preflight_path"],
            "semantic_digest": SNAPSHOT_FORMAL["preflight_semantic_digest"],
        },
        "raw_children": [{"dataset_id": d, **SNAPSHOT_RAW[d]} for d in DATASETS],
        "producer_sha": SNAPSHOT_FORMAL["producer_sha"],
        "protocol_path": "paper/rq5-snapshot-footprint-protocol-v1.json",
        "protocol_semantic_digest": SNAPSHOT_PROTOCOL_DIGEST,
        "size_definition": SNAPSHOT_SIZE_DEFINITION,
        "logical_size_excludes": [
            "filesystem allocation",
            "du allocated blocks",
            "compressed archive size",
            "manifest-only size",
            "Python object memory size",
        ],
    }
    stages = deepcopy(body["stages"])
    stages["snapshot capture"] = _stage(
        "snapshot-capture",
        "snapshot capture",
        "formal-measured",
        "formal-snapshot-footprint-stock",
        snapshot_paths,
        snapshot_digests,
        list(DATASETS),
        {
            "snapshot_capture_elapsed_seconds": {
                d: snapshot_projection["per_dataset"][d]["snapshot_capture_elapsed_seconds"]
                for d in DATASETS
            },
            "sealed_snapshot_logical_bytes": {
                d: snapshot_projection["per_dataset"][d]["sealed_snapshot_logical_bytes"]
                for d in DATASETS
            },
            "sample_payload_bytes_total": {
                d: snapshot_projection["per_dataset"][d]["sample_payload_bytes_total"]
                for d in DATASETS
            },
            "snapshot_size_semantics": SNAPSHOT_SIZE_DEFINITION,
        },
        [],
        "Validated sealed advisor-snapshot-v1 logical regular-file footprint and capture timing",
        [
            "Logical file bytes are not filesystem allocation bytes",
            "Snapshot semantic digest variation is retained; equality is not required",
            "Snapshot contents are not tracked in this inventory",
        ],
        True,
    )
    body["stages"] = stages
    body["coverage_matrix"] = _coverage(stages)
    body["snapshot_footprint"] = {
        "summary": dict(SNAPSHOT_FORMAL),
        "preflight": {
            "path": SNAPSHOT_FORMAL["preflight_path"],
            "semantic_digest": SNAPSHOT_FORMAL["preflight_semantic_digest"],
        },
        "producer_sha": SNAPSHOT_FORMAL["producer_sha"],
        "protocol_path": "paper/rq5-snapshot-footprint-protocol-v1.json",
        "protocol_semantic_digest": SNAPSHOT_PROTOCOL_DIGEST,
        "size_definition": SNAPSHOT_SIZE_DEFINITION,
        "logical_size_is_not_filesystem_allocation": True,
        "logical_size_is_not_compressed_size": True,
        "logical_size_is_not_manifest_only": True,
        "per_dataset": snapshot_projection["per_dataset"],
        "headline_rows": snapshot_projection["headline_rows"],
    }
    body["protocol_required_unresolved"] = [
        {
            "stage": "production exact truth acquisition",
            "missing_metric": "exact_count_elapsed_seconds",
            "datasets": list(DATASETS),
            "reason": "Tracked evidence does not contain production exact-count acquisition timing",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "refresh",
            "missing_metric": "refresh_elapsed_seconds",
            "datasets": list(DATASETS),
            "reason": "No tracked refresh protocol or execution exists",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
        {
            "stage": "refresh",
            "missing_metric": "refresh_quality_trend",
            "datasets": list(DATASETS),
            "reason": "No tracked refresh quality trend exists",
            "would_require_new_live_measurement": True,
            "priority": "main-text-critical",
        },
    ]
    body["remaining_measurement_gaps"] = deepcopy(body["protocol_required_unresolved"])
    body["claim_readiness"] = dict(body["claim_readiness"])
    body["claim_readiness"].update(
        {
            "can_claim_snapshot_capture_cost": True,
            "can_claim_snapshot_size": True,
            "can_claim_production_exact_truth_cost": False,
            "can_claim_refresh_cost": False,
            "can_claim_refresh_quality_trend": False,
            "can_claim_end_to_end_advisor_cost": False,
        }
    )
    body["optional_external_validity_gaps"] = [
        {
            "stage": "post-deployment planning",
            "missing_metric": "production_online_query_latency",
            "reason": "Offline EXPLAIN timing is not online latency",
            "completion_blocker": False,
        }
    ]
    body["interpretation"] = dict(body["interpretation"])
    body["interpretation"].update(
        {
            "snapshot_capture_cost_is_formally_measured": True,
            "snapshot_size_is_formally_measured": True,
            "snapshot_size_is_logical_regular_file_sum": True,
            "snapshot_semantic_digest_equality_is_not_required": True,
            "sample_payload_sha256_stable_within_dataset": all(
                item["sample_payload_sha256_stable"]
                for item in snapshot_projection["per_dataset"].values()
            ),
            "workload_sha256_stable_within_dataset": all(
                item["workload_sha256_stable"]
                for item in snapshot_projection["per_dataset"].values()
            ),
            "snapshot_repetitions_are_not_refresh": True,
            "external_truth_import_is_not_production_exact_cost": True,
            "no_canonical_end_to_end_elapsed": True,
        }
    )
    return body


def build_inventory_v3(research_root: Path) -> dict[str, Any]:
    body = _build_inventory_v3_without_digest(research_root)
    body["semantic_digest"] = semantic_digest(body)
    return body


def write_inventory_v3(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    output_path = output or default_inventory_v3_path(research_root)
    value = build_inventory_v3(research_root)
    write_json(output_path, value)
    return value


def validate_inventory_v3(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(
        value.get("format_version") == FORMAT_VERSION_V3,
        "unsupported RQ5 inventory v3 format",
    )
    digest = value.get("semantic_digest")
    _require(isinstance(digest, str), "RQ5 inventory v3 lacks semantic_digest")
    _require(
        digest == semantic_digest(_without_digest(value, "semantic_digest")),
        "RQ5 inventory v3 digest mismatch",
    )
    _require(value.get("rq5_completion_status") == "incomplete", "RQ5 must remain incomplete")
    _require(
        value.get("rq5_registry_status") == RQ5_STATUS,
        "RQ5 registry status must remain planned",
    )
    _require(
        value.get("generated_from_tracked_json_only") is True, "v3 inventory is not offline-only"
    )
    _require(
        value.get("new_postgresql_execution") is False, "v3 inventory claims PostgreSQL execution"
    )
    _require(value.get("new_planner_execution") is False, "v3 inventory claims planner execution")
    _require(value.get("new_benchmark_run") is False, "v3 inventory claims a benchmark run")
    _require(value.get("no_synthetic_total") is True, "v3 inventory must forbid synthetic totals")
    _require("total_advisor_seconds" not in value, "synthetic end-to-end total is forbidden")
    _require(value.get("supersedes_inventory") == V2_INVENTORY, "v2 supersession gate drifted")
    expected = build_inventory_v3(research_root)
    _require(value == expected, "RQ5 inventory v3 does not match source-derived evidence")
    snapshot_stage = value["stages"]["snapshot capture"]
    _require(
        snapshot_stage["measurement_status"] == "formal-measured",
        "snapshot time/size is not formal",
    )
    _require(snapshot_stage["unmeasured_required_metrics"] == [], "snapshot gap remains unresolved")
    _require(
        value["snapshot_footprint"]["size_definition"] == SNAPSHOT_SIZE_DEFINITION,
        "snapshot size semantics drifted",
    )
    _require(
        value["claim_readiness"]["can_claim_snapshot_size"] is True,
        "snapshot size claim is not ready",
    )
    _require(
        all(
            item["completion_blocker"] is False for item in value["optional_external_validity_gaps"]
        ),
        "optional external gap became a completion blocker",
    )
    _require(
        all(item["stage"] != "snapshot capture" for item in value["protocol_required_unresolved"]),
        "snapshot capture remains a required unresolved gap",
    )
    _require(
        value["claim_readiness"]["can_claim_end_to_end_advisor_cost"] is False,
        "v3 inventory claims an end-to-end Advisor total",
    )
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION_V3,
        "experiment_id": EXPERIMENT_ID,
        "semantic_digest": digest,
        "rq5_completion_status": value["rq5_completion_status"],
        "dataset_count": len(DATASETS),
        "remaining_measurement_gap_count": len(value["remaining_measurement_gaps"]),
    }
