"""Calibrate frozen greedy-search wall-clock budgets from one canonical run."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_REPOSITORY,
    FROZEN_PATCHED_POSTGRES_SHA,
)
from .pins import verify_frozen_systems, verify_research_repository
from .provenance import read_json, reject_credentials, semantic_digest, write_json

FORMAT_VERSION = "arecel-search-budget-calibration-v1"
EXPECTED_SOURCE_RUN = "bf7fda28d90b3da88e7a14e4"
EXPECTED_SOURCE_RESEARCH_SHA = "9de794eaa62389c48ee6c5cebb192a467b9696cc"
EXPECTED_BUDGETS = (60, 120, 180)
COMPLETED_TERMINATIONS = frozenset({"local-optimum", "all-screened-candidates-selected"})
BUDGET_TERMINATIONS = frozenset({"budget-expired-before-round", "budget-expired-incomplete-round"})


def validate_budgets(budgets: list[int] | tuple[int, ...]) -> tuple[int, ...]:
    values = tuple(int(value) for value in budgets)
    if not values or any(value <= 0 or value > 180 for value in values):
        raise ValueError("calibration budgets must be positive and no greater than 180 seconds")
    if tuple(sorted(set(values))) != values:
        raise ValueError("calibration budgets must be strictly increasing and unique")
    return values


def classify_termination(reason: str) -> str:
    if reason in COMPLETED_TERMINATIONS:
        return "completed"
    if reason in BUDGET_TERMINATIONS:
        return "budget-limited"
    raise ValueError(f"unsupported search termination reason: {reason}")


def first_completed_budget(results: list[dict[str, Any]]) -> int | None:
    completed = [
        int(result["budget_seconds"])
        for result in results
        if classify_termination(result["termination_reason"]) == "completed"
    ]
    return min(completed) if completed else None


def validate_objective_monotonicity(
    baseline_objective: float, results: list[dict[str, Any]]
) -> bool:
    previous = float(baseline_objective)
    for result in sorted(results, key=lambda item: int(item["budget_seconds"])):
        objective = float(result["final_objective"])
        if objective > previous:
            raise ValueError(
                "search objective worsened with a larger budget: "
                f"previous={previous}, current={objective}, budget={result['budget_seconds']}"
            )
        previous = objective
    return True


def validate_source_binding(
    source_run: dict[str, Any], *, run_id: str = EXPECTED_SOURCE_RUN
) -> dict[str, Any]:
    if source_run.get("run_id") != run_id or source_run.get("status") != "complete":
        raise ValueError("calibration source must be the completed corrected canonical run")
    if source_run.get("research_commit_sha") != EXPECTED_SOURCE_RESEARCH_SHA:
        raise ValueError("calibration source research SHA is not the corrected canonical SHA")
    if source_run.get("advisor_commit_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("calibration source advisor SHA does not match the frozen system")
    if source_run.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("calibration source PostgreSQL SHA does not match the frozen system")
    return {
        "run_id": source_run["run_id"],
        "research_commit_sha": source_run["research_commit_sha"],
        "advisor_commit_sha": source_run["advisor_commit_sha"],
        "patched_postgres_commit_sha": source_run["patched_postgres_commit_sha"],
    }


def resolve_selected_definitions(
    selected_ids: list[str],
    candidate_universe: dict[str, Any],
    singleton_profile: dict[str, Any],
    search_result: dict[str, Any],
) -> list[dict[str, Any]]:
    candidates = {item["candidate_id"]: item for item in candidate_universe["candidates"]}
    profiles = {item["candidate_id"]: item for item in singleton_profile["candidate_profiles"]}
    marginal = {
        move["added_candidate_id"]: move["improvement"] for move in search_result["accepted_moves"]
    }
    result = []
    for candidate_id in selected_ids:
        candidate = candidates[candidate_id]
        profile = profiles[candidate_id]
        result.append(
            {
                "candidate_id": candidate_id,
                "kind": candidate["kind"],
                "column_names": candidate["column_names"],
                "frozen_precedence_rank": profile["frozen_precedence_rank"],
                "singleton_objective": profile["singleton_objective"],
                "singleton_improvement": profile["improvement"],
                "marginal_add_improvement": marginal.get(candidate_id),
            }
        )
    return result


def _assert_compact_tree(directory: Path) -> None:
    forbidden_names = {
        "candidate-universe.json",
        "ground-truth-v1.json",
        "native-stats-repository",
        "advisor-snapshot",
        "workload.json",
    }
    for path in directory.rglob("*"):
        if path.name in forbidden_names:
            raise ValueError(f"calibration directory must not copy bulk source artifact: {path}")


def _run_advisor(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"frozen advisor command failed ({completed.returncode}): {' '.join(command)}\n"
            f"{completed.stderr}"
        )


def _source_artifact_digests(source_directory: Path, manifest: dict[str, Any]) -> dict[str, str]:
    result = {}
    for name in (
        "snapshot",
        "ground_truth",
        "candidate_universe",
        "native_repository",
        "singleton_profile",
    ):
        metadata = manifest["artifacts"][name]
        path = source_directory / metadata["path"]
        source = path / "manifest.json" if path.is_dir() else path
        value = read_json(source)
        digest = value.get("semantic_digest")
        if digest != metadata["semantic_digest"]:
            raise ValueError(f"source artifact digest mismatch: {name}")
        result[name] = digest
    return result


def _search_record(budget: int, plan: dict[str, Any], search: dict[str, Any]) -> dict[str, Any]:
    runtime = search["runtime_metadata"]
    live = int(runtime["live_configuration_evaluation_count"])
    elapsed = float(runtime["elapsed_search_seconds"])
    return {
        "budget_seconds": budget,
        "optimization_plan_digest": plan["semantic_digest"],
        "search_result_digest": search["semantic_digest"],
        "termination_reason": search["termination_reason"],
        "termination_class": classify_termination(search["termination_reason"]),
        "baseline_objective": search["baseline_objective"],
        "final_objective": search["final_objective"],
        "absolute_improvement": search["improvement"],
        "relative_improvement": search["improvement"] / search["baseline_objective"],
        "selected_candidate_ids": search["final_ordered_candidate_ids"],
        "accepted_moves": search["accepted_moves"],
        "accepted_move_count": len(search["accepted_moves"]),
        "completed_round_count": len(search["completed_rounds"]),
        "partial_final_round_evaluation_count": runtime.get("partial_final_round_evaluation_count"),
        "cached_singleton_configuration_count": runtime["cached_singleton_configuration_count"],
        "live_configuration_evaluation_count": live,
        "planner_query_estimate_count": runtime["planner_query_estimate_count"],
        "elapsed_search_seconds": elapsed,
        "seconds_per_live_configuration": elapsed / live if live else None,
    }


def run_search_budget_calibration(
    *,
    source_run: Path,
    planner_dsn: str,
    output_directory: Path,
    budgets: list[int] | tuple[int, ...] = EXPECTED_BUDGETS,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    budgets = validate_budgets(budgets)
    if not planner_dsn.strip():
        raise ValueError("planner DSN is required and never written to an artifact")
    source_run = source_run.expanduser().resolve()
    output_directory = output_directory.expanduser().resolve()
    if output_directory.exists() and any(output_directory.iterdir()):
        raise FileExistsError(f"calibration output already exists: {output_directory}")
    output_directory.mkdir(parents=True, exist_ok=True)
    _assert_compact_tree(output_directory)

    source_manifest = read_json(source_run / "manifest.json")
    source_binding = validate_source_binding(source_manifest)
    source_digests = _source_artifact_digests(source_run, source_manifest)
    source_search = read_json(source_run / "search-result.json")
    source_profile = read_json(source_run / "singleton-profile.json")
    source_universe = read_json(source_run / "candidate-universe.json")
    source_30 = _search_record(
        30,
        read_json(source_run / "optimization-plan.json"),
        source_search,
    )

    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    systems = verify_frozen_systems(advisor_root, patched_postgres_root)
    if systems["advisor_commit_sha"] != FROZEN_ADVISOR_SHA:
        raise ValueError("advisor root is not the frozen advisor")
    if systems["patched_postgres_commit_sha"] != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("patched PostgreSQL root is not the frozen source")

    snapshot_path = source_run / source_manifest["artifacts"]["snapshot"]["path"]
    candidate_path = source_run / source_manifest["artifacts"]["candidate_universe"]["path"]
    native_path = source_run / source_manifest["artifacts"]["native_repository"]["path"]
    ground_truth_path = source_run / source_manifest["artifacts"]["ground_truth"]["path"]
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.native_stats.repository import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    repository = load_native_stats_repository(native_path)
    results = []
    for budget in budgets:
        budget_directory = output_directory / f"budget-{budget}"
        budget_directory.mkdir()
        plan_path = budget_directory / "optimization-plan.json"
        search_path = budget_directory / "search-result.json"
        _run_advisor(
            [
                advisor_command,
                "optimization",
                "plan",
                str(snapshot_path),
                str(candidate_path),
                str(native_path),
                str(ground_truth_path),
                str(source_run / source_manifest["artifacts"]["singleton_profile"]["path"]),
                "--candidate-limit",
                "8",
                "--wall-clock-seconds",
                str(budget),
                "--output",
                str(plan_path),
            ]
        )
        plan = read_json(plan_path)
        prepared = False
        try:
            prepare_postgres_planner_sandbox(planner_dsn, snapshot, universe, repository)
            prepared = True
            verify_postgres_planner_sandbox(planner_dsn, snapshot, universe, repository)
            _run_advisor(
                [
                    advisor_command,
                    "optimization",
                    "search",
                    "postgres",
                    str(snapshot_path),
                    str(candidate_path),
                    str(native_path),
                    str(ground_truth_path),
                    str(source_run / source_manifest["artifacts"]["singleton_profile"]["path"]),
                    str(plan_path),
                    "--dsn",
                    planner_dsn,
                    "--output",
                    str(search_path),
                ]
            )
            search = read_json(search_path)
            _run_advisor(
                [
                    advisor_command,
                    "optimization",
                    "validate",
                    str(plan_path),
                    "--snapshot",
                    str(snapshot_path),
                    "--candidate-universe",
                    str(candidate_path),
                    "--native-repository",
                    str(native_path),
                    "--ground-truth",
                    str(ground_truth_path),
                    "--singleton-profile",
                    str(source_run / source_manifest["artifacts"]["singleton_profile"]["path"]),
                ]
            )
            _run_advisor(
                [
                    advisor_command,
                    "optimization",
                    "search",
                    "validate",
                    str(search_path),
                    "--snapshot",
                    str(snapshot_path),
                    "--candidate-universe",
                    str(candidate_path),
                    "--native-repository",
                    str(native_path),
                    "--ground-truth",
                    str(ground_truth_path),
                    "--singleton-profile",
                    str(source_run / source_manifest["artifacts"]["singleton_profile"]["path"]),
                    "--optimization-plan",
                    str(plan_path),
                ]
            )
        finally:
            if prepared:
                destroy_postgres_planner_sandbox(planner_dsn)
        results.append(_search_record(budget, plan, search))

    baseline = float(source_30["baseline_objective"])
    validate_objective_monotonicity(baseline, results)
    completed_budget = first_completed_budget(results)
    longest = (
        next(item for item in reversed(results) if item["termination_class"] == "completed")
        if completed_budget is not None
        else results[-1]
    )
    longest_search = read_json(
        output_directory / f"budget-{longest['budget_seconds']}" / "search-result.json"
    )
    selected_definitions = resolve_selected_definitions(
        longest["selected_candidate_ids"], source_universe, source_profile, longest_search
    )
    artifact = {
        "format_version": FORMAT_VERSION,
        "source_run_id": source_binding["run_id"],
        "source_run_research_sha": source_binding["research_commit_sha"],
        "calibration_implementation_research_sha": research_identity["research_commit_sha"],
        "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
        "advisor_commit_sha": systems["advisor_commit_sha"],
        "patched_postgresql_repository": FROZEN_PATCHED_POSTGRES_REPOSITORY,
        "patched_postgresql_commit_sha": systems["patched_postgres_commit_sha"],
        "source_artifact_digests": source_digests,
        "candidate_limit": 8,
        "source_30_second": source_30,
        "tested_budgets": results,
        "first_tested_completed_budget": completed_budget,
        "converged": {
            "budget_seconds": longest["budget_seconds"],
            "final_objective": longest["final_objective"],
            "selected_candidate_ids": longest["selected_candidate_ids"],
            "selected_definitions": selected_definitions,
        },
        "objective_monotonicity": "validated",
        "calibration_rationale": "30-second source rate used only to size requested 60/120/180-second tests",
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output_directory / "calibration-v1.json", artifact)
    return artifact
