"""Small live semantic-equivalence smoke for Advisor v2 Greedy ADD.

The harness deliberately reuses the production Advisor's snapshot, candidate
incidence, native payload registration, planner, utility, and q-error loss.
It only constructs a three-query utility fixture and compares the preserved v1
reference search with the v2 incidence-incremental search.  It is not a formal
RQ4 experiment and never runs the full AreCEL workload.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from . import FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_git_sha, verify_research_repository
from .provenance import read_json, semantic_digest, write_json

FORMAT_VERSION = "advisor-greedy-incremental-equivalence-v1"
ADVISOR_SHA = "fbd912e94aa1bacedeb5070bc5584a0b84cc9fad"
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_RUN = (
    RESEARCH_ROOT / ".runtime/rq1-census13-canary/advisor-design/1ad3a0f7fc9d8c1918e5b117"
)


def _modules(advisor_root: Path) -> dict[str, Any]:
    source = str(Path(advisor_root).resolve() / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
    from extstats_advisor.dbms.postgres.sandbox import (
        POSTGRES_PLANNER_SANDBOX_CONTRACT,
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.dbms.postgres.search import (
        IncrementalPostgresSearchEvaluator,
        PostgresSearchEvaluator,
    )
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.native_stats.model import ABSENT_NATIVE, PRESENT
    from extstats_advisor.optimization import (
        BaselineProfile,
        CandidateSingletonProfile,
        PlannerIdentity,
        SearchDeadline,
        SingletonProfile,
        create_optimization_plan,
        greedy_add_search,
        greedy_add_search_incremental,
    )
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.snapshot.model import Workload, WorkloadQuery
    from extstats_advisor.utility import WeightedWorkloadUtility
    from extstats_advisor.utility.loss import QErrorLoss

    return {
        "load_candidate_universe": load_candidate_universe,
        "PostgresPlannerSession": PostgresPlannerSession,
        "PostgresSearchEvaluator": PostgresSearchEvaluator,
        "IncrementalPostgresSearchEvaluator": IncrementalPostgresSearchEvaluator,
        "POSTGRES_PLANNER_SANDBOX_CONTRACT": POSTGRES_PLANNER_SANDBOX_CONTRACT,
        "prepare_postgres_planner_sandbox": prepare_postgres_planner_sandbox,
        "verify_postgres_planner_sandbox": verify_postgres_planner_sandbox,
        "destroy_postgres_planner_sandbox": destroy_postgres_planner_sandbox,
        "load_ground_truth_set": load_ground_truth_set,
        "ArtifactGroundTruthProvider": ArtifactGroundTruthProvider,
        "load_native_stats_repository": load_native_stats_repository,
        "PRESENT": PRESENT,
        "ABSENT_NATIVE": ABSENT_NATIVE,
        "BaselineProfile": BaselineProfile,
        "CandidateSingletonProfile": CandidateSingletonProfile,
        "PlannerIdentity": PlannerIdentity,
        "SearchDeadline": SearchDeadline,
        "SingletonProfile": SingletonProfile,
        "create_optimization_plan": create_optimization_plan,
        "greedy_add_search": greedy_add_search,
        "greedy_add_search_incremental": greedy_add_search_incremental,
        "load_snapshot": load_snapshot,
        "Workload": Workload,
        "WorkloadQuery": WorkloadQuery,
        "WeightedWorkloadUtility": WeightedWorkloadUtility,
        "QErrorLoss": QErrorLoss,
    }


def _source_paths(source_run: Path) -> dict[str, Path]:
    return {
        "snapshot": source_run / "advisor-snapshot",
        "candidate_universe": source_run / "candidate-universe.json",
        "native_repository": source_run / "native-stats-repository",
        "ground_truth": source_run / "ground-truth-v1.json",
        "manifest": source_run / "manifest.json",
    }


def _subset_workload(
    snapshot: Any, query_ids: tuple[str, ...], workload_cls: Any, query_cls: Any
) -> Any:
    by_id = {query.query_id: query for query in snapshot.workload.queries}
    return workload_cls(
        snapshot.workload.workload_id,
        tuple(
            query_cls(query_id, by_id[query_id].sql, by_id[query_id].weight)
            for query_id in query_ids
        ),
        {**snapshot.workload.provenance, "incremental_equivalence_query_subset": True},
    )


def _subset_truth(truth: Any, query_ids: tuple[str, ...]) -> Any:
    selected = frozenset(query_ids)
    return type(truth)(
        truth.source_snapshot_semantic_digest,
        truth.workload_id,
        truth.source,
        tuple(item for item in truth.truths if item.query_id in selected),
        truth.collection_contract,
        truth.created_at,
        {**truth.runtime_metadata, "incremental_equivalence_query_subset": True},
    )


def _estimate_utility(
    planner: Any, utility: Any, candidate_ids: tuple[str, ...], query_ids: tuple[str, ...]
) -> Any:
    from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

    planner.activate(PostgresStatisticsConfiguration(candidate_ids))
    estimates = {
        estimate.query_id: estimate.estimated_rows
        for estimate in planner.estimate_queries(query_ids)
    }
    return utility.evaluate(estimates)


def _build_smoke_profile(
    modules: dict[str, Any],
    planner: Any,
    snapshot: Any,
    universe: Any,
    repository: Any,
    utility: Any,
    query_ids: tuple[str, ...],
    truth_digest: str,
) -> Any:
    started = time.perf_counter()
    baseline = _estimate_utility(planner, utility, (), query_ids)
    native_by_id = {candidate.candidate_id: candidate for candidate in repository.candidate_models}
    candidate_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    raw_profiles: list[tuple[str, str, int, float, float]] = []
    for candidate in universe.candidates:
        native = native_by_id[candidate.candidate_id]
        if native.state == modules["PRESENT"]:
            result = _estimate_utility(planner, utility, (candidate.candidate_id,), query_ids)
            objective = float(result.objective)
            improvement = float(baseline.objective - result.objective)
            raw_profiles.append(
                (
                    candidate.candidate_id,
                    native.state,
                    candidate.static_precedence_rank,
                    objective,
                    improvement,
                )
            )
        else:
            raw_profiles.append(
                (
                    candidate.candidate_id,
                    modules["ABSENT_NATIVE"],
                    candidate.static_precedence_rank,
                    float(baseline.objective),
                    0.0,
                )
            )
    present = [profile for profile in raw_profiles if profile[1] == modules["PRESENT"]]
    ordered = sorted(
        present,
        key=lambda profile: (
            -profile[4],
            candidate_by_id[profile[0]].static_precedence_rank,
            profile[0],
        ),
    )
    rank = {profile[0]: index for index, profile in enumerate(ordered, start=1)}
    ranked_profiles = tuple(
        modules["CandidateSingletonProfile"](
            candidate_id,
            state,
            static_rank,
            objective,
            improvement,
            rank.get(candidate_id),
        )
        for candidate_id, state, static_rank, objective, improvement in raw_profiles
    )
    return modules["SingletonProfile"](
        snapshot.semantic_digest,
        universe.semantic_digest,
        repository.semantic_digest,
        truth_digest,
        modules["POSTGRES_PLANNER_SANDBOX_CONTRACT"],
        repository.backend_contract,
        repository.server_version,
        repository.server_version_num,
        repository.ordinary_stats_fingerprint,
        utility.utility_contract,
        utility.loss_contract,
        "singleton-utility-precedence-v1",
        modules["BaselineProfile"](float(baseline.objective)),
        ranked_profiles,
        tuple(profile[0] for profile in ordered),
        runtime_metadata={
            "evaluation_strategy": "three-query-live-smoke-profile",
            "positive_query_count": len(query_ids),
            "present_candidate_count": len(present),
            "configuration_objective_evaluations": 1 + len(present),
            "selection_preprocessing_wall_clock_seconds": time.perf_counter() - started,
        },
    )


def _proposal_trace(result: Any) -> list[dict[str, Any]]:
    trace = [
        {
            "round": 1,
            "evaluations": [item.to_dict() for item in result.first_round_evaluations],
        }
    ]
    trace.extend(
        {
            "round": item.round_index,
            "evaluations": [evaluation.to_dict() for evaluation in item.evaluations],
        }
        for item in result.completed_rounds
    )
    return trace


def run_smoke(
    *,
    advisor_root: Path,
    patched_dsn: str,
    patched_postgres_root: Path,
    output: Path,
    source_run: Path = DEFAULT_SOURCE_RUN,
    query_count: int = 3,
) -> dict[str, Any]:
    """Run a bounded live smoke and write a validation-backed artifact."""

    if query_count != 3:
        raise ValueError("the v1 smoke is intentionally fixed to three queries")
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
    query_ids = tuple(query.query_id for query in snapshot.workload.queries if query.weight > 0)[
        :query_count
    ]
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
        plan = modules["create_optimization_plan"](
            profile, candidate_limit=3, max_statistics_count=3, wall_clock_seconds=300.0
        )
        identity = modules["PlannerIdentity"](
            modules["POSTGRES_PLANNER_SANDBOX_CONTRACT"],
            repository.backend_contract,
            repository.server_version,
            repository.server_version_num,
            repository.ordinary_stats_fingerprint,
        )
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
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
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            incremental_evaluator = modules["IncrementalPostgresSearchEvaluator"](
                planner, snapshot, universe, plan, utility, query_ids=query_ids
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
            )
            audits = []
            audit_started = time.perf_counter()
            selected = list(incremental.final_ordered_candidate_ids)
            if selected:
                audits.append(
                    incremental_evaluator.audit_configuration_transition(
                        frozenset(), frozenset(selected[:1]), modules["SearchDeadline"](plan.budget)
                    )
                )
            if len(selected) >= 2:
                audits.append(
                    incremental_evaluator.audit_configuration_transition(
                        frozenset(selected[:1]),
                        frozenset(selected[:2]),
                        modules["SearchDeadline"](plan.budget),
                    )
                )
            audit_elapsed_seconds = time.perf_counter() - audit_started
    finally:
        modules["destroy_postgres_planner_sandbox"](patched_dsn)

    reference_trace = _proposal_trace(reference)
    incremental_trace = _proposal_trace(incremental)
    comparisons = {
        "first_round_evaluations_equal": reference_trace[0] == incremental_trace[0],
        "proposal_trace_equal": reference_trace == incremental_trace,
        "accepted_sequence_equal": reference.accepted_moves == incremental.accepted_moves,
        "final_membership_equal": reference.final_ordered_candidate_ids
        == incremental.final_ordered_candidate_ids,
        "final_objective_equal": reference.final_objective == incremental.final_objective,
        "termination_reason_equal": reference.termination_reason == incremental.termination_reason,
    }
    if not all(comparisons.values()):
        raise ValueError(f"reference/incremental semantic mismatch: {comparisons}")
    post_audit_runtime = incremental_evaluator.runtime_metadata()
    incremental_runtime = dict(incremental.runtime_metadata)
    incremental_runtime["audit_planner_query_calls"] = post_audit_runtime[
        "audit_planner_query_calls"
    ]
    incremental_runtime["planner_query_estimate_count_including_audit"] = post_audit_runtime[
        "planner_query_estimate_count"
    ]
    incremental_runtime["audit_wall_clock_seconds"] = audit_elapsed_seconds
    artifact: dict[str, Any] = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
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
            "manifest_sha256": __import__("hashlib")
            .sha256(paths["manifest"].read_bytes())
            .hexdigest(),
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
        "plan": {
            "format_version": plan.semantic_manifest()["format_version"],
            "candidate_screening_width_K_s": plan.screened_candidate_count,
            "maximum_selected_statistics_B": plan.max_statistics_count,
            "selected_k": incremental.selected_candidate_count,
            "semantic_digest": plan.computed_semantic_digest,
        },
        "singleton_profile": {
            "semantic_digest": profile.computed_semantic_digest,
            "runtime_metadata": dict(profile.runtime_metadata),
        },
        "sandbox_verification": {"prepared": prepared.metadata.to_dict(), "verified": verified},
        "reference": {
            "search_result_format": reference.format_version,
            "termination_reason": reference.termination_reason,
            "final_ordered_candidate_ids": list(reference.final_ordered_candidate_ids),
            "final_objective": reference.final_objective,
            "runtime_metadata": dict(reference.runtime_metadata),
            "trace": reference_trace,
        },
        "incremental": {
            "search_result_format": incremental.format_version,
            "termination_reason": incremental.termination_reason,
            "final_ordered_candidate_ids": list(incremental.final_ordered_candidate_ids),
            "final_objective": incremental.final_objective,
            "runtime_metadata": incremental_runtime,
            "trace": incremental_trace,
        },
        "nonincident_audits": audits,
        "semantic_equivalence": comparisons,
    }
    artifact["artifact_digest"] = semantic_digest(artifact)
    write_json(output, artifact)
    return inspect_artifact(output)


def validate_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported incremental search equivalence artifact")
    body = dict(value)
    digest = body.pop("artifact_digest", None)
    if digest != semantic_digest(body):
        raise ValueError("incremental search equivalence artifact digest mismatch")
    if value.get("status") != "complete" or value.get("formal_experiment") is not False:
        raise ValueError("incremental search smoke status is invalid")
    if not all(value.get("semantic_equivalence", {}).values()):
        raise ValueError("reference/incremental semantic gate failed")
    if not value.get("nonincident_audits"):
        raise ValueError("incremental search smoke lacks nonincident audit evidence")
    if value["plan"].get("maximum_selected_statistics_B") != 3:
        raise ValueError("smoke did not bind the explicit maximum-statistics budget")
    return inspect_artifact(path) | {"status": "valid"}


def inspect_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return {
        "format_version": value.get("format_version"),
        "status": value.get("status"),
        "artifact_digest": value.get("artifact_digest"),
        "query_count": value.get("smoke_fixture", {}).get("query_count"),
        "selected_k": value.get("plan", {}).get("selected_k"),
        "termination_reason": value.get("incremental", {}).get("termination_reason"),
        "semantic_equivalence": value.get("semantic_equivalence"),
    }


__all__ = ["FORMAT_VERSION", "inspect_artifact", "run_smoke", "validate_artifact"]
