"""Small, production-objective summary extracted from advisor artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..provenance import read_json, sha256_file, write_json


def _artifact_digest(path: Path) -> str | None:
    manifest = path / "manifest.json" if path.is_dir() else path
    if not manifest.is_file():
        return None
    value = read_json(manifest)
    return value.get("semantic_digest") or value.get("sha256") or sha256_file(manifest)


def extract_summary(layout: Any, *, benchmark_id: str, workload: dict[str, Any]) -> dict[str, Any]:
    paths = layout.artifacts()
    candidate = read_json(paths["candidate_universe"])
    profile = read_json(paths["singleton_profile"])
    plan = read_json(paths["optimization_plan"])
    search = read_json(paths["search_result"])
    recommendation = read_json(paths["recommendation"])
    snapshot_manifest = read_json(paths["snapshot"] / "manifest.json")
    native_manifest = read_json(paths["native_repository"] / "manifest.json")
    population = snapshot_manifest.get("population", [])
    sample_counts = snapshot_manifest.get("sample_row_counts", {})
    if not sample_counts:
        sample_counts = {
            item["relation_id"]: item["sample_row_count"]
            for item in snapshot_manifest.get("sample_inventory", [])
            if "relation_id" in item and "sample_row_count" in item
        }
    candidate_profiles = profile.get("candidate_profiles", profile.get("profiles", []))
    native_candidates = native_manifest.get("candidates", [])
    state_source = native_candidates or candidate_profiles
    state_key = "state" if native_candidates else "native_state"
    baseline_objective = profile.get("baseline", {}).get(
        "objective", profile.get("baseline_objective")
    )
    best_profile = min(
        candidate_profiles,
        key=lambda item: item.get("singleton_objective", item.get("objective", float("inf"))),
        default={},
    )
    best_candidate_id = profile.get("best_singleton_candidate_id", best_profile.get("candidate_id"))
    best_objective = profile.get(
        "best_singleton_objective", best_profile.get("singleton_objective")
    )
    best_improvement = profile.get("best_singleton_improvement", best_profile.get("improvement"))
    plan_budget = plan.get("budget", {})
    search_runtime = search.get("runtime_metadata", {})
    selected = recommendation.get(
        "deployment_ordered_candidate_ids",
        recommendation.get("deployment_order", recommendation.get("ordered_candidate_ids", [])),
    )
    summary = {
        "format": "research-metrics-summary-v1",
        "benchmark_id": benchmark_id,
        "workload_id": workload["workload_id"],
        "query_count": len(workload["queries"]),
        "sample_row_count": next(iter(sample_counts.values()), None),
        "population_row_count": next(
            (item.get("row_count") for item in population if isinstance(item, dict)),
            native_manifest.get("materialization", {}).get("population_row_count"),
        ),
        "candidate_count": len(candidate.get("candidates", [])),
        "candidate_states": {
            "PRESENT": profile.get(
                "present_count",
                sum(1 for item in state_source if item.get(state_key) in {"present", "PRESENT"}),
            ),
            "ABSENT": profile.get(
                "absent_count",
                sum(
                    1
                    for item in state_source
                    if item.get(state_key) in {"absent", "absent-native", "ABSENT", "ABSENT_NATIVE"}
                ),
            ),
        },
        "baseline": search.get("baseline_objective", baseline_objective),
        "best_singleton": {
            "candidate_id": best_candidate_id,
            "objective": best_objective,
            "improvement": best_improvement,
        },
        "optimization": {
            "candidate_limit": plan.get("candidate_limit", plan_budget.get("candidate_limit")),
            "screened_count": plan.get("screened_candidate_count", plan.get("candidate_count")),
        },
        "search": {
            "final_objective": search.get("final_objective"),
            "absolute_improvement": search.get("improvement"),
            "relative_improvement": search.get(
                "relative_improvement",
                (
                    search.get("improvement") / search.get("baseline_objective")
                    if search.get("improvement") is not None and search.get("baseline_objective")
                    else None
                ),
            ),
            "termination": search.get("termination_reason"),
            "accepted_moves": search.get(
                "accepted_move_count",
                search_runtime.get("accepted_move_count", len(search.get("accepted_moves", []))),
            ),
            "selected_candidate_ids": search.get(
                "final_ordered_candidate_ids", search.get("selected_candidate_ids", selected)
            ),
        },
        "recommendation": {
            "decision": recommendation.get("decision"),
            "order": selected,
        },
        "artifact_digests": {
            name: _artifact_digest(path)
            for name, path in paths.items()
            if name not in {"logs", "summary"}
        },
    }
    write_json(paths["summary"], summary)
    return summary
