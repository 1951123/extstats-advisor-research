"""Single-point screening diagnostic extending the frozen K=12 result to K=16."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_REPOSITORY,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
    TRANSFER_SOURCE_ADVISOR_SHA,
)
from .pins import verify_frozen_systems, verify_research_repository
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .screening_k12 import (
    EXPECTED_SOURCE_DIGESTS,
    K8_OBJECTIVE,
    K8_SELECTED,
    SOURCE_RESEARCH_SHA,
    SOURCE_RUN_ID,
    _compact_tree_is_empty,
    validate_k8_reference,
    validate_source_binding,
)

FORMAT_VERSION = "arecel-screening-k16-v1"
K12_ARTIFACT_DIGEST = "48081899ecf127e836144ae41ee293dd19e6ab94ed1690682ec1ac5633a48302"
K12_PLAN_DIGEST = "a99c10e7209ee1e6c9b0c369cd3ebbf16e2bb30b0e2b9b980a9acb633b850ba7"
K12_SEARCH_DIGEST = "c6ec3de5520b5bc2e17df1f8e8f3db09c5d83570285a4b1cdbe2b532737f25d5"
K12_OBJECTIVE = 2.3493124105613443
K12_SELECTED = (
    "cand_cb55f02882e2d0ef75203787",
    "cand_ffc5c63805d1be782d2b4a02",
    "cand_8a4f08da78a4aa33ad1a459b",
    "cand_ae97bada52eb7042e5fc00b8",
    "cand_d339256e88c14460f89f50b8",
    "cand_7ac24ff8a73a964272444efd",
    "cand_384c338346aee8994f53b6e6",
    "cand_d68ee3835dfcaee105f14cf6",
    "cand_7211142c6035d2eac65ef212",
    "cand_2c9419ea6e50d1a0b1b5b706",
)
BASELINE_OBJECTIVE = 5.256435607220752
K16_CANDIDATE_LIMIT = 16
K16_WALL_CLOCK_SECONDS = 900
COMPLETED_TERMINATIONS = frozenset({"local-optimum", "all-screened-candidates-selected"})
OBJECTIVE_TOLERANCE = 1e-12


def validate_completed_termination(reason: str) -> bool:
    if reason not in COMPLETED_TERMINATIONS:
        raise ValueError(f"K=16 search did not reach a completed termination: {reason}")
    return True


def validate_k12_reference(repository_root: Path) -> dict[str, Any]:
    directory = repository_root / "experiments/arecel-census13/screening-k12"
    artifact = read_json(directory / "screening-k12-v1.json")
    if artifact.get("semantic_digest") != K12_ARTIFACT_DIGEST:
        raise ValueError("frozen K=12 screening artifact digest mismatch")
    screening = artifact.get("screening", {})
    if screening.get("candidate_limit") != 12:
        raise ValueError("frozen K=12 screening candidate limit mismatch")
    if screening.get("k12_plan_digest") != K12_PLAN_DIGEST:
        raise ValueError("frozen K=12 plan digest mismatch")
    if screening.get("k12_search_digest") != K12_SEARCH_DIGEST:
        raise ValueError("frozen K=12 search digest mismatch")
    if float(screening.get("k12_objective")) != K12_OBJECTIVE:
        raise ValueError("frozen K=12 objective mismatch")
    if screening.get("k12_selected_candidate_ids") != list(K12_SELECTED):
        raise ValueError("frozen K=12 membership mismatch")
    if screening.get("termination_reason") not in COMPLETED_TERMINATIONS:
        raise ValueError("frozen K=12 SearchResult is not completed")
    source = artifact.get("source", {})
    if source.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("frozen K=12 source RunID mismatch")
    if source.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("frozen K=12 source research SHA mismatch")
    if source.get("advisor_commit_sha") != TRANSFER_SOURCE_ADVISOR_SHA:
        raise ValueError("frozen K=12 source advisor SHA mismatch")
    if source.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("frozen K=12 source PostgreSQL SHA mismatch")
    if source.get("artifact_digests") != EXPECTED_SOURCE_DIGESTS:
        raise ValueError("frozen K=12 source artifact digests mismatch")
    return {
        "artifact_digest": K12_ARTIFACT_DIGEST,
        "plan_digest": K12_PLAN_DIGEST,
        "search_digest": K12_SEARCH_DIGEST,
        "objective": K12_OBJECTIVE,
        "selected_candidate_ids": list(K12_SELECTED),
        "selected_count": len(K12_SELECTED),
        "termination_reason": screening["termination_reason"],
    }


def classify_screening(
    k12_objective: float,
    k16_objective: float,
    k12_membership: list[str] | tuple[str, ...],
    k16_membership: list[str] | tuple[str, ...],
    *,
    termination_reason: str,
    tolerance: float = OBJECTIVE_TOLERANCE,
) -> dict[str, Any]:
    objective_delta = float(k12_objective) - float(k16_objective)
    relative = objective_delta / float(k12_objective)
    objective_equal = abs(objective_delta) <= tolerance
    same_membership = tuple(k12_membership) == tuple(k16_membership)
    complete = termination_reason in COMPLETED_TERMINATIONS
    if not complete:
        decision = "k16-incomplete"
    elif objective_equal and same_membership:
        decision = "k12-sufficient-within-k16"
    else:
        decision = "k12-screen-limited"
    return {
        "decision": decision,
        "k12_objective": float(k12_objective),
        "k16_objective": float(k16_objective),
        "absolute_improvement": objective_delta,
        "relative_improvement": relative,
        "objective_equal_within_tolerance": objective_equal,
        "objective_tolerance": tolerance,
        "same_membership": same_membership,
        "k12_only_selected_ids": sorted(set(k12_membership) - set(k16_membership)),
        "k16_only_selected_ids": sorted(set(k16_membership) - set(k12_membership)),
    }


def membership_comparison(
    k8_membership: list[str] | tuple[str, ...],
    k12_membership: list[str] | tuple[str, ...],
    k16_membership: list[str] | tuple[str, ...],
) -> dict[str, list[str]]:
    k8, k12, k16 = map(set, (k8_membership, k12_membership, k16_membership))
    return {
        "k8_intersection_k12": sorted(k8 & k12),
        "k12_intersection_k16": sorted(k12 & k16),
        "k8_only": sorted(k8 - k12),
        "k12_only_vs_k8": sorted(k12 - k8),
        "k16_only_vs_k12": sorted(k16 - k12),
        "k8_selected_not_k16": sorted(k8 - k16),
        "k12_selected_not_k16": sorted(k12 - k16),
    }


def parsimony_metrics(
    baseline_objective: float,
    k8_objective: float,
    k12_objective: float,
    k16_objective: float,
    k8_count: int,
    k12_count: int,
    k16_count: int,
) -> dict[str, Any]:
    k8_to_k12_count = k12_count - k8_count
    k12_to_k16_count = k16_count - k12_count
    k8_to_k12_gain = float(k8_objective) - float(k12_objective)
    k12_to_k16_gain = float(k12_objective) - float(k16_objective)
    total_gain = float(baseline_objective) - float(k16_objective)
    return {
        "additional_objects_k8_to_k12": k8_to_k12_count,
        "additional_objects_k12_to_k16": k12_to_k16_count,
        "objective_gain_k8_to_k12": k8_to_k12_gain,
        "objective_gain_k12_to_k16": k12_to_k16_gain,
        "objective_gain_per_added_object_k8_to_k12": (
            k8_to_k12_gain / k8_to_k12_count if k8_to_k12_count else None
        ),
        "objective_gain_per_added_object_k12_to_k16": (
            k12_to_k16_gain / k12_to_k16_count if k12_to_k16_count else None
        ),
        "r8_fraction_of_k16_total_improvement": (
            (float(baseline_objective) - float(k8_objective)) / total_gain if total_gain else None
        ),
        "r12_fraction_of_k16_total_improvement": (
            (float(baseline_objective) - float(k12_objective)) / total_gain if total_gain else None
        ),
    }


def accepted_move_curve(search_result: dict[str, Any]) -> list[dict[str, Any]]:
    baseline = float(search_result["baseline_objective"])
    curve = [
        {
            "selected_object_count": 0,
            "objective": baseline,
            "cumulative_improvement_from_baseline": 0.0,
            "marginal_improvement_of_latest_accepted_object": None,
        }
    ]
    for move in search_result["accepted_moves"]:
        curve.append(
            {
                "selected_object_count": len(curve),
                "objective": move["objective_after"],
                "cumulative_improvement_from_baseline": baseline - move["objective_after"],
                "marginal_improvement_of_latest_accepted_object": move["improvement"],
            }
        )
    return curve


def rank_definitions(
    candidate_universe: dict[str, Any],
    singleton_profile: dict[str, Any],
    native_repository: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    native = {item["candidate_id"]: item for item in native_repository["candidates"]}
    result = []
    for profile in singleton_profile["candidate_profiles"]:
        rank = profile.get("frozen_precedence_rank")
        if rank is None or not 13 <= rank <= 16:
            continue
        candidate = candidates[profile["candidate_id"]]
        result.append(
            {
                "candidate_id": profile["candidate_id"],
                "frozen_singleton_rank": rank,
                "kind": candidate["kind"],
                "column_names": candidate["column_names"],
                "native_state": native[profile["candidate_id"]]["state"],
                "singleton_objective": profile["singleton_objective"],
                "singleton_improvement": profile["improvement"],
            }
        )
    return sorted(result, key=lambda item: item["frozen_singleton_rank"])


def rank_outcomes(
    definitions: list[dict[str, Any]], search_result: dict[str, Any]
) -> list[dict[str, Any]]:
    live_rounds = {
        evaluation["candidate_id"]: round_["round_index"]
        for round_ in search_result["completed_rounds"]
        for evaluation in round_["evaluations"]
    }
    accepted_ids = {move["added_candidate_id"] for move in search_result["accepted_moves"]}
    outcomes = []
    for definition in definitions:
        candidate_id = definition["candidate_id"]
        accepted = candidate_id in accepted_ids
        live = candidate_id in live_rounds
        outcomes.append(
            {
                **definition,
                "live_evaluated": live,
                "evaluated_live": live,
                "accepted": accepted,
                "outcome": "accepted"
                if accepted
                else (
                    "rejected-in-completed-round"
                    if live
                    else "never-reached-because-search-terminated-earlier"
                ),
                "rejection_completed_round": live_rounds.get(candidate_id)
                if not accepted
                else None,
            }
        )
    return outcomes


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"frozen advisor command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr}"
        )


def _all_definitions(
    candidate_universe: dict[str, Any],
    singleton_profile: dict[str, Any],
    native_repository: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    profiles = {item["candidate_id"]: item for item in singleton_profile["candidate_profiles"]}
    native = {item["candidate_id"]: item for item in native_repository["candidates"]}
    return [
        {
            "candidate_id": candidate_id,
            "frozen_singleton_rank": profile.get("frozen_precedence_rank"),
            "kind": candidates[candidate_id]["kind"],
            "column_names": candidates[candidate_id]["column_names"],
            "native_state": native[candidate_id]["state"],
            "singleton_objective": profile["singleton_objective"],
            "singleton_improvement": profile["improvement"],
        }
        for candidate_id, profile in profiles.items()
        if candidate_id in candidates
    ]


def _build_trace(search: dict[str, Any], definitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {item["candidate_id"]: item for item in definitions}
    trace = []
    for move in search["accepted_moves"]:
        item = by_id[move["added_candidate_id"]]
        trace.append(
            {
                "round": move["round_index"],
                "candidate_id": move["added_candidate_id"],
                "frozen_singleton_rank": item["frozen_singleton_rank"],
                "kind": item["kind"],
                "column_names": item["column_names"],
                "objective_before": move["objective_before"],
                "objective_after": move["objective_after"],
                "marginal_improvement": move["improvement"],
                "singleton_improvement": item["singleton_improvement"],
            }
        )
    return trace


def _finalize_screening_k16(
    *,
    repository_root: Path,
    source: dict[str, Any],
    k8: dict[str, Any],
    k12: dict[str, Any],
    research_identity: dict[str, str],
    output: Path,
    paths: dict[str, Path],
    plan: dict[str, Any],
    search: dict[str, Any],
) -> dict[str, Any]:
    termination = search["termination_reason"]
    candidate_universe = read_json(paths["candidate_universe"])
    singleton_profile = read_json(paths["singleton_profile"])
    native_repository_manifest = read_json(paths["native_repository"] / "manifest.json")
    definitions = rank_definitions(
        candidate_universe, singleton_profile, native_repository_manifest
    )
    all_definitions = _all_definitions(
        candidate_universe, singleton_profile, native_repository_manifest
    )
    outcomes = rank_outcomes(definitions, search)
    trace = _build_trace(search, all_definitions)
    comparison = classify_screening(
        K12_OBJECTIVE,
        search["final_objective"],
        K12_SELECTED,
        search["final_ordered_candidate_ids"],
        termination_reason=termination,
    )
    runtime = search["runtime_metadata"]
    k16_objective = float(search["final_objective"])
    artifact = {
        "format_version": FORMAT_VERSION,
        "source": {
            "run_id": source["run_id"],
            "research_commit_sha": source["source_research_sha"],
            "advisor_commit_sha": source["source_advisor_sha"],
            "patched_postgres_commit_sha": source["patched_postgres_sha"],
            "artifact_digests": source["source_artifact_digests"],
        },
        "execution_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "research_commit_sha": research_identity["research_commit_sha"],
            "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        },
        "screening": {
            "candidate_limit": K16_CANDIDATE_LIMIT,
            "wall_clock_cap_seconds": K16_WALL_CLOCK_SECONDS,
            "baseline_objective": BASELINE_OBJECTIVE,
            "k8_reference": k8,
            "k12_reference": k12,
            "k16_plan_digest": plan["semantic_digest"],
            "k16_search_digest": search["semantic_digest"],
            "k16_objective": k16_objective,
            "k16_selected_candidate_ids": search["final_ordered_candidate_ids"],
            "termination_reason": termination,
            **comparison,
            "baseline_relative_improvement": {
                "k8": (BASELINE_OBJECTIVE - K8_OBJECTIVE) / BASELINE_OBJECTIVE,
                "k12": (BASELINE_OBJECTIVE - K12_OBJECTIVE) / BASELINE_OBJECTIVE,
                "k16": (BASELINE_OBJECTIVE - k16_objective) / BASELINE_OBJECTIVE,
            },
            "membership_comparison": membership_comparison(
                K8_SELECTED, K12_SELECTED, search["final_ordered_candidate_ids"]
            ),
            "parsimony": parsimony_metrics(
                BASELINE_OBJECTIVE,
                K8_OBJECTIVE,
                K12_OBJECTIVE,
                k16_objective,
                len(K8_SELECTED),
                len(K12_SELECTED),
                len(search["final_ordered_candidate_ids"]),
            ),
        },
        "singleton_ranks_13_16": outcomes,
        "accepted_move_trace": trace,
        "accepted_move_curve": accepted_move_curve(search),
        "runtime_metadata": {
            "elapsed_search_seconds": runtime["elapsed_search_seconds"],
            "cached_singleton_configuration_count": runtime["cached_singleton_configuration_count"],
            "live_configuration_evaluation_count": runtime["live_configuration_evaluation_count"],
            "planner_query_estimate_count": runtime["planner_query_estimate_count"],
            "completed_round_count": runtime["completed_round_count"],
            "partial_final_round_evaluation_count": runtime["partial_final_round_evaluation_count"],
            "seconds_per_live_configuration": runtime["elapsed_search_seconds"]
            / runtime["live_configuration_evaluation_count"],
        },
        "planner_sandbox": {
            "contract": "postgresql-planner-sandbox-v1",
            "sample_row_count": 10_000,
            "population_metadata_preserved": True,
            "physical_extstats_count_before_search": 0,
        },
        "recommendation_or_deployment": {
            "recommendation_built": False,
            "deployment_performed": False,
        },
        "credentials_recorded": False,
    }
    validate_artifact_source_binding(artifact)
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output / "screening-k16-v1.json", artifact)
    return {
        "output_directory": str(output),
        "plan_digest": plan["semantic_digest"],
        "search_digest": search["semantic_digest"],
        "decision": comparison["decision"],
        "termination_reason": termination,
        "query_estimates": runtime["planner_query_estimate_count"],
    }


def run_screening_k16(
    source_run: Path,
    planner_dsn: str,
    *,
    output_directory: Path | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    if not planner_dsn.strip():
        raise ValueError("planner DSN is required and never written to an artifact")
    repository_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(repository_root)
    systems = verify_frozen_systems(advisor_root, patched_postgres_root)
    if systems["advisor_commit_sha"] != FROZEN_ADVISOR_SHA:
        raise ValueError("execution advisor is not the frozen current advisor")
    if systems["patched_postgres_commit_sha"] != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("execution PostgreSQL is not the frozen patched source")

    source = validate_source_binding(source_run.resolve())
    k8 = validate_k8_reference(repository_root)
    k12 = validate_k12_reference(repository_root)
    output = (
        output_directory or repository_root / "experiments/arecel-census13/screening-k16"
    ).resolve()
    _compact_tree_is_empty(output)
    output.mkdir(parents=True, exist_ok=True)

    paths = source["paths"]
    singleton_path = paths["singleton_profile"]
    plan_path = output / "optimization-plan.json"
    search_path = output / "search-result.json"
    _run(
        [
            advisor_command,
            "optimization",
            "plan",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            str(singleton_path),
            "--candidate-limit",
            str(K16_CANDIDATE_LIMIT),
            "--wall-clock-seconds",
            str(K16_WALL_CLOCK_SECONDS),
            "--output",
            str(plan_path),
        ]
    )
    _run(
        [
            advisor_command,
            "optimization",
            "validate",
            str(plan_path),
            "--snapshot",
            str(paths["snapshot"]),
            "--candidate-universe",
            str(paths["candidate_universe"]),
            "--native-repository",
            str(paths["native_repository"]),
            "--ground-truth",
            str(paths["ground_truth"]),
            "--singleton-profile",
            str(singleton_path),
        ]
    )
    plan = read_json(plan_path)
    if plan["budget"]["candidate_limit"] != K16_CANDIDATE_LIMIT:
        raise ValueError("K=16 OptimizationPlan candidate limit mismatch")
    if float(plan["budget"]["wall_clock_seconds"]) != K16_WALL_CLOCK_SECONDS:
        raise ValueError("K=16 OptimizationPlan wall-clock cap mismatch")

    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.native_stats.repository import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(paths["snapshot"])
    universe = load_candidate_universe(paths["candidate_universe"], snapshot)
    repository = load_native_stats_repository(paths["native_repository"])
    prepared = False
    try:
        prepare_postgres_planner_sandbox(planner_dsn, snapshot, universe, repository)
        prepared = True
        verification = verify_postgres_planner_sandbox(planner_dsn, snapshot, universe, repository)
        if verification["sandbox_contract"] != "postgresql-planner-sandbox-v1":
            raise ValueError("unexpected planner sandbox contract")
        if verification["frozen_sample_row_count"] != 10_000:
            raise ValueError("planner sandbox sample row count is not 10000")
        if verification["physical_extstats_count"] != 0:
            raise ValueError("planner sandbox contains physical extended statistics")
        _run(
            [
                advisor_command,
                "optimization",
                "search",
                "postgres",
                str(paths["snapshot"]),
                str(paths["candidate_universe"]),
                str(paths["native_repository"]),
                str(paths["ground_truth"]),
                str(singleton_path),
                str(plan_path),
                "--dsn",
                planner_dsn,
                "--output",
                str(search_path),
            ]
        )
        _run(
            [
                advisor_command,
                "optimization",
                "search",
                "validate",
                str(search_path),
                "--snapshot",
                str(paths["snapshot"]),
                "--candidate-universe",
                str(paths["candidate_universe"]),
                "--native-repository",
                str(paths["native_repository"]),
                "--ground-truth",
                str(paths["ground_truth"]),
                "--singleton-profile",
                str(singleton_path),
                "--optimization-plan",
                str(plan_path),
            ]
        )
    finally:
        if prepared:
            destroy_postgres_planner_sandbox(planner_dsn)

    return _finalize_screening_k16(
        repository_root=repository_root,
        source=source,
        k8=k8,
        k12=k12,
        research_identity=research_identity,
        output=output,
        paths=paths,
        plan=plan,
        search=read_json(search_path),
    )


def validate_artifact_source_binding(artifact: dict[str, Any]) -> bool:
    source = artifact.get("source", {})
    execution = artifact.get("execution_system", {})
    if source.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("screening artifact source RunID is not frozen")
    if source.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("screening artifact source research SHA is not frozen")
    if source.get("advisor_commit_sha") != TRANSFER_SOURCE_ADVISOR_SHA:
        raise ValueError("screening artifact source advisor SHA is not frozen")
    if source.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("screening artifact source PostgreSQL SHA is not frozen")
    if source.get("artifact_digests") != EXPECTED_SOURCE_DIGESTS:
        raise ValueError("screening artifact source digests are not frozen")
    if execution.get("research_repository") != FROZEN_RESEARCH_REPOSITORY:
        raise ValueError("screening artifact execution research repository is invalid")
    if execution.get("advisor_commit_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("screening artifact execution advisor SHA is not frozen")
    if execution.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("screening artifact execution PostgreSQL SHA is not frozen")
    return True
