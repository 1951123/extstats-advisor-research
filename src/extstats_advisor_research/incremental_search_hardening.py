"""Live v2 incremental-search hardening evidence.

This module is deliberately a small compatibility harness, not a formal RQ4
experiment.  It reuses the production Advisor's planner, utility, loss, and
search drivers and keeps the prior v1 smoke artifact immutable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from extstats_advisor_research.incremental_search_equivalence import (
    DEFAULT_SOURCE_RUN,
    _build_smoke_profile,
    _modules,
    _proposal_trace,
    _source_paths,
    _subset_truth,
    _subset_workload,
)

from . import FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_git_sha, verify_research_repository
from .provenance import read_json, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "advisor-greedy-incremental-hardening-v2"
ADVISOR_SHA = "67206c4e0e30bc0726ca6febaa887e9ae9e7bbbd"
RESEARCH_ROOT = Path(__file__).resolve().parents[2]


def _comparison(reference: Any, incremental: Any) -> dict[str, bool]:
    return {
        "first_round_evaluations_equal": [
            item.to_dict() for item in reference.first_round_evaluations
        ]
        == [item.to_dict() for item in incremental.first_round_evaluations],
        "proposal_trace_equal": _proposal_trace(reference) == _proposal_trace(incremental),
        "accepted_sequence_equal": reference.accepted_moves == incremental.accepted_moves,
        "final_membership_equal": reference.final_ordered_candidate_ids
        == incremental.final_ordered_candidate_ids,
        "final_objective_equal": reference.final_objective == incremental.final_objective,
        "termination_reason_equal": reference.termination_reason == incremental.termination_reason,
    }


def _run_case(
    modules: dict[str, Any],
    *,
    snapshot: Any,
    universe: Any,
    repository: Any,
    profile: Any,
    utility: Any,
    query_ids: tuple[str, ...],
    identity: Any,
    patched_dsn: str,
    budget: int,
) -> dict[str, Any]:
    plan = modules["create_optimization_plan"](
        profile, candidate_limit=3, max_statistics_count=budget, wall_clock_seconds=300.0
    )
    with modules["PostgresPlannerSession"](patched_dsn, snapshot, universe, repository) as planner:
        reference_evaluator = modules["PostgresSearchEvaluator"](
            planner, snapshot, universe, plan, utility, query_ids=query_ids
        )
        reference = modules["greedy_add_search"](
            profile,
            plan,
            utility,
            reference_evaluator,
            identity,
            runtime_metadata_provider=reference_evaluator.runtime_metadata,
        )
    with modules["PostgresPlannerSession"](patched_dsn, snapshot, universe, repository) as planner:
        incremental_evaluator = modules["IncrementalPostgresSearchEvaluator"](
            planner,
            snapshot,
            universe,
            plan,
            utility,
            query_ids=query_ids,
            expected_baseline_objective=profile.baseline.objective,
        )
        incremental = modules["greedy_add_search_incremental"](
            profile,
            plan,
            utility,
            incremental_evaluator,
            identity,
            runtime_metadata_provider=incremental_evaluator.runtime_metadata,
            prepare_initial_configuration=incremental_evaluator.prepare_initial_configuration,
            commit_configuration=incremental_evaluator.commit_configuration,
            discard_proposals=incremental_evaluator.discard_proposals,
        )
        audits = []
        selected = list(incremental.final_ordered_candidate_ids)
        for before, after in zip(([], *selected[:-1]), selected):
            audits.append(
                incremental_evaluator.audit_configuration_transition(
                    frozenset(before),
                    frozenset((*before, after)),
                    modules["SearchDeadline"](plan.budget),
                )
            )
        runtime_after_audit = dict(incremental_evaluator.runtime_metadata())
    comparisons = _comparison(reference, incremental)
    if not all(comparisons.values()):
        raise ValueError(f"v2 bounded reference/incremental mismatch for B={budget}")
    if not all(item["passed"] for item in audits):
        raise ValueError(f"v2 nonincident audit failed for B={budget}")
    return {
        "budget_B": budget,
        "plan_format_version": plan.semantic_manifest()["format_version"],
        "plan_semantic_digest": plan.computed_semantic_digest,
        "reference": {
            "termination_reason": reference.termination_reason,
            "final_ordered_candidate_ids": list(reference.final_ordered_candidate_ids),
            "final_objective": reference.final_objective,
            "accepted_moves": [move.to_dict() for move in reference.accepted_moves],
            "runtime_metadata": dict(reference.runtime_metadata),
            "trace": _proposal_trace(reference),
        },
        "incremental": {
            "termination_reason": incremental.termination_reason,
            "final_ordered_candidate_ids": list(incremental.final_ordered_candidate_ids),
            "final_objective": incremental.final_objective,
            "accepted_moves": [move.to_dict() for move in incremental.accepted_moves],
            "runtime_metadata": dict(incremental.runtime_metadata),
            "runtime_metadata_after_audit": runtime_after_audit,
            "trace": _proposal_trace(incremental),
        },
        "nonincident_audits": audits,
        "semantic_equivalence": comparisons,
    }


def run_hardening_smoke(
    *,
    advisor_root: Path,
    patched_dsn: str,
    patched_postgres_root: Path,
    output: Path,
    source_run: Path = DEFAULT_SOURCE_RUN,
) -> dict[str, Any]:
    """Run the bounded live v2 hardening fixture and write evidence."""

    verify_git_sha(advisor_root, ADVISOR_SHA)
    verify_git_sha(patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA)
    research_identity = verify_research_repository(RESEARCH_ROOT)
    paths = _source_paths(source_run)
    if any(not path.exists() for path in paths.values()):
        raise FileNotFoundError("Census13 source run is incomplete")
    modules = _modules(advisor_root)
    snapshot = modules["load_snapshot"](paths["snapshot"])
    universe = modules["load_candidate_universe"](paths["candidate_universe"], snapshot)
    repository = modules["load_native_stats_repository"](paths["native_repository"])
    truth = modules["load_ground_truth_set"](paths["ground_truth"], snapshot)
    query_ids = tuple(query.query_id for query in snapshot.workload.queries if query.weight > 0)[:3]
    smoke_workload = _subset_workload(
        snapshot, query_ids, modules["Workload"], modules["WorkloadQuery"]
    )
    smoke_truth = _subset_truth(truth, query_ids)
    utility = modules["WeightedWorkloadUtility"](
        smoke_workload,
        modules["ArtifactGroundTruthProvider"](smoke_truth),
        modules["QErrorLoss"](),
    )
    prepared = modules["prepare_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    verified = modules["verify_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    try:
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            profile = _build_smoke_profile(
                modules,
                planner,
                snapshot,
                universe,
                repository,
                utility,
                query_ids,
                smoke_truth.computed_semantic_digest,
            )
        identity = modules["PlannerIdentity"](
            modules["POSTGRES_PLANNER_SANDBOX_CONTRACT"],
            repository.backend_contract,
            repository.server_version,
            repository.server_version_num,
            repository.ordinary_stats_fingerprint,
        )
        cases = {
            str(budget): _run_case(
                modules,
                snapshot=snapshot,
                universe=universe,
                repository=repository,
                profile=profile,
                utility=utility,
                query_ids=query_ids,
                identity=identity,
                patched_dsn=patched_dsn,
                budget=budget,
            )
            for budget in (3, 2, 1)
        }

        explicit_v1_plan = modules["create_optimization_plan"](
            profile, candidate_limit=3, max_statistics_count=None, wall_clock_seconds=300.0
        )
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            v1_evaluator = modules["PostgresSearchEvaluator"](
                planner, snapshot, universe, explicit_v1_plan, utility, query_ids=query_ids
            )
            explicit_v1 = modules["greedy_add_search"](
                profile,
                explicit_v1_plan,
                utility,
                v1_evaluator,
                identity,
                runtime_metadata_provider=v1_evaluator.runtime_metadata,
            )
        v2_reference = cases["3"]["reference"]
        v1_reference_equal = {
            "trace_equal": _proposal_trace(explicit_v1) == v2_reference["trace"],
            "accepted_sequence_equal": [move.to_dict() for move in explicit_v1.accepted_moves]
            == v2_reference["accepted_moves"],
            "final_membership_equal": list(explicit_v1.final_ordered_candidate_ids)
            == v2_reference["final_ordered_candidate_ids"],
            "final_objective_equal": explicit_v1.final_objective == v2_reference["final_objective"],
            "termination_reason_equal": explicit_v1.termination_reason
            == v2_reference["termination_reason"],
        }
        # The trace/final checks are the authoritative comparison; retain the
        # accepted sequence as a separately serialized diagnostic below.
        q1 = (query_ids[0],)
        q1_workload = _subset_workload(snapshot, q1, modules["Workload"], modules["WorkloadQuery"])
        q1_truth = _subset_truth(truth, q1)
        q1_utility = modules["WeightedWorkloadUtility"](
            q1_workload,
            modules["ArtifactGroundTruthProvider"](q1_truth),
            modules["QErrorLoss"](),
        )
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            q1_profile = _build_smoke_profile(
                modules,
                planner,
                snapshot,
                universe,
                repository,
                q1_utility,
                q1,
                q1_truth.computed_semantic_digest,
            )
        q1_plan = modules["create_optimization_plan"](
            q1_profile, candidate_limit=3, wall_clock_seconds=300.0
        )
        empty_candidates = [
            candidate_id
            for candidate_id in q1_plan.screened_candidate_ids
            if not set(universe.query_ids_for_candidate(candidate_id)).intersection(q1)
        ]
        incident_candidates = [
            candidate_id
            for candidate_id in q1_plan.screened_candidate_ids
            if set(universe.query_ids_for_candidate(candidate_id)).intersection(q1)
        ]
        if not empty_candidates or not incident_candidates:
            raise ValueError("three-query live fixture lacks an empty-incidence ADD witness")
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            empty_evaluator = modules["IncrementalPostgresSearchEvaluator"](
                planner,
                snapshot,
                universe,
                q1_plan,
                q1_utility,
                query_ids=q1,
                expected_baseline_objective=q1_profile.baseline.objective,
            )
            deadline = modules["SearchDeadline"](q1_plan.budget)
            start = frozenset((incident_candidates[0],))
            empty = empty_candidates[0]
            empty_evaluator.prepare_initial_configuration(start, deadline)
            before = empty_evaluator.runtime_metadata()["proposal_planner_query_calls"]
            empty_result = empty_evaluator(frozenset((*start, empty)), deadline)
            after = empty_evaluator.runtime_metadata()["proposal_planner_query_calls"]
            empty_evaluator.discard_proposals()
        start_profile = next(
            item
            for item in q1_profile.candidate_profiles
            if item.candidate_id == incident_candidates[0]
        )
        empty_incidence = {
            "candidate_id": empty,
            "current_membership": list(start),
            "proposed_membership": [*start, empty],
            "proposal_planner_query_calls": after - before,
            "objective_unchanged": empty_result.objective == start_profile.singleton_objective,
            "passed": after == before,
        }

        def expired_evaluation(_membership: Any, _deadline: Any) -> Any:
            from extstats_advisor.errors import SearchBudgetExpired

            raise SearchBudgetExpired("hardening deadline fixture")

        deadline_plan = modules["create_optimization_plan"](
            profile, candidate_limit=3, max_statistics_count=3, wall_clock_seconds=300.0
        )
        deadline_result = modules["greedy_add_search_incremental"](
            profile,
            deadline_plan,
            utility,
            expired_evaluation,
            identity,
        )
    finally:
        modules["destroy_postgres_planner_sandbox"](patched_dsn)

    all_cases_equal = all(all(case["semantic_equivalence"].values()) for case in cases.values())
    artifact = {
        "format_version": FORMAT_VERSION,
        "status": "complete"
        if all_cases_equal
        and empty_incidence["passed"]
        and deadline_result.termination_reason == "budget-expired-incomplete-round"
        else "failed",
        "formal_experiment": False,
        "experiment_id": FORMAT_VERSION,
        "research_repository": research_identity,
        "advisor": {"repository": "1951123/extstats-advisor", "commit_sha": ADVISOR_SHA},
        "patched_postgresql": {
            "repository": "1951123/postgresql-pgextadv",
            "commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "postgres_version": "16.14",
        },
        "source_run": {
            "path": str(source_run),
            "manifest_sha256": sha256_file(paths["manifest"]),
            "snapshot_semantic_digest": snapshot.semantic_digest,
            "candidate_universe_semantic_digest": universe.semantic_digest,
            "native_repository_semantic_digest": repository.semantic_digest,
            "full_workload_ground_truth_semantic_digest": truth.computed_semantic_digest,
        },
        "smoke_fixture": {
            "query_count": len(query_ids),
            "query_ids": list(query_ids),
            "full_workload_search": False,
            "formal_rq4_comparison": False,
            "truth_source": truth.source.kind,
            "subset_ground_truth_semantic_digest": smoke_truth.computed_semantic_digest,
        },
        "plan_policy": {
            "default_v2_candidate_screening_width_K_s": 3,
            "tested_budgets": [3, 2, 1],
            "explicit_v1_reference": True,
        },
        "singleton_profile": {
            "semantic_digest": profile.computed_semantic_digest,
            "runtime_metadata": dict(profile.runtime_metadata),
        },
        "sandbox_verification": {"prepared": prepared.metadata.to_dict(), "verified": verified},
        "cases": cases,
        "explicit_v1_reference": {
            "format_version": explicit_v1.format_version,
            "termination_reason": explicit_v1.termination_reason,
            "final_ordered_candidate_ids": list(explicit_v1.final_ordered_candidate_ids),
            "final_objective": explicit_v1.final_objective,
            "trace": _proposal_trace(explicit_v1),
            "semantic_equal_to_bounded_v2_B3": v1_reference_equal,
        },
        "empty_incidence_add": empty_incidence,
        "deadline_incomplete_round": {
            "termination_reason": deadline_result.termination_reason,
            "accepted_moves": [move.to_dict() for move in deadline_result.accepted_moves],
            "partial_final_round_evaluation_count": deadline_result.runtime_metadata[
                "partial_final_round_evaluation_count"
            ],
        },
        "artifact_notes": {
            "historical_v1_artifact_unchanged": True,
            "no_formal_arecel_rq4_experiment": True,
        },
    }
    artifact["artifact_digest"] = semantic_digest(artifact)
    write_json(output, artifact)
    return inspect_artifact(output)


def inspect_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return {
        "format_version": value.get("format_version"),
        "status": value.get("status"),
        "artifact_digest": value.get("artifact_digest"),
        "tested_budgets": value.get("plan_policy", {}).get("tested_budgets"),
        "empty_incidence_passed": value.get("empty_incidence_add", {}).get("passed"),
    }


def validate_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported incremental hardening artifact")
    body = dict(value)
    digest = body.pop("artifact_digest", None)
    if digest != semantic_digest(body):
        raise ValueError("incremental hardening artifact digest mismatch")
    if value.get("status") != "complete" or value.get("formal_experiment") is not False:
        raise ValueError("incremental hardening status is invalid")
    if value.get("plan_policy", {}).get("tested_budgets") != [3, 2, 1]:
        raise ValueError("incremental hardening budgets are incomplete")
    for case in value.get("cases", {}).values():
        if not all(case.get("semantic_equivalence", {}).values()):
            raise ValueError("incremental hardening semantic gate failed")
    if value.get("empty_incidence_add", {}).get("passed") is not True:
        raise ValueError("empty-incidence ADD gate failed")
    if value.get("deadline_incomplete_round", {}).get("termination_reason") != (
        "budget-expired-incomplete-round"
    ):
        raise ValueError("deadline-incomplete-round gate failed")
    return inspect_artifact(path) | {"status": "valid"}


__all__ = ["FORMAT_VERSION", "inspect_artifact", "run_hardening_smoke", "validate_artifact"]
