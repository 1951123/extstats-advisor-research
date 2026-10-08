"""Offline aggregation of the completed RQ4 K_s sensitivity children.

This module deliberately consumes only tracked JSON evidence.  It never imports
the Advisor bridge, opens PostgreSQL, or performs planner evaluation.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, write_json

SUMMARY_FORMAT = "rq4-ks-sensitivity-cross-dataset-summary-v1"
EXPERIMENT_ID = "rq4-ks-sensitivity-cross-dataset-summary"
PROTOCOL_PATH = "paper/top-k-screening-protocol-v2.json"
PROTOCOL_DIGEST = "55c29212eabbd59dcc3539d9b4f538390305431a35181126866c383f1f15faec"
SYSTEM_FREEZE_DIGEST = "552c44e1f80244632061815c723f0cd8674aee45a9e013524320aed2314bdc48"
FIXED_B = 4
FINITE_WIDTHS = (4, 8, 16, 32)
SCREENING_WIDTHS = (*FINITE_WIDTHS, "all")
DATASETS = ("arecel-census13", "arecel-power7", "arecel-dmv11")

CHILDREN = {
    "arecel-power7": {
        "path": "experiments/arecel-power7/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json",
        "semantic_digest": "85b832eae7f253d7306b710ed769b5a9d48914bc4ad773c00e45c6a93a9db8e5",
    },
    "arecel-census13": {
        "path": "experiments/arecel-census13/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json",
        "semantic_digest": "119dc4e886a61ee023b4045f74f43ed102c3fadc060fdb4fbd16c739188b1ee8",
    },
    "arecel-dmv11": {
        "path": "experiments/arecel-dmv11/rq4-ks-sensitivity-v1/rq4-ks-sensitivity-v1.json",
        "semantic_digest": "569994babd3b4bc5d07d82a3b6bfad03ca2b13a596d8533a16d68b7ee2a74109",
    },
}

_REQUIRED_POINT_FIELDS = (
    "K_s",
    "execution_mode",
    "effective_candidate_count",
    "selected_k",
    "selected_candidate_ids",
    "status",
    "termination_reason",
    "proposal_configuration_objective_evaluations",
    "incremental_planner_calls",
    "reference_search_planner_calls",
    "saved_search_planner_calls",
    "planner_call_reduction_fraction",
    "selection_wall_clock_seconds",
)


class RQ4KSSummaryValidationError(ValueError):
    """Raised when the aggregation contract or an immutable child is invalid."""


def default_summary_path(research_root: Path) -> Path:
    return research_root / "experiments/rq4-ks-sensitivity-cross-dataset-summary-v1.json"


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ4KSSummaryValidationError(message)


def _number(value: Any, label: str) -> float | int:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{label} must be a finite number",
    )
    return value


def _child_path(root: Path, dataset_id: str) -> Path:
    return root / CHILDREN[dataset_id]["path"]


def _validate_child(value: Mapping[str, Any], dataset_id: str) -> dict[str, Any]:
    expected = CHILDREN[dataset_id]
    _require(
        value.get("format_version") == "rq4-ks-sensitivity-v1", f"{dataset_id} child format drifted"
    )
    _require(
        value.get("experiment_id") == "rq4-ks-sensitivity-v1",
        f"{dataset_id} child identity drifted",
    )
    _require(value.get("dataset_id") == dataset_id, f"{dataset_id} child dataset identity drifted")
    _require(value.get("status") == "complete", f"{dataset_id} child is not complete")
    _require(
        value.get("formal_confirmatory_experiment") is True, f"{dataset_id} child is not formal"
    )
    _require(
        value.get("screening_widths") == list(SCREENING_WIDTHS), f"{dataset_id} child grid drifted"
    )
    _require(value.get("fixed_B") == FIXED_B, f"{dataset_id} child B drifted")
    _require(
        value.get("search_wall_clock_seconds") == 300.0, f"{dataset_id} child wall cap drifted"
    )
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        f"{dataset_id} child protocol digest drifted",
    )
    _require(
        value.get("system_freeze_semantic_digest") == SYSTEM_FREEZE_DIGEST,
        f"{dataset_id} child system freeze digest drifted",
    )
    _require(
        isinstance(value.get("research_commit_sha"), str),
        f"{dataset_id} child lacks research provenance",
    )
    _require(
        value.get("advisor_commit_sha") == "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3",
        f"{dataset_id} child Advisor identity drifted",
    )
    _require(
        value.get("patched_postgres_commit_sha") == "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6",
        f"{dataset_id} child patched PostgreSQL identity drifted",
    )
    _require(
        value.get("stock_physical_evaluation", {}).get("performed") is False,
        f"{dataset_id} child claims stock physical evaluation",
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_payload(value)),
        f"{dataset_id} child semantic digest is invalid",
    )
    _require(
        value.get("semantic_digest") == expected["semantic_digest"],
        f"{dataset_id} child digest drifted",
    )
    points = value.get("points")
    _require(
        isinstance(points, list) and len(points) == len(SCREENING_WIDTHS),
        f"{dataset_id} child point count drifted",
    )
    by_width = {point.get("K_s"): point for point in points if isinstance(point, Mapping)}
    _require(set(by_width) == set(SCREENING_WIDTHS), f"{dataset_id} child grid is incomplete")
    for width in SCREENING_WIDTHS:
        point = by_width[width]
        _require(
            all(field in point for field in _REQUIRED_POINT_FIELDS),
            f"{dataset_id} K_s={width} fields are incomplete",
        )
        _require(
            isinstance(point.get("selected_candidate_ids"), list),
            f"{dataset_id} K_s={width} selection is invalid",
        )
        _require(
            isinstance(point.get("search_result"), Mapping),
            f"{dataset_id} K_s={width} search result is missing",
        )
        _require(
            isinstance(point["search_result"].get("final_objective"), (int, float)),
            f"{dataset_id} K_s={width} objective is missing",
        )
        final = point.get("final_sandbox_evaluation")
        _require(
            isinstance(final, Mapping), f"{dataset_id} K_s={width} final evaluation is missing"
        )
        _require(
            final.get("performed") is True,
            f"{dataset_id} K_s={width} final evaluation was not performed",
        )
        _require(
            final.get("evaluation_scope") == "independent-final-sandbox-evaluation",
            f"{dataset_id} K_s={width} final scope drifted",
        )
        _require(
            final.get("selection_budget_charged") is False,
            f"{dataset_id} K_s={width} final evaluation charged to selection",
        )
        _require(
            final.get("stock_physical_evaluation") is False,
            f"{dataset_id} K_s={width} stock evaluation claimed",
        )
    return dict(value)


def _reference_semantics(point: Mapping[str, Any]) -> str:
    if (
        point.get("status") == "complete"
        and point.get("termination_reason") == "max-statistics-count"
        and point.get("selected_k") == FIXED_B
    ):
        return "completed-full-universe-greedy-reference"
    if (
        point.get("status") == "budget-censored"
        and point.get("termination_reason") == "budget-expired-incomplete-round"
        and point.get("selected_k") == 2
    ):
        return "bounded-full-universe-incumbent"
    raise RQ4KSSummaryValidationError("unsupported all-point reference semantics")


def _project_point(point: Mapping[str, Any]) -> dict[str, Any]:
    final = point["final_sandbox_evaluation"]
    search = point["search_result"]
    return {
        "K_s": point["K_s"],
        "execution_mode": point["execution_mode"],
        "effective_candidate_count": point["effective_candidate_count"],
        "selected_k": point["selected_k"],
        "selected_candidate_ids": list(point["selected_candidate_ids"]),
        "status": point["status"],
        "termination_reason": point["termination_reason"],
        "final_objective": search["final_objective"],
        "proposal_configuration_objective_evaluations": point[
            "proposal_configuration_objective_evaluations"
        ],
        "incremental_planner_calls": point["incremental_planner_calls"],
        "reference_search_planner_calls": point["reference_search_planner_calls"],
        "saved_search_planner_calls": point["saved_search_planner_calls"],
        "planner_call_reduction_fraction": point["planner_call_reduction_fraction"],
        "selection_wall_clock_seconds": point["selection_wall_clock_seconds"],
        "final_evaluation_planner_calls": final["postgresql_planner_query_calls"],
        "final_evaluation_wall_clock_seconds": final["wall_clock_seconds"],
    }


def _comparisons(
    points: list[dict[str, Any]], all_point: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    all_objective = float(all_point["final_objective"])
    all_calls = float(all_point["incremental_planner_calls"])
    all_wall = float(all_point["selection_wall_clock_seconds"])
    result: dict[str, dict[str, Any]] = {}
    for point in points:
        objective = float(point["final_objective"])
        calls = float(point["incremental_planner_calls"])
        wall = float(point["selection_wall_clock_seconds"])
        result[str(point["K_s"])] = {
            "objective_delta_vs_all": objective - all_objective,
            "objective_ratio_vs_all": objective / all_objective,
            "planner_calls_delta_vs_all": point["incremental_planner_calls"]
            - all_point["incremental_planner_calls"],
            "planner_calls_fraction_of_all": calls / all_calls,
            "planner_calls_reduction_vs_all": 1.0 - calls / all_calls,
            "wall_delta_vs_all": wall - all_wall,
            "wall_fraction_of_all": wall / all_wall,
            "wall_reduction_vs_all": 1.0 - wall / all_wall,
            "selected_k_delta_vs_all": point["selected_k"] - all_point["selected_k"],
        }
    return result


def _dominance(points: list[dict[str, Any]]) -> dict[str, dict[str, list[int]]]:
    dominates = {str(point["K_s"]): [] for point in points}
    dominated_by = {str(point["K_s"]): [] for point in points}
    for left in points:
        for right in points:
            if left["K_s"] == right["K_s"]:
                continue
            no_worse = (
                left["final_objective"] <= right["final_objective"]
                and left["incremental_planner_calls"] <= right["incremental_planner_calls"]
            )
            strict = (
                left["final_objective"] < right["final_objective"]
                or left["incremental_planner_calls"] < right["incremental_planner_calls"]
            )
            if no_worse and strict:
                dominates[str(left["K_s"])].append(right["K_s"])
                dominated_by[str(right["K_s"])].append(left["K_s"])
    return {"dominated_by": dominated_by, "dominates": dominates}


def _dataset_summary(dataset_id: str, child: Mapping[str, Any], path: str) -> dict[str, Any]:
    points = [
        _project_point(point)
        for point in sorted(child["points"], key=lambda item: SCREENING_WIDTHS.index(item["K_s"]))
    ]
    all_point = next(point for point in points if point["K_s"] == "all")
    finite = [point for point in points if point["K_s"] != "all"]
    best = min(point["final_objective"] for point in finite)
    best_width = next(point["K_s"] for point in finite if point["final_objective"] == best)
    all_semantics = _reference_semantics(all_point)
    return {
        "status": "complete",
        "artifact": path,
        "artifact_semantic_digest": child["semantic_digest"],
        "all_reference_semantics": all_semantics,
        "points": points,
        "comparisons_vs_all": _comparisons(finite, all_point),
        "finite_point_dominance": _dominance(finite),
        "best_finite_objective": best,
        "smallest_width_matching_best_finite_objective": best_width,
        "best_finite_point": {
            "K_s": best_width,
            "selected_k": next(
                point["selected_k"] for point in finite if point["K_s"] == best_width
            ),
            "incremental_planner_calls": next(
                point["incremental_planner_calls"] for point in finite if point["K_s"] == best_width
            ),
            "selection_wall_clock_seconds": next(
                point["selection_wall_clock_seconds"]
                for point in finite
                if point["K_s"] == best_width
            ),
        },
    }


def _load_children(research_root: Path) -> dict[str, dict[str, Any]]:
    result = {}
    for dataset_id in DATASETS:
        path = _child_path(research_root, dataset_id)
        _require(
            path.is_file(), f"missing immutable {dataset_id} child: {CHILDREN[dataset_id]['path']}"
        )
        value = read_json(path)
        _require(isinstance(value, Mapping), f"{dataset_id} child is not a JSON object")
        result[dataset_id] = _validate_child(value, dataset_id)
    return result


def build_cross_dataset_summary(research_root: Path) -> dict[str, Any]:
    """Build the summary from exactly the three pinned formal children."""

    root = research_root.resolve()
    children = _load_children(root)
    datasets = {
        dataset_id: _dataset_summary(dataset_id, children[dataset_id], CHILDREN[dataset_id]["path"])
        for dataset_id in DATASETS
    }
    best_widths = {
        dataset_id: datasets[dataset_id]["smallest_width_matching_best_finite_objective"]
        for dataset_id in DATASETS
    }
    finite_matches = {
        str(width): [
            dataset_id
            for dataset_id in DATASETS
            if any(
                point["K_s"] == width
                and point["final_objective"] == datasets[dataset_id]["best_finite_objective"]
                for point in datasets[dataset_id]["points"]
                if point["K_s"] != "all"
            )
        ]
        for width in FINITE_WIDTHS
    }
    all_semantics = {
        dataset_id: datasets[dataset_id]["all_reference_semantics"] for dataset_id in DATASETS
    }
    cross_points = []
    for dataset_id in DATASETS:
        for point in datasets[dataset_id]["points"]:
            row = {
                "dataset_id": dataset_id,
                **point,
                "all_reference_semantics": datasets[dataset_id]["all_reference_semantics"],
            }
            if point["K_s"] != "all":
                row.update(datasets[dataset_id]["comparisons_vs_all"][str(point["K_s"])])
            cross_points.append(row)
    artifact: dict[str, Any] = {
        "format_version": SUMMARY_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "dataset-scope-complete",
        "aggregation_scope": "three completed rq4-ks-sensitivity-v1 children",
        "aggregation_only": True,
        "new_postgresql_execution": False,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "finite_widths": list(FINITE_WIDTHS),
        "screening_widths": list(SCREENING_WIDTHS),
        "fixed_B": FIXED_B,
        "dataset_count": len(DATASETS),
        "datasets": datasets,
        "cross_dataset_points": cross_points,
        "cross_dataset_findings": {
            "datasets_where_K8_matches_best_finite_objective": finite_matches["8"],
            "datasets_where_K16_matches_best_finite_objective": finite_matches["16"],
            "datasets_where_K32_matches_best_finite_objective": finite_matches["32"],
            "datasets_where_finite_point_beats_all_objective": [
                dataset_id
                for dataset_id in DATASETS
                if any(
                    point["K_s"] != "all"
                    and point["final_objective"]
                    < next(
                        item["final_objective"]
                        for item in datasets[dataset_id]["points"]
                        if item["K_s"] == "all"
                    )
                    for point in datasets[dataset_id]["points"]
                )
            ],
            "datasets_where_all_is_budget_censored": [
                dataset_id
                for dataset_id in DATASETS
                if all_semantics[dataset_id] == "bounded-full-universe-incumbent"
            ],
            "datasets_where_all_is_complete": [
                dataset_id
                for dataset_id in DATASETS
                if all_semantics[dataset_id] == "completed-full-universe-greedy-reference"
            ],
        },
        "headline_counts": {
            "completed_full_universe_reference_count": sum(
                semantic == "completed-full-universe-greedy-reference"
                for semantic in all_semantics.values()
            ),
            "censored_full_universe_reference_count": sum(
                semantic == "bounded-full-universe-incumbent" for semantic in all_semantics.values()
            ),
        },
        "smallest_best_finite_widths": best_widths,
        "production_configuration_note": (
            "This sensitivity fixes B=4. The frozen production configuration remains K_s=8, B=8; "
            "the aggregation does not retune or replace that configuration."
        ),
        "global_rq4_status": "incomplete",
        "global_rq4_incomplete_reasons": [
            "rq4-fixed-evaluation-budget is implementation-needed",
            "rq4-native-analyze-stability remains planned",
        ],
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    return artifact


def validate_cross_dataset_summary(
    artifact_or_path: Mapping[str, Any] | Path,
    *,
    research_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the summary and recompute it from the pinned child evidence."""

    value = (
        read_json(artifact_or_path)
        if isinstance(artifact_or_path, Path)
        else dict(artifact_or_path)
    )
    _require(value.get("format_version") == SUMMARY_FORMAT, "unsupported K_s summary format")
    _require(value.get("experiment_id") == EXPERIMENT_ID, "K_s summary identity drifted")
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    expected = build_cross_dataset_summary(root)
    _require(
        value.get("semantic_digest") == semantic_digest(_payload(value)),
        "K_s summary digest mismatch",
    )
    _require(value == expected, "K_s summary does not match immutable child-derived aggregation")
    return {
        "status": "valid",
        "format_version": SUMMARY_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def write_cross_dataset_summary(research_root: Path, output: Path | None = None) -> dict[str, Any]:
    artifact = build_cross_dataset_summary(research_root)
    destination = output or default_summary_path(research_root)
    write_json(destination, artifact)
    validate_cross_dataset_summary(destination, research_root=research_root)
    return artifact


__all__ = [
    "DATASETS",
    "FINITE_WIDTHS",
    "SCREENING_WIDTHS",
    "SUMMARY_FORMAT",
    "build_cross_dataset_summary",
    "default_summary_path",
    "validate_cross_dataset_summary",
    "write_cross_dataset_summary",
]
