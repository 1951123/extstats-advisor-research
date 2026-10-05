"""Screening-sufficiency diagnostic for extending the frozen K=8 screen to K=12."""

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

FORMAT_VERSION = "arecel-screening-k12-v1"
SOURCE_RUN_ID = "bf7fda28d90b3da88e7a14e4"
SOURCE_RESEARCH_SHA = "9de794eaa62389c48ee6c5cebb192a467b9696cc"
K8_PLAN_DIGEST = "dc0ffdcab66942888be063dea9540ce3a87a9e431c8f0830da3e6ff1f4a15963"
K8_SEARCH_DIGEST = "ad77fafbd3b9f1c1ffae62dfa20d987c02a4636dda7ae6865d35921d4172bd6b"
K8_OBJECTIVE = 2.3829607477239465
K8_SELECTED = (
    "cand_cb55f02882e2d0ef75203787",
    "cand_ffc5c63805d1be782d2b4a02",
    "cand_8a4f08da78a4aa33ad1a459b",
    "cand_ae97bada52eb7042e5fc00b8",
    "cand_d339256e88c14460f89f50b8",
    "cand_7ac24ff8a73a964272444efd",
    "cand_384c338346aee8994f53b6e6",
)
EXPECTED_SOURCE_DIGESTS = {
    "snapshot": "7843ccf2e1ae0b017fda3c5a8e97797118b8534bdd97d854ed1ef1ca9631568d",
    "ground_truth": "b70410c12af2b7e52d3d3b9972ab086a4270c2743c4363f58ed400a3bdb9d491",
    "candidate_universe": "8912122ddc7cff7b4047b470de13edf89ecc8dc5237107c25aec7c5d8ac86b74",
    "native_repository": "787483dabec6816123566574b1a4ab8d68d930cc2f1c4e4cd543241fb764ed62",
    "singleton_profile": "2665d5ff54331bc768bb4661554b3ed763cf0f445545ac2ed5e5169657c0aa78",
}
K12_CANDIDATE_LIMIT = 12
K12_WALL_CLOCK_SECONDS = 600
COMPLETED_TERMINATIONS = frozenset({"local-optimum", "all-screened-candidates-selected"})
OBJECTIVE_TOLERANCE = 1e-12


def validate_completed_termination(reason: str) -> bool:
    if reason not in COMPLETED_TERMINATIONS:
        raise ValueError(f"K=12 search did not reach a completed termination: {reason}")
    return True


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


def _source_paths(source_run: Path, manifest: dict[str, Any]) -> dict[str, Path]:
    return {
        name: source_run / manifest["artifacts"][name]["path"] for name in EXPECTED_SOURCE_DIGESTS
    }


def validate_source_binding(source_run: Path) -> dict[str, Any]:
    manifest = read_json(source_run / "manifest.json")
    if source_run.name != SOURCE_RUN_ID or manifest.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("screening source must be the corrected canonical RunID")
    if manifest.get("status") != "complete":
        raise ValueError("screening source RunManifest is not complete")
    if manifest.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("screening source research SHA is not the corrected canonical SHA")
    if manifest.get("advisor_commit_sha") != TRANSFER_SOURCE_ADVISOR_SHA:
        raise ValueError("screening source advisor SHA is not the immutable source advisor")
    if manifest.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("screening source PostgreSQL SHA is not the frozen patched SHA")

    paths = _source_paths(source_run, manifest)
    digests: dict[str, str] = {}
    for name, expected in EXPECTED_SOURCE_DIGESTS.items():
        artifact = paths[name] / "manifest.json" if paths[name].is_dir() else paths[name]
        digest = read_json(artifact).get("semantic_digest")
        if digest != expected:
            raise ValueError(f"screening source {name} digest is not frozen")
        digests[name] = digest
    return {
        "run_id": SOURCE_RUN_ID,
        "paths": paths,
        "source_research_sha": SOURCE_RESEARCH_SHA,
        "source_advisor_sha": TRANSFER_SOURCE_ADVISOR_SHA,
        "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "source_artifact_digests": digests,
    }


def validate_k8_reference(repository_root: Path) -> dict[str, Any]:
    directory = repository_root / "experiments/arecel-census13/search-budget-k8/budget-180"
    plan = read_json(directory / "optimization-plan.json")
    search = read_json(directory / "search-result.json")
    if plan.get("semantic_digest") != K8_PLAN_DIGEST:
        raise ValueError("frozen K=8 OptimizationPlan digest mismatch")
    if search.get("semantic_digest") != K8_SEARCH_DIGEST:
        raise ValueError("frozen K=8 SearchResult digest mismatch")
    if search.get("termination_reason") not in COMPLETED_TERMINATIONS:
        raise ValueError("frozen K=8 SearchResult is not completed")
    if search.get("final_ordered_candidate_ids") != list(K8_SELECTED):
        raise ValueError("frozen K=8 membership mismatch")
    if float(search["final_objective"]) != K8_OBJECTIVE:
        raise ValueError("frozen K=8 objective mismatch")
    if plan["budget"]["candidate_limit"] != 8:
        raise ValueError("frozen K=8 plan candidate limit mismatch")
    return {
        "plan_digest": K8_PLAN_DIGEST,
        "search_digest": K8_SEARCH_DIGEST,
        "objective": K8_OBJECTIVE,
        "selected_candidate_ids": list(K8_SELECTED),
        "selected_count": len(K8_SELECTED),
        "termination_reason": search["termination_reason"],
    }


def classify_screening(
    k8_objective: float,
    k12_objective: float,
    k8_membership: list[str] | tuple[str, ...],
    k12_membership: list[str] | tuple[str, ...],
    *,
    tolerance: float = OBJECTIVE_TOLERANCE,
) -> dict[str, Any]:
    objective_delta = float(k8_objective) - float(k12_objective)
    relative = objective_delta / float(k8_objective)
    objective_equal = abs(objective_delta) <= tolerance
    same_membership = tuple(k8_membership) == tuple(k12_membership)
    decision = (
        "k8-sufficient-within-k12" if objective_equal and same_membership else "k8-screen-limited"
    )
    return {
        "decision": decision,
        "k8_objective": float(k8_objective),
        "k12_objective": float(k12_objective),
        "absolute_improvement": objective_delta,
        "relative_improvement": relative,
        "objective_equal_within_tolerance": objective_equal,
        "objective_tolerance": tolerance,
        "same_membership": same_membership,
        "k8_only_selected_ids": sorted(set(k8_membership) - set(k12_membership)),
        "k12_only_selected_ids": sorted(set(k12_membership) - set(k8_membership)),
    }


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
        if rank is None or not 9 <= rank <= 12:
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
    live_ids = {
        evaluation["candidate_id"]
        for round_ in search_result["completed_rounds"]
        for evaluation in round_["evaluations"]
    }
    accepted_ids = {move["added_candidate_id"] for move in search_result["accepted_moves"]}
    outcomes = []
    for definition in definitions:
        candidate_id = definition["candidate_id"]
        if candidate_id in accepted_ids:
            outcome = "accepted"
        elif candidate_id in live_ids:
            outcome = "rejected-in-completed-round"
        else:
            outcome = "never-reached-because-search-terminated-earlier"
        outcomes.append(
            {**definition, "evaluated_live": candidate_id in live_ids, "outcome": outcome}
        )
    return outcomes


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"frozen advisor command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr}"
        )


def _compact_tree_is_empty(directory: Path) -> None:
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError(f"screening output already exists and is not empty: {directory}")


def run_screening_k12(
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
    output = (
        output_directory or repository_root / "experiments/arecel-census13/screening-k12"
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
            str(K12_CANDIDATE_LIMIT),
            "--wall-clock-seconds",
            str(K12_WALL_CLOCK_SECONDS),
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
    if plan["budget"]["candidate_limit"] != K12_CANDIDATE_LIMIT:
        raise ValueError("K=12 OptimizationPlan candidate limit mismatch")
    if float(plan["budget"]["wall_clock_seconds"]) != K12_WALL_CLOCK_SECONDS:
        raise ValueError("K=12 OptimizationPlan wall-clock cap mismatch")

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

    search = read_json(search_path)
    termination = search["termination_reason"]
    if termination not in COMPLETED_TERMINATIONS and not termination.startswith("budget-expired"):
        raise ValueError(f"unsupported K=12 termination reason: {termination}")
    candidate_universe = read_json(paths["candidate_universe"])
    singleton_profile = read_json(singleton_path)
    native_repository_manifest = read_json(paths["native_repository"] / "manifest.json")
    definitions = rank_definitions(
        candidate_universe, singleton_profile, native_repository_manifest
    )
    all_definitions = []
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    profiles = {item["candidate_id"]: item for item in singleton_profile["candidate_profiles"]}
    native = {item["candidate_id"]: item for item in native_repository_manifest["candidates"]}
    for candidate_id, candidate in candidates.items():
        if candidate_id in profiles:
            all_definitions.append(
                {
                    "candidate_id": candidate_id,
                    "frozen_singleton_rank": profiles[candidate_id].get("frozen_precedence_rank"),
                    "kind": candidate["kind"],
                    "column_names": candidate["column_names"],
                    "native_state": native[candidate_id]["state"],
                    "singleton_improvement": profiles[candidate_id]["improvement"],
                }
            )
    all_by_id = {item["candidate_id"]: item for item in all_definitions}
    trace = []
    for move in search["accepted_moves"]:
        item = all_by_id[move["added_candidate_id"]]
        trace.append(
            {
                "round": move["round_index"],
                "candidate_id": move["added_candidate_id"],
                "frozen_singleton_rank": item["frozen_singleton_rank"],
                "kind": item["kind"],
                "column_names": item["column_names"],
                "singleton_improvement": item["singleton_improvement"],
                "objective_before": move["objective_before"],
                "objective_after": move["objective_after"],
                "marginal_improvement": move["improvement"],
            }
        )
    outcomes = rank_outcomes(definitions, search)
    comparison = classify_screening(
        K8_OBJECTIVE,
        search["final_objective"],
        K8_SELECTED,
        search["final_ordered_candidate_ids"],
    )
    if termination not in COMPLETED_TERMINATIONS:
        comparison["decision"] = "k12-incomplete"
    runtime = search["runtime_metadata"]
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
            "candidate_limit": K12_CANDIDATE_LIMIT,
            "wall_clock_cap_seconds": K12_WALL_CLOCK_SECONDS,
            "k8_reference": k8,
            "k12_plan_digest": plan["semantic_digest"],
            "k12_search_digest": search["semantic_digest"],
            "k12_objective": search["final_objective"],
            "k12_selected_candidate_ids": search["final_ordered_candidate_ids"],
            "termination_reason": termination,
            **comparison,
        },
        "singleton_ranks_9_12": outcomes,
        "accepted_move_trace": trace,
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
    write_json(output / "screening-k12-v1.json", artifact)
    return {
        "output_directory": str(output),
        "plan_digest": plan["semantic_digest"],
        "search_digest": search["semantic_digest"],
        "decision": comparison["decision"],
        "termination_reason": termination,
        "query_estimates": runtime["planner_query_estimate_count"],
    }
