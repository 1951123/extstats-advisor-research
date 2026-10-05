"""Forest10 canonical-run validation and compact evidence helpers."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA
from .pins import verify_git_sha
from .provenance import semantic_digest


def compare_label_maps(expected: dict[str, int], observed: dict[str, int]) -> dict[str, Any]:
    """Compare two complete query-id/cardinality maps and fail closed."""
    expected_ids = set(expected)
    observed_ids = set(observed)
    missing = sorted(expected_ids - observed_ids)
    extra = sorted(observed_ids - expected_ids)
    mismatches = [
        {
            "query_id": query_id,
            "audited": expected[query_id],
            "production": observed[query_id],
        }
        for query_id in sorted(expected_ids & observed_ids)
        if expected[query_id] != observed[query_id]
    ]
    if missing or extra or mismatches:
        raise ValueError(
            "Forest production truth does not match audited labels: "
            f"matched={len(expected_ids & observed_ids) - len(mismatches)}, "
            f"mismatched={len(mismatches)}, missing={len(missing)}, extra={len(extra)}, "
            f"first_mismatch={mismatches[0] if mismatches else None!r}"
        )
    return {
        "matched": len(expected_ids),
        "mismatched": 0,
        "missing": 0,
        "extra": 0,
        "query_count": len(expected_ids),
    }


def compare_production_truth_to_labels(
    snapshot_path: Path,
    ground_truth_path: Path,
    audited_records: list[dict[str, Any]],
    *,
    advisor_root: Path,
) -> dict[str, Any]:
    """Load production artifacts through public APIs and compare every audited label."""
    verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA)
    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.ground_truth.artifact import load_ground_truth_set
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    ground_truth = load_ground_truth_set(ground_truth_path, snapshot)
    labels = {
        f"arecel_forest10_test_{record['source_index']:06d}": int(record["truth"])
        for record in audited_records
    }
    observed = {truth.query_id: int(truth.cardinality) for truth in ground_truth.truths}
    comparison = compare_label_maps(labels, observed)
    expected_ids = set(labels)
    if len(expected_ids) != 10_000:
        raise ValueError(
            f"Forest canonical truth comparison expected 10000 labels, got {len(expected_ids)}"
        )
    source = ground_truth.source
    if source.kind != "production-exact-execution":
        raise ValueError(f"Forest truth source is not production-exact-execution: {source.kind}")
    return {
        **comparison,
        "snapshot_digest": snapshot.semantic_digest,
        "ground_truth_digest": ground_truth.semantic_digest,
        "source_kind": source.kind,
        "source_view_token": source.source_view_token,
    }


def sampling_provenance(snapshot_path: Path, *, advisor_root: Path) -> dict[str, Any]:
    verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA)
    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    sampling = dict(snapshot.semantic_provenance.get("sampling", {}))
    required = {
        "method",
        "seed",
        "requested_rows",
        "actual_rows",
        "successful_percentage",
        "candidate_row_limit",
        "attempts",
    }
    missing = sorted(required - set(sampling))
    if missing:
        raise ValueError(f"Forest snapshot is missing sampling provenance: {missing}")
    return {key: sampling[key] for key in sorted(required)}


def compact_summary(
    *,
    run_id: str,
    run_directory: str,
    manifest: dict[str, Any],
    full_data: dict[str, Any],
    paper_baseline: dict[str, Any],
    candidate_universe: dict[str, Any],
    native_repository: dict[str, Any],
    singleton_profile: dict[str, Any],
    optimization_plan: dict[str, Any],
    search_result: dict[str, Any],
    recommendation: dict[str, Any],
    truth_validation: dict[str, Any],
    sampling: dict[str, Any],
    stage_timings: dict[str, float],
    audit: dict[str, Any],
) -> dict[str, Any]:
    candidates = candidate_universe.get("candidates", [])
    incidence = candidate_universe.get("incidence", [])
    pairs = {
        tuple(item.get("column_names", []))
        for item in candidates
        if len(item.get("column_names", [])) == 2
    }
    native_candidates = native_repository.get("candidates", [])
    native_states = {
        state: sum(item.get("state") == state for item in native_candidates)
        for state in ("present", "absent-native")
    }
    profiles = singleton_profile.get("candidate_profiles", [])
    singleton = {
        "present": sum(item.get("native_state") == "present" for item in profiles),
        "improving": sum(
            item.get("native_state") == "present" and item.get("improvement", 0) > 0
            for item in profiles
        ),
        "neutral": sum(
            item.get("native_state") == "present" and item.get("improvement", 0) == 0
            for item in profiles
        ),
        "worsening": sum(
            item.get("native_state") == "present" and item.get("improvement", 0) < 0
            for item in profiles
        ),
    }
    best_profile = min(
        (item for item in profiles if item.get("native_state") == "present"),
        key=lambda item: item.get("singleton_objective", float("inf")),
        default={},
    )
    best = {
        "candidate_id": best_profile.get("candidate_id"),
        "kind": None,
        "columns": None,
        "objective": best_profile.get("singleton_objective"),
        "improvement": best_profile.get("improvement"),
    }
    candidate_by_id = {item["candidate_id"]: item for item in candidates}
    if best["candidate_id"] in candidate_by_id:
        best_candidate = candidate_by_id[best["candidate_id"]]
        best["kind"] = best_candidate.get("kind")
        best["columns"] = best_candidate.get("column_names")
    selected = search_result.get("final_ordered_candidate_ids", [])
    recommendation_order = recommendation.get(
        "deployment_ordered_candidate_ids", recommendation.get("deployment_order", [])
    )
    source_artifacts = dict(manifest.get("artifacts", {}))
    source_artifacts["full_data_target100"] = {
        "path": "full-data-target100-v1.json",
        "semantic_digest": full_data["semantic_digest"],
    }
    if audit:
        source_artifacts["audit"] = {
            "path": "analysis/audit-v1.json",
            "semantic_digest": audit.get("semantic_digest"),
        }
    result = {
        "format_version": "arecel-forest-canonical-k8-summary-v1",
        "run_id": run_id,
        "run_directory": run_directory,
        "research_repository": manifest["research_repository"],
        "research_commit_sha": manifest["research_commit_sha"],
        "advisor_repository": manifest["advisor_repository"],
        "advisor_commit_sha": manifest["advisor_commit_sha"],
        "patched_postgres_repository": manifest["patched_postgres_repository"],
        "patched_postgres_commit_sha": manifest["patched_postgres_commit_sha"],
        "dataset": {
            "benchmark_id": "arecel-forest10",
            "content_identity": manifest["dataset_content_identity"],
            "schema_contract_id": "arecel-forest10-postgres-schema-v1",
            "row_count": 581012,
            "workload_id": manifest["workload_id"],
            "workload_sha256": manifest["workload_sha256"],
            "label_sha256": manifest["label_sha256"],
        },
        "settings": {
            "sample_rows": manifest["sample_rows"],
            "sample_seed": manifest["sample_seed"],
            "statistics_target": manifest["statistics_target"],
            "candidate_limit": manifest["candidate_limit"],
            "search_wall_clock_seconds": manifest["search_wall_clock_seconds"],
        },
        "paper_postgresql_target10000": paper_baseline["summary"],
        "full_data_target100_ordinary": full_data["summary"],
        "snapshot_sampling": sampling,
        "truth_validation": truth_validation,
        "candidates": {
            "candidate_count": len(candidates),
            "incidence_count": len(incidence),
            "relevant_pair_count": len(pairs),
            "relevant_group_count": len(pairs),
        },
        "native": {
            "present": native_states["present"],
            "absent_native": native_states["absent-native"],
            "repository_digest": manifest.get("native_repository_semantic_digest"),
        },
        "singleton": {**singleton, "best": best},
        "optimization": {
            "candidate_limit": optimization_plan.get("budget", {}).get("candidate_limit"),
            "screened_candidate_count": optimization_plan.get("screened_candidate_count"),
        },
        "search": {
            "baseline_objective": search_result.get("baseline_objective"),
            "final_objective": search_result.get("final_objective"),
            "absolute_improvement": search_result.get("improvement"),
            "relative_improvement": (
                search_result.get("improvement") / search_result["baseline_objective"]
                if search_result.get("improvement") is not None
                and search_result.get("baseline_objective")
                else None
            ),
            "termination_reason": search_result.get("termination_reason"),
            "accepted_moves": search_result.get("accepted_moves", []),
            "selected_candidates": selected,
        },
        "recommendation": {
            "decision": recommendation.get("decision"),
            "selected_membership": recommendation.get("selected_candidate_ids", selected),
            "deployment_order": recommendation_order,
            "statistics_definitions": recommendation.get("selected_candidates", []),
            "object_count": len(recommendation.get("selected_candidates", [])),
        },
        "audit": {
            "semantic_digest": audit.get("semantic_digest"),
            "per_query_sha256": audit.get("per_query_sha256"),
            "record_count": audit.get("predicate_diagnostics", {}).get("query_count"),
            "distributions": audit.get("distributions"),
            "classification": audit.get("classification"),
            "tail_queries": audit.get("tail_queries"),
        },
        "source_artifacts": source_artifacts,
        "deployment": {"performed": False},
    }
    result["semantic_digest"] = semantic_digest(result)
    result["stage_timings"] = stage_timings
    return result
