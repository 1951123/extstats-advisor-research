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
    population = snapshot_manifest.get("population", [])
    sample_counts = snapshot_manifest.get("sample_row_counts", {})
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
            (item.get("row_count") for item in population if isinstance(item, dict)), None
        ),
        "candidate_count": len(candidate.get("candidates", [])),
        "candidate_states": {
            "PRESENT": profile.get(
                "present_count",
                sum(1 for x in profile.get("profiles", []) if x.get("state") == "PRESENT"),
            ),
            "ABSENT": profile.get(
                "absent_count",
                sum(1 for x in profile.get("profiles", []) if x.get("state") == "ABSENT_NATIVE"),
            ),
        },
        "baseline": search.get("baseline_objective", profile.get("baseline_objective")),
        "best_singleton": {
            "candidate_id": profile.get("best_singleton_candidate_id"),
            "objective": profile.get("best_singleton_objective"),
            "improvement": profile.get("best_singleton_improvement"),
        },
        "optimization": {
            "candidate_limit": plan.get("candidate_limit"),
            "screened_count": plan.get("screened_candidate_count", plan.get("candidate_count")),
        },
        "search": {
            "final_objective": search.get("final_objective"),
            "absolute_improvement": search.get("improvement"),
            "relative_improvement": search.get("relative_improvement"),
            "termination": search.get("termination_reason"),
            "accepted_moves": search.get(
                "accepted_move_count", len(search.get("accepted_moves", []))
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
            name: _artifact_digest(path) for name, path in paths.items() if name != "summary"
        },
    }
    write_json(paths["summary"], summary)
    return summary
