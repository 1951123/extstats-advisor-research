"""Offline audit of RQ4 selection-budget comparability.

This module reads only committed RQ4 protocol/design artifacts.  It never
opens PostgreSQL, invokes Advisor code, or reconstructs external workloads.
The audit intentionally records missing timing fields as ``None`` rather than
turning them into zero-cost claims.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, sha256_file

FORMAT_VERSION = "rq4-time-budget-fairness-audit-v1"
AUDIT_SOURCE_COMMIT = "bbd6ea2933ef241855a432ef1c85b941a2c55bfd"
PROTOCOL_PATH = "paper/top-k-screening-protocol-v2.json"
FIXED_SUMMARY_PATH = "experiments/rq4-fixed-k-cross-dataset-summary-v1.json"
KS_SUMMARY_PATH = "experiments/rq4-ks-sensitivity-cross-dataset-summary-v1.json"

FIXED_DESIGNS = {
    "arecel-census13": "experiments/arecel-census13/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
    "arecel-forest10": "experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json",
    "arecel-power7": "experiments/arecel-power7/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
    "arecel-dmv11": "experiments/arecel-dmv11/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
}


def _without_digest(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item
        for key, item in value.items()
        if key not in {"semantic_digest", "artifact_digest"}
    }


def _source_reference(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    value = read_json(path)
    stored = value.get("semantic_digest", value.get("artifact_digest"))
    computed = semantic_digest(_without_digest(value))
    if stored != computed:
        raise ValueError(f"source semantic digest mismatch: {relative}")
    return {
        "path": relative,
        "semantic_digest": computed,
        "byte_sha256": sha256_file(path),
    }


def _compact_method(method_id: str, value: dict[str, Any], *, version: str) -> dict[str, Any]:
    if version == "rq4-design-evaluation-v1":
        accounting = value["accounting"]
        selection = accounting["selection"]
        final = accounting["final"]
        return {
            "method_id": method_id,
            "status": value.get("status"),
            "termination_reason": value.get("termination_or_censoring_reason"),
            "selected_k": len(value.get("selected_membership", [])),
            "selection": {
                "configuration_objective_evaluations": selection.get(
                    "configuration_objective_evaluations"
                ),
                "planner_query_calls": selection.get("postgresql_planner_query_calls"),
                "wall_clock_seconds": selection.get("elapsed_wall_clock_seconds"),
                "timing_scope": "recorded",
            },
            "final_evaluation": {
                "configuration_objective_evaluations": final.get(
                    "configuration_objective_evaluations"
                ),
                "planner_query_calls": final.get("postgresql_planner_query_calls"),
                "wall_clock_seconds": final.get("elapsed_wall_clock_seconds"),
                "excluded_from_selection_budget": True,
            },
        }

    selection = value.get("selection_accounting", {})
    final = value.get("final_sandbox_evaluation", {})
    search = value.get("search_result", {}).get("runtime_metadata", {})
    if method_id == "greedy-ADD":
        selection_record = {
            "configuration_objective_evaluations": search.get("proposal_configuration_evaluations"),
            "planner_query_calls": search.get("actual_search_planner_calls"),
            "wall_clock_seconds": search.get("elapsed_search_seconds"),
            "timing_scope": "recorded-search-runtime",
            "partial_final_round_evaluations": search.get("partial_final_round_evaluation_count"),
        }
        termination = value.get("termination_reason")
    elif method_id == "singleton-utility-top-k":
        selection_record = {
            "configuration_objective_evaluations": selection.get(
                "source_configuration_objective_evaluations"
            ),
            "planner_query_calls": selection.get("source_postgresql_planner_query_calls"),
            "wall_clock_seconds": selection.get("source_wall_clock_seconds"),
            "timing_scope": "reused-profile; source-wall-time-unavailable",
            "new_selection_configuration_objective_evaluations": selection.get(
                "new_selection_configuration_objective_evaluations"
            ),
        }
        termination = value.get("termination_reason")
    else:
        selection_record = {
            "configuration_objective_evaluations": selection.get(
                "configuration_objective_evaluations"
            ),
            "planner_query_calls": selection.get("postgresql_planner_query_calls"),
            "backend_wall_clock_seconds": selection.get("wall_clock_seconds"),
            "wall_clock_seconds": None,
            "timing_scope": "selection-preprocessing-wall-time-not-recorded",
        }
        termination = value.get("termination_reason")
    return {
        "method_id": method_id,
        "status": value.get("status"),
        "termination_reason": termination,
        "selected_k": len(value.get("selected_membership", [])),
        "selection": selection_record,
        "final_evaluation": {
            "configuration_objective_evaluations": final.get("configuration_objective_evaluations"),
            "planner_query_calls": final.get("postgresql_planner_query_calls"),
            "wall_clock_seconds": final.get("wall_clock_seconds"),
            "excluded_from_selection_budget": final.get("selection_budget_charged") is False,
        },
    }


def build_audit_artifact(research_root: Path) -> dict[str, Any]:
    """Build a deterministic audit record from committed RQ4 artifacts."""

    source_paths = [PROTOCOL_PATH, FIXED_SUMMARY_PATH, KS_SUMMARY_PATH, *FIXED_DESIGNS.values()]
    sources = [_source_reference(research_root, relative) for relative in source_paths]
    ks_summary = read_json(research_root / KS_SUMMARY_PATH)
    ks_evidence = {
        dataset_id: {
            "artifact_semantic_digest": value["artifact_semantic_digest"],
            "status": value["status"],
            "smallest_width_matching_best_finite_objective": value[
                "smallest_width_matching_best_finite_objective"
            ],
            "best_finite_objective": value["best_finite_objective"],
            "points": [
                {
                    "K_s": point["K_s"],
                    "execution_mode": point["execution_mode"],
                    "status": point["status"],
                    "termination_reason": point["termination_reason"],
                    "selection_wall_clock_seconds": point["selection_wall_clock_seconds"],
                    "proposal_configuration_objective_evaluations": point[
                        "proposal_configuration_objective_evaluations"
                    ],
                    "incremental_planner_calls": point["incremental_planner_calls"],
                    "final_objective": point["final_objective"],
                }
                for point in value["points"]
            ],
        }
        for dataset_id, value in ks_summary["datasets"].items()
    }
    datasets: dict[str, Any] = {}
    for dataset_id, relative in FIXED_DESIGNS.items():
        value = read_json(research_root / relative)
        version = value["format_version"]
        if version == "rq4-design-evaluation-v1":
            dataset_name = value["dataset"]["dataset_id"]
            comparison = value["comparison"]
            methods = value["comparison"]["methods"]
            records = [
                _compact_method(method, methods[method], version=version)
                for method in comparison["method_order"]
            ]
            status = value["status"]
        else:
            dataset_name = value["dataset_id"]
            comparison = value["selection_budget"]
            methods = value["methods"]
            records = [
                _compact_method(method, methods[method], version=version)
                for method in value["method_order"]
            ]
            status = value["status"]
        datasets[dataset_id] = {
            "dataset_id": dataset_name,
            "design_artifact": relative,
            "design_format": version,
            "design_semantic_digest": value["semantic_digest"],
            "evidence_status": status,
            "selection_budget": {
                "unit": comparison["unit"]
                if "unit" in comparison
                else comparison["budget"]["unit"],
                "max_configuration_objective_evaluations": comparison.get(
                    "max_configuration_evaluations",
                    comparison.get("budget", {}).get("max_configuration_evaluations"),
                ),
                "wall_clock_seconds": comparison.get(
                    "wall_clock_seconds", comparison.get("budget", {}).get("wall_clock_seconds")
                ),
                "final_evaluation_excluded": comparison.get(
                    "final_evaluation_excluded",
                    comparison.get("budget", {}).get("final_evaluation_excluded"),
                ),
            },
            "methods": records,
            "anytime_feasibility": "partial-anytime-trace",
        }

    artifact: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "experiment_id": "rq4-time-budget-fairness-audit",
        "audit_source_commit": AUDIT_SOURCE_COMMIT,
        "source_artifacts": sources,
        "declared_budget_semantics": {
            "K_s": "candidate screening width; not a planner-call or elapsed-time budget",
            "B": "selected-statistics count bound; fixed-k quality artifacts use k=4",
            "T_seconds": 300.0,
            "configuration_objective_evaluation_cap": 2000,
            "budget_clock": "_BudgetedEvaluator construction through pre-call checks; timeout checked between backend calls",
            "final_evaluation": "independent final sandbox evaluation is explicitly excluded",
            "physical_evaluation": "separate RQ4b stage, outside selection budget",
        },
        "code_verified_facts": [
            "_BudgetedEvaluator starts its perf_counter clock at evaluator construction.",
            "Wall-clock expiry is checked before each backend call and cannot interrupt a backend call already in progress.",
            "Greedy proposal evaluation uses the evaluator; cheap rankings do not perform selection objective evaluations.",
            "Greedy accepts a winner only after a complete proposal round; an incomplete round returns the prior incumbent.",
            "run_rq4_ablation rejects fixed_evaluation_budget rather than implementing a common allocator.",
            "RQ4 v2 performs a second deterministic selection replay and then independent final sandbox evaluations.",
        ],
        "artifact_verified_facts": [
            "Census13 and DMV11 Greedy are budget-censored after incomplete rounds at approximately 300 seconds.",
            "Power7 Greedy reaches max-statistics-count at 185.87962744000106 seconds.",
            "Forest10 is historical rq4-design-evaluation-v1 evidence with a 3600-second declared wall-clock budget.",
            "Singleton source profile planner calls are recorded for v2, but source profile wall-clock seconds are null.",
            "Cheap-method v2 selection planner calls are recorded as zero; their final evaluations are separate 10000-call stages.",
        ],
        "datasets": datasets,
        "ks_sensitivity_evidence": {
            "summary_path": KS_SUMMARY_PATH,
            "screening_widths": ks_summary["screening_widths"],
            "fixed_B": ks_summary["fixed_B"],
            "dataset_scope": ks_evidence,
            "global_status": ks_summary["global_rq4_status"],
            "incomplete_reasons": ks_summary["global_rq4_incomplete_reasons"],
        },
        "comparison_assessment": {
            "search_only_time_fairness": "fair-within-declared-search-scope",
            "end_to_end_selection_cost_fairness": "not-established",
            "resource_fairness": "not-established",
            "reason": "The deadline constrains Greedy search and K_s points, while preprocessing, reused singleton profiling, final evaluation, and physical realization have separate or missing accounting; configuration evaluations and planner calls are not interchangeable.",
        },
        "anytime_analysis": {
            "overall_classification": "partial-anytime-trace; no defensible combined objective-vs-wall-clock curves",
            "available": [
                "Greedy records ordered search objectives, cumulative planner accounting, and elapsed search time.",
                "Forest10 singleton and Greedy traces include per-configuration backend durations.",
            ],
            "missing": [
                "Absolute event timestamps or a common time origin for all methods.",
                "Selection preprocessing wall time for v2 cheap methods and v2 reused singleton profiles.",
                "Comparable event traces for random, workload-frequency, and native-payload-size selection.",
            ],
            "permitted_output": "Use aggregate cost-quality tables or scatter points; do not interpolate a combined anytime curve.",
        },
        "fixed_evaluation_budget_recommendation": {
            "classification": "Conditionally useful",
            "core_quality_claim": "not-required",
            "efficiency_claim": "useful only if redesigned around an explicitly shared computational resource unit",
            "current_design_caveat": "A common 2000 configuration-evaluation cap alone is not a fair resource allocator because one configuration can contain thousands of planner calls and methods reuse or avoid profiling work.",
            "recommended_next_step": "First publish the offline cost-boundary accounting; only run a new budget study if the paper needs a resource-normalized efficiency claim and the protocol defines comparable planner-call/CPU/preprocessing accounting.",
        },
        "manuscript_impact": {
            "paths": [
                "paper/sections/07_evaluation_methodology.tex",
                "paper/sections/08_results.tex",
                "paper/sections/09_discussion.tex",
                "paper/experiment-plan.md",
            ],
            "proposed_corrections": [
                "Call 300 seconds a Greedy/search deadline, not an end-to-end selection-cost budget.",
                "State that fixed-k quality comparisons do not equalize total selection computation across methods.",
                "Retain the fixed-evaluation-budget item as unexecuted pending a reviewed common allocator; do not claim the current fixed-k results establish resource fairness.",
                "Retain Forest10's historical 3600-second v1 budget as a provenance exception.",
            ],
            "edits_applied_by_this_audit": False,
        },
        "unresolved_methodological_questions": [
            "Whether a future budget study should charge planner calls, CPU time, wall time including preprocessing, or a declared vector of resources.",
            "Whether reused singleton profiling should be amortized across methods or charged as method acquisition cost.",
            "How to compare incremental Greedy planner calls with full-workload configuration evaluations without changing the scientific estimand.",
        ],
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    return artifact


def validate_audit_artifact(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported RQ4 time-budget audit format")
    expected = semantic_digest(_without_digest(value))
    if value.get("semantic_digest") != expected:
        raise ValueError("RQ4 time-budget audit semantic digest mismatch")
    expected_sources = build_audit_artifact(research_root)["source_artifacts"]
    if value.get("source_artifacts") != expected_sources:
        raise ValueError("RQ4 time-budget audit source bindings mismatch")
    if value.get("fixed_evaluation_budget_recommendation", {}).get("classification") not in {
        "Required",
        "Conditionally useful",
        "Optional",
        "Not recommended",
    }:
        raise ValueError("invalid fixed-evaluation-budget recommendation")
    return {"status": "valid", "format_version": FORMAT_VERSION, "semantic_digest": expected}
