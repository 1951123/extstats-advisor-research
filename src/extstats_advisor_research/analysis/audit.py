"""Research-owned audit of one completed frozen-advisor run."""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from ..pins import verify_frozen_systems, verify_research_repository
from ..provenance import read_json, reject_credentials, semantic_digest, sha256_file, write_json

AUDIT_FORMAT_VERSION = "research-run-audit-v1"
QERROR_CONTRACT_VERSION = "qerror-cardinality-floor-1-v1"
OBJECTIVE_TOLERANCE = 1e-12


def _attribute(value: Any, name: str) -> Any:
    return getattr(value, name) if hasattr(value, name) else value[name]


def _record_summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "query_id",
            "true_rows",
            "baseline_estimated_rows",
            "final_estimated_rows",
            "baseline_qerror",
            "final_qerror",
        )
    }


def build_per_query_records(baseline: Any, final: Any) -> list[dict[str, Any]]:
    """Join two frozen-advisor UtilityResults without reimplementing q-error."""
    if _attribute(baseline, "loss_contract") != QERROR_CONTRACT_VERSION:
        raise ValueError("baseline utility does not use the q-error cardinality floor contract")
    if _attribute(final, "loss_contract") != QERROR_CONTRACT_VERSION:
        raise ValueError("final utility does not use the q-error cardinality floor contract")
    baseline_rows = {
        _attribute(item, "query_id"): item for item in _attribute(baseline, "per_query")
    }
    final_rows = {_attribute(item, "query_id"): item for item in _attribute(final, "per_query")}
    if set(baseline_rows) != set(final_rows):
        raise ValueError("baseline and final utility results cover different query IDs")
    records = []
    for query_id in sorted(baseline_rows):
        before = baseline_rows[query_id]
        after = final_rows[query_id]
        weight = float(_attribute(before, "weight"))
        truth = _attribute(before, "truth")
        if weight != float(_attribute(after, "weight")) or truth != _attribute(after, "truth"):
            raise ValueError(f"baseline/final utility metadata differs for query {query_id}")
        baseline_qerror = float(_attribute(before, "loss"))
        final_qerror = float(_attribute(after, "loss"))
        absolute_change = baseline_qerror - final_qerror
        if absolute_change > 0:
            classification = "improved"
        elif absolute_change < 0:
            classification = "worsened"
        else:
            classification = "unchanged"
        records.append(
            {
                "query_id": query_id,
                "weight": weight,
                "true_rows": truth,
                "baseline_estimated_rows": int(_attribute(before, "estimate")),
                "baseline_qerror": baseline_qerror,
                "final_estimated_rows": int(_attribute(after, "estimate")),
                "final_qerror": final_qerror,
                "absolute_qerror_change": absolute_change,
                "relative_qerror_change": (
                    absolute_change / baseline_qerror if baseline_qerror else None
                ),
                "classification": classification,
            }
        )
    return records


def weighted_objective(records: list[dict[str, Any]], field: str) -> float:
    total_weight = sum(record["weight"] for record in records)
    if total_weight <= 0:
        raise ValueError("audit requires positive total workload weight")
    return sum(record["weight"] * record[field] for record in records) / total_weight


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile of an empty distribution")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def distribution(records: list[dict[str, Any]], field: str, objective: float) -> dict[str, Any]:
    values = [float(record[field]) for record in records]
    return {
        "min": min(values),
        "median": _percentile(values, 0.50),
        "p50": _percentile(values, 0.50),
        "p75": _percentile(values, 0.75),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values),
        "arithmetic_mean": sum(values) / len(values),
        "weighted_objective": objective,
        "quantile_method": "linear-interpolation-n-minus-1",
    }


def _tail(records: list[dict[str, Any]], key: str, reverse: bool) -> list[dict[str, Any]]:
    return [
        _record_summary(record)
        for record in sorted(records, key=lambda x: x[key], reverse=reverse)[:10]
    ]


def contribution_summary(
    records: list[dict[str, Any]], qerror_key: str = "baseline_qerror"
) -> dict[str, Any]:
    denominator = sum(record["weight"] * record[qerror_key] for record in records)
    ranked = sorted(
        records,
        key=lambda record: record["weight"] * record[qerror_key],
        reverse=True,
    )
    result: dict[str, Any] = {}
    counts = {
        "top_1": 1,
        "top_5": 5,
        "top_10": 10,
        "top_1_percent": max(1, math.ceil(len(records) * 0.01)),
        "top_5_percent": max(1, math.ceil(len(records) * 0.05)),
        "top_10_percent": max(1, math.ceil(len(records) * 0.10)),
    }
    for label, count in counts.items():
        selected = ranked[:count]
        contribution = sum(item["weight"] * item[qerror_key] for item in selected)
        result[label] = {
            "query_count": len(selected),
            "baseline_objective_fraction": contribution / denominator,
        }
    return result


def resolve_selected_candidates(
    selected_ids: list[str],
    candidate_universe: dict[str, Any],
    native_repository: dict[str, Any],
    singleton_profile: dict[str, Any],
    recommendation: dict[str, Any],
    relation: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    native = {item["candidate_id"]: item for item in native_repository["candidates"]}
    profiles = {item["candidate_id"]: item for item in singleton_profile["candidate_profiles"]}
    recommendations = {item["candidate_id"]: item for item in recommendation["selected_candidates"]}
    result = []
    for candidate_id in selected_ids:
        try:
            candidate = candidates[candidate_id]
            native_candidate = native[candidate_id]
            profile = profiles[candidate_id]
            selected = recommendations[candidate_id]
        except KeyError as exc:
            raise ValueError(
                f"selected candidate is missing from a validated artifact: {candidate_id}"
            ) from exc
        result.append(
            {
                "candidate_id": candidate_id,
                "kind": candidate["kind"],
                "relation": {"relation_id": candidate["relation_id"], **relation},
                "column_ordinals": candidate["column_ordinals"],
                "column_names": candidate["column_names"],
                "native_state": native_candidate["state"],
                "static_precedence_rank": candidate["static_precedence_rank"],
                "frozen_precedence_rank": profile["frozen_precedence_rank"],
                "singleton_objective": profile["singleton_objective"],
                "singleton_improvement": profile["improvement"],
                "statistics_target": selected["statistics_target"],
                "recommendation_object_name": selected["statistics_object"]["name"],
                "recommendation_deployment_order_position": selected["deployment_order_position"],
            }
        )
    return result


def singleton_landscape(
    singleton_profile: dict[str, Any], candidate_universe: dict[str, Any], plan: dict[str, Any]
) -> dict[str, Any]:
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    profiles = {item["candidate_id"]: item for item in singleton_profile["candidate_profiles"]}
    present = [
        item
        for item in singleton_profile["candidate_profiles"]
        if item["native_state"] == "present"
    ]
    landscape = {
        "present_candidate_count": len(present),
        "improving_singleton_count": sum(item["improvement"] > 0 for item in present),
        "neutral_singleton_count": sum(item["improvement"] == 0 for item in present),
        "worsening_singleton_count": sum(item["improvement"] < 0 for item in present),
    }
    screened = []
    for item in plan["screened_candidates"]:
        candidate_id = item["candidate_id"]
        candidate = candidates[candidate_id]
        profile = profiles[candidate_id]
        screened.append(
            {
                "frozen_precedence_rank": profile["frozen_precedence_rank"],
                "candidate_id": candidate_id,
                "kind": candidate["kind"],
                "columns": candidate["column_names"],
                "singleton_objective": profile["singleton_objective"],
                "singleton_improvement": profile["improvement"],
            }
        )
    landscape["top_screened_candidates"] = screened
    return landscape


def search_trace(plan: dict[str, Any], search: dict[str, Any]) -> dict[str, Any]:
    runtime = search["runtime_metadata"]
    accepted = search["accepted_moves"]
    first_round = search["first_round_evaluations"]
    completed_rounds = search["completed_rounds"]
    rounds = []
    if accepted:
        move = accepted[0]
        rounds.append(
            {
                "round": move["round_index"],
                "source": search["first_round_source"],
                "chosen_candidate": move["added_candidate_id"],
                "objective_before": move["objective_before"],
                "objective_after": move["objective_after"],
                "improvement": move["improvement"],
                "candidate_configurations_evaluated": len(first_round),
            }
        )
    for move, completed in zip(accepted[1:], completed_rounds):
        rounds.append(
            {
                "round": move["round_index"],
                "source": "live-planner",
                "chosen_candidate": move["added_candidate_id"],
                "objective_before": move["objective_before"],
                "objective_after": move["objective_after"],
                "improvement": move["improvement"],
                "candidate_configurations_evaluated": len(completed["evaluations"]),
            }
        )
    final_ids = set(search["final_ordered_candidate_ids"])
    remaining = [
        candidate_id
        for candidate_id in plan["screened_candidate_ids"]
        if candidate_id not in final_ids
    ]
    partial = runtime.get("partial_final_round_evaluation_count")
    attempted = {
        "round": (rounds[-1]["round"] + 1) if rounds else 1,
        "remaining_candidates": remaining,
        "evaluations_completed_before_budget_expiry": partial,
        "evaluations_not_completed": (
            len(remaining) - partial if isinstance(partial, int) else None
        ),
        "candidate_ids_recorded_for_partial_evaluations": [],
    }
    elapsed = runtime.get("elapsed_search_seconds")
    live = runtime.get("live_configuration_evaluation_count")
    return {
        "baseline_objective": search["baseline_objective"],
        "rounds": rounds,
        "attempted_final_round": attempted,
        "completed_round_count": runtime.get("completed_round_count", len(completed_rounds)),
        "cached_singleton_configuration_count": runtime.get("cached_singleton_configuration_count"),
        "live_configuration_evaluation_count": live,
        "planner_query_estimate_count": runtime.get("planner_query_estimate_count"),
        "elapsed_search_seconds": elapsed,
        "wall_clock_budget_seconds": plan["budget"]["wall_clock_seconds"],
        "termination_reason": search["termination_reason"],
        "average_seconds_per_live_configuration": elapsed / live if elapsed and live else None,
    }


def _source_artifact_digests(run_directory: Path, manifest: dict[str, Any]) -> dict[str, str]:
    result = {}
    for name, metadata in manifest["artifacts"].items():
        path = run_directory / metadata["path"]
        source = path / "manifest.json" if path.is_dir() else path
        value = read_json(source)
        actual = value.get("semantic_digest")
        if actual != metadata["semantic_digest"]:
            raise ValueError(f"source artifact digest mismatch: {name}")
        result[name] = actual
    return result


def run_audit(
    run_directory: Path,
    *,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output_directory: Path | None = None,
) -> dict[str, Any]:
    """Replay baseline/final configurations against one immutable completed run."""
    run_directory = run_directory.expanduser().resolve()
    manifest = read_json(run_directory / "manifest.json")
    if manifest.get("run_id") != run_directory.name or manifest.get("status") != "complete":
        raise ValueError("audit source must be one completed canonical run")
    current_research = verify_research_repository(Path(__file__).resolve().parents[2])
    systems = verify_frozen_systems(advisor_root, patched_postgres_root)
    for key in ("advisor_commit_sha", "patched_postgres_commit_sha"):
        if manifest.get(key) != systems[key]:
            raise ValueError(f"source run {key} does not match the frozen system")
    source_digests = _source_artifact_digests(run_directory, manifest)
    output_directory = (
        (run_directory / "analysis") if output_directory is None else output_directory
    )
    output_directory = output_directory.expanduser().resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    audit_path = output_directory / "audit-v1.json"
    per_query_path = output_directory / "per-query-v1.jsonl"
    if audit_path.exists() or per_query_path.exists():
        raise FileExistsError(f"audit output already exists: {output_directory}")

    snapshot_path = run_directory / manifest["artifacts"]["snapshot"]["path"]
    candidate_path = run_directory / manifest["artifacts"]["candidate_universe"]["path"]
    native_path = run_directory / manifest["artifacts"]["native_repository"]["path"]
    ground_truth_path = run_directory / manifest["artifacts"]["ground_truth"]["path"]
    profile = read_json(run_directory / manifest["artifacts"]["singleton_profile"]["path"])
    plan = read_json(run_directory / manifest["artifacts"]["optimization_plan"]["path"])
    search = read_json(run_directory / manifest["artifacts"]["search_result"]["path"])
    recommendation = read_json(run_directory / manifest["artifacts"]["recommendation"]["path"])
    candidate_universe_json = read_json(candidate_path)
    native_repository_json = read_json(native_path / "manifest.json")

    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import (
        PostgresPlannerSession,
        PostgresStatisticsConfiguration,
    )
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.ground_truth.artifact import load_ground_truth_set
    from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
    from extstats_advisor.native_stats.repository import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.utility import QErrorLoss, WeightedWorkloadUtility

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    repository = load_native_stats_repository(native_path)
    ground_truth = load_ground_truth_set(ground_truth_path, snapshot)
    relation = snapshot.schemas[0].relation_name.to_dict()
    selected_ids = list(search["final_ordered_candidate_ids"])
    selected = resolve_selected_candidates(
        selected_ids,
        candidate_universe_json,
        native_repository_json,
        profile,
        recommendation,
        relation,
    )
    utility = WeightedWorkloadUtility(
        snapshot.workload,
        ArtifactGroundTruthProvider(ground_truth),
        QErrorLoss(),
    )
    query_ids = [query.query_id for query in snapshot.workload.queries if query.weight > 0]
    prepared = False
    try:
        prepared_metadata = prepare_postgres_planner_sandbox(
            planner_dsn, snapshot, universe, repository
        ).metadata.to_dict()
        prepared = True
        verified_metadata = verify_postgres_planner_sandbox(
            planner_dsn, snapshot, universe, repository
        )
        with PostgresPlannerSession(planner_dsn, snapshot, universe, repository) as session:
            session.activate(PostgresStatisticsConfiguration(()))
            baseline_estimates = {
                item.query_id: item.estimated_rows for item in session.estimate_queries(query_ids)
            }
            session.activate(PostgresStatisticsConfiguration(tuple(selected_ids)))
            final_estimates = {
                item.query_id: item.estimated_rows for item in session.estimate_queries(query_ids)
            }
        baseline_result = utility.evaluate(baseline_estimates)
        final_result = utility.evaluate(final_estimates)
    finally:
        if prepared:
            destroy_postgres_planner_sandbox(planner_dsn)

    records = build_per_query_records(baseline_result, final_result)
    recomputed_baseline = weighted_objective(records, "baseline_qerror")
    recomputed_final = weighted_objective(records, "final_qerror")
    expected_baseline = search["baseline_objective"]
    expected_final = search["final_objective"]
    reproduction = {
        "tolerance": OBJECTIVE_TOLERANCE,
        "baseline": {
            "expected": expected_baseline,
            "recomputed": recomputed_baseline,
            "absolute_difference": abs(expected_baseline - recomputed_baseline),
            "matches": math.isclose(
                expected_baseline,
                recomputed_baseline,
                rel_tol=OBJECTIVE_TOLERANCE,
                abs_tol=OBJECTIVE_TOLERANCE,
            ),
        },
        "final": {
            "expected": expected_final,
            "recomputed": recomputed_final,
            "absolute_difference": abs(expected_final - recomputed_final),
            "matches": math.isclose(
                expected_final,
                recomputed_final,
                rel_tol=OBJECTIVE_TOLERANCE,
                abs_tol=OBJECTIVE_TOLERANCE,
            ),
        },
    }
    if not reproduction["baseline"]["matches"] or not reproduction["final"]["matches"]:
        raise ValueError("per-query audit does not reproduce the validated objectives")

    incidence = {}
    for item in candidate_universe_json["incidence"]:
        incidence.setdefault(item["query_id"], []).append(item["candidate_id"])
    profile_by_query = {
        item["query_id"]: item for item in candidate_universe_json["query_profiles"]
    }
    for record in records:
        query_profile = profile_by_query[record["query_id"]]
        record["predicate_arity"] = len(query_profile["predicate_column_ordinals"])
        record["selected_candidate_ids_touching_query"] = [
            candidate_id
            for candidate_id in selected_ids
            if candidate_id in incidence.get(record["query_id"], [])
        ]
    classification_counts = Counter(record["classification"] for record in records)
    top_baseline = _tail(records, "baseline_qerror", True)
    top_final = _tail(records, "final_qerror", True)
    top_improvements = _tail(records, "absolute_qerror_change", True)
    top_regressions = _tail(records, "absolute_qerror_change", False)
    distributions = {
        "baseline": distribution(records, "baseline_qerror", recomputed_baseline),
        "final": distribution(records, "final_qerror", recomputed_final),
    }
    arity_distribution = Counter(str(record["predicate_arity"]) for record in records)
    audit = {
        "format": AUDIT_FORMAT_VERSION,
        "run_id": manifest["run_id"],
        "source_run_research_commit_sha": manifest["research_commit_sha"],
        "audit_implementation_research_sha": current_research["research_commit_sha"],
        "advisor_commit_sha": systems["advisor_commit_sha"],
        "patched_postgres_commit_sha": systems["patched_postgres_commit_sha"],
        "artifact_digests": source_digests,
        "qerror_contract": QERROR_CONTRACT_VERSION,
        "selected_candidates": selected,
        "singleton_landscape": singleton_landscape(profile, candidate_universe_json, plan),
        "search_trace": search_trace(plan, search),
        "aggregate_reproduction": reproduction,
        "distributions": distributions,
        "classification": {
            "comparison": "exact-float-comparison",
            "counts": dict(sorted(classification_counts.items())),
            "fractions": {
                label: count / len(records)
                for label, count in sorted(classification_counts.items())
            },
        },
        "tail_queries": {
            "top_10_baseline_qerror": top_baseline,
            "top_10_final_qerror": top_final,
            "top_10_largest_absolute_improvements": top_improvements,
            "top_10_largest_regressions": top_regressions,
        },
        "baseline_mean_contribution": contribution_summary(records),
        "final_mean_contribution": contribution_summary(records, "final_qerror"),
        "predicate_diagnostics": {
            "query_count": len(records),
            "predicate_arity_distribution": dict(sorted(arity_distribution.items())),
            "top_baseline_queries_selected_candidate_coverage": [
                {
                    **item,
                    "selected_candidate_ids_touching_query": next(
                        record["selected_candidate_ids_touching_query"]
                        for record in records
                        if record["query_id"] == item["query_id"]
                    ),
                }
                for item in top_baseline
            ],
        },
        "budget_diagnostics": {
            "singleton_profiling_candidate_count": len(profile["candidate_profiles"]),
            "singleton_profiling_planner_configuration_count": profile["runtime_metadata"][
                "singleton_configuration_count"
            ],
            "singleton_profiling_planner_query_estimate_count": profile["runtime_metadata"][
                "planner_query_estimate_count"
            ],
            "search_cached_singleton_evaluations": search["runtime_metadata"][
                "cached_singleton_configuration_count"
            ],
            "search_live_configuration_evaluations": search["runtime_metadata"][
                "live_configuration_evaluation_count"
            ],
            "search_planner_query_estimate_count": search["runtime_metadata"][
                "planner_query_estimate_count"
            ],
            "search_wall_clock_budget_seconds": plan["budget"]["wall_clock_seconds"],
            "search_elapsed_seconds": search["runtime_metadata"]["elapsed_search_seconds"],
            "termination_reason": search["termination_reason"],
        },
        "planner_replay": {
            "sandbox_prepare": prepared_metadata,
            "sandbox_verify": verified_metadata,
            "baseline_configuration": [],
            "final_configuration": selected_ids,
            "replay_query_count": len(records),
            "physical_deployment_performed": False,
        },
        "per_query_path": per_query_path.name,
    }
    with per_query_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    audit["per_query_sha256"] = sha256_file(per_query_path)
    reject_credentials(audit)
    audit["semantic_digest"] = semantic_digest(audit)
    write_json(audit_path, audit)
    return {
        "run_id": manifest["run_id"],
        "audit_path": str(audit_path),
        "per_query_path": str(per_query_path),
        "semantic_digest": audit["semantic_digest"],
        "record_count": len(records),
        "aggregate_reproduction": reproduction,
    }
