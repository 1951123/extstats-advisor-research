"""Pre-registered RQ4 screening-width sensitivity harness.

This module owns only research orchestration and evidence validation.  Search
plans are built from the frozen singleton order and the actual Greedy ADD
implementation is supplied by the frozen Advisor.  No statistics estimator,
payload builder, utility, or loss function is implemented here.
"""

from __future__ import annotations

import copy
import dataclasses
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from . import FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_git_sha
from .provenance import read_json, semantic_digest
from .rq1_canary import _ensure_planner_catalog
from .rq4_ablation import RQ4ValidationError
from .rq4_formal_common import (
    DATASETS,
    _advisor_modules,
    _planner_identity,
    _require_incremental_backend,
    build_ranked_plan,
    load_reusable_source,
)
from .system_freeze_v2 import FROZEN_ADVISOR_SHA as FROZEN_V2_ADVISOR_SHA
from .system_freeze_v2 import FROZEN_STOCK_POSTGRES_SHA, FROZEN_SYSTEM_FREEZE_V2_DIGEST

FORMAT_VERSION = "rq4-ks-sensitivity-v1"
PREFLIGHT_FORMAT = "rq4-ks-sensitivity-preflight-v1"
SMOKE_FORMAT = "rq4-ks-sensitivity-smoke-v1"
LIVE_SMOKE_FORMAT = "rq4-ks-sensitivity-live-smoke-v1"
LIVE_SMOKE_SCOPE = "small-live-correctness-validation"
OFFLINE_SMOKE_SCOPE = "small-plan-correctness-validation"
EXPERIMENT_ID = "rq4-ks-sensitivity"
SCREENING_WIDTHS: tuple[int | Literal["all"], ...] = (4, 8, 16, 32, "all")
FIXED_B = 4
SEARCH_WALL_CLOCK_SECONDS = 300.0
MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS = 2_000
SMOKE_CANDIDATE_COUNT = 6
SMOKE_QUERY_COUNT = 3
FORMAL_DATASETS = DATASETS

_FIXED_K_CHILD_DIGESTS = {
    "arecel-census13": "ed9031d8573f0048cbc852e5b8a236c7a29cc224b6012fd88b252700bbebba73",
    "arecel-power7": "c092541aa381a3e4eb8660e44f870f61ad1ea81eafc2d5fe94a87b79d8e853a7",
    "arecel-dmv11": "15182b79f7691890cbfa5121d7eda65bbbb02737fb5129646dcc25341330b693",
}
_FULL_K_SUMMARY_DIGEST = "4db25845552c38f1b3b48b5cf3ec9b7637617eb162701e9444a2a2b8b189f50f"


def normalize_screening_width(value: int | str) -> int | Literal["all"]:
    if value == "all":
        return "all"
    if isinstance(value, bool) or not isinstance(value, int) or value < FIXED_B:
        raise RQ4ValidationError("K_s must be 'all' or an integer >= B=4")
    return value


def effective_candidate_count(eligible_count: int, screening_width: int | str) -> int:
    if eligible_count < 0:
        raise RQ4ValidationError("eligible candidate count must be non-negative")
    width = normalize_screening_width(screening_width)
    return eligible_count if width == "all" else min(width, eligible_count)


def worst_case_live_proposals(eligible_count: int, screening_width: int | str) -> int:
    count = effective_candidate_count(eligible_count, screening_width)
    if count < FIXED_B:
        raise RQ4ValidationError("the screened universe must contain at least B candidates")
    return 3 * count - 6


def frozen_candidate_prefix(
    source: Mapping[str, Any], screening_width: int | str
) -> tuple[str, ...]:
    """Return the exact frozen singleton-order prefix; never rerank it."""

    eligible_ids = {
        item["candidate_id"] for item in source["eligible_universe"]["eligible_candidates"]
    }
    ordered = tuple(
        candidate_id
        for candidate_id in source["singleton_profile"].frozen_ordered_candidate_ids
        if candidate_id in eligible_ids
    )
    if set(ordered) != eligible_ids:
        raise RQ4ValidationError("eligible IDs do not match frozen singleton order")
    width = normalize_screening_width(screening_width)
    return ordered if width == "all" else ordered[:width]


def candidate_prefix_digest(candidate_ids: Sequence[str]) -> str:
    return semantic_digest({"candidate_prefix": list(candidate_ids)})


def build_sensitivity_plan(
    source: Mapping[str, Any],
    modules: Mapping[str, Any],
    screening_width: int | str,
) -> Any:
    width = normalize_screening_width(screening_width)
    return build_ranked_plan(
        source,
        modules,
        screening_width=width,
        max_statistics_count=FIXED_B,
        wall_clock_seconds=SEARCH_WALL_CLOCK_SECONDS,
    )


def _system_freeze_digest(research_root: Path) -> str:
    freeze = read_json(research_root / "paper/system-freeze-v2.json")
    digest = freeze.get("semantic_digest")
    if digest != FROZEN_SYSTEM_FREEZE_V2_DIGEST:
        raise RQ4ValidationError("system-freeze-v2 digest does not match the frozen contract")
    return digest


def validate_all_reuse_gate(
    dataset_id: str, research_root: Path, source: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate the only permitted source for the future ``all`` point."""

    if dataset_id not in FORMAL_DATASETS:
        raise RQ4ValidationError(f"unsupported sensitivity dataset: {dataset_id}")
    source = source or load_reusable_source(
        dataset_id, research_root, Path("/home/wqts/projects/extstats-advisor")
    )
    child_path = research_root / f"experiments/{dataset_id}/rq4-fixed-k-v2/rq4-ablation-v2.json"
    summary_path = research_root / "experiments/rq4-fixed-k-cross-dataset-summary-v1.json"
    if not child_path.is_file() or not summary_path.is_file():
        raise RQ4ValidationError("fixed-k-v2 all-point reuse evidence is missing")
    child = read_json(child_path)
    summary = read_json(summary_path)
    if child.get("semantic_digest") != _FIXED_K_CHILD_DIGESTS[dataset_id]:
        raise RQ4ValidationError("fixed-k-v2 child digest is not the registered source")
    if summary.get("semantic_digest") != _FULL_K_SUMMARY_DIGEST:
        raise RQ4ValidationError("fixed-k-v2 cross-dataset summary digest is not registered")
    if child.get("format_version") != "rq4-fixed-k-v2" or child.get("status") != "complete":
        raise RQ4ValidationError("fixed-k-v2 child is not a complete v2 artifact")
    if child.get("dataset_id") != dataset_id or child.get("fixed_k") != FIXED_B:
        raise RQ4ValidationError("fixed-k-v2 child dataset/B binding is incorrect")
    if child.get("system_freeze_semantic_digest") != FROZEN_SYSTEM_FREEZE_V2_DIGEST:
        raise RQ4ValidationError("fixed-k-v2 child system freeze binding is incorrect")
    if child.get("eligible_universe_digest") != source["eligible_universe"]["semantic_digest"]:
        raise RQ4ValidationError("fixed-k-v2 child eligible-universe digest differs from source")
    design_path = child_path.parent / child["rq4a"]["artifact"]
    design = read_json(design_path)
    if design.get("semantic_digest") != child["rq4a"]["semantic_digest"]:
        raise RQ4ValidationError("fixed-k-v2 design child digest does not bind")
    budget = design.get("selection_budget", {})
    if budget != {
        "unit": "configuration-objective-evaluations",
        "max_configuration_evaluations": MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
        "wall_clock_seconds": SEARCH_WALL_CLOCK_SECONDS,
        "final_evaluation_excluded": True,
    }:
        raise RQ4ValidationError("fixed-k-v2 selection budget is not equivalent")
    if design.get("implementation", {}).get("greedy") != "advisor-incidence-incremental-v1":
        raise RQ4ValidationError("fixed-k-v2 child does not use incremental Greedy")
    greedy = design.get("methods", {}).get("greedy-ADD", {})
    final = greedy.get("final_sandbox_evaluation", {})
    if final.get("evaluation_scope") != "independent-final-sandbox-evaluation":
        raise RQ4ValidationError("fixed-k-v2 Greedy lacks an independent final evaluation")
    return {
        "status": "valid",
        "evidence_mode": "reused",
        "dataset_id": dataset_id,
        "source_path": str(child_path.relative_to(research_root)),
        "source_semantic_digest": child["semantic_digest"],
        "cross_dataset_summary_digest": summary["semantic_digest"],
        "source_method": "greedy-ADD",
        "fixed_k": FIXED_B,
        "selection_wall_clock_seconds": SEARCH_WALL_CLOCK_SECONDS,
        "max_configuration_objective_evaluations": MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
        "final_evaluation_scope": final["evaluation_scope"],
    }


def build_preflight(
    dataset_id: str,
    research_root: Path,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
) -> dict[str, Any]:
    source = load_reusable_source(dataset_id, research_root, advisor_root)
    _system_freeze_digest(research_root)
    n = len(source["eligible_universe"]["eligible_candidates"])
    points = []
    for width in SCREENING_WIDTHS:
        prefix = frozen_candidate_prefix(source, width)
        points.append(
            {
                "K_s": width,
                "effective_candidate_count": len(prefix),
                "candidate_prefix": list(prefix),
                "candidate_prefix_semantic_digest": candidate_prefix_digest(prefix),
                "worst_case_live_proposals": worst_case_live_proposals(n, width),
                "execution_mode": "reused" if width == "all" else "not-executed",
            }
        )
    result = {
        "format_version": PREFLIGHT_FORMAT,
        "experiment_id": "rq4-ks-sensitivity-v1",
        "status": "ready-to-run",
        "dataset_id": dataset_id,
        "formal_run_executed": False,
        "fixed_B": FIXED_B,
        "screening_widths": list(SCREENING_WIDTHS),
        "search_wall_clock_seconds": SEARCH_WALL_CLOCK_SECONDS,
        "max_configuration_objective_evaluations": MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
        "eligible_candidate_count": n,
        "eligible_universe_semantic_digest": source["eligible_universe"]["semantic_digest"],
        "singleton_profile_reuse": {
            "source_profile_semantic_digest": source["singleton_profile"].computed_semantic_digest,
            "new_singleton_profiling_work": 0,
            "source_profiling_accounting": dict(source["singleton_profile"].runtime_metadata),
            "source_artifact_digest": source["source_artifact_digests"]["singleton_profile"],
        },
        "points": points,
        "all_point_reuse_gate": validate_all_reuse_gate(dataset_id, research_root, source),
        "source_artifact_digests": source["source_artifact_digests"],
        "frozen_system_identities": {
            "advisor_sha": FROZEN_V2_ADVISOR_SHA,
            "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
            "system_freeze_v2_digest": FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        },
    }
    result["semantic_digest"] = semantic_digest(result)
    return result


def validate_preflight(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != PREFLIGHT_FORMAT:
        raise RQ4ValidationError("unsupported K_s sensitivity preflight format")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if value.get("semantic_digest") != expected:
        raise RQ4ValidationError("K_s sensitivity preflight digest mismatch")
    if value.get("formal_run_executed") is not False:
        raise RQ4ValidationError("preflight must not claim a formal run")
    if value.get("fixed_B") != FIXED_B or value.get("screening_widths") != list(SCREENING_WIDTHS):
        raise RQ4ValidationError("preflight protocol constants drifted")
    n = value.get("eligible_candidate_count")
    for point in value.get("points", []):
        if point["effective_candidate_count"] != effective_candidate_count(n, point["K_s"]):
            raise RQ4ValidationError("preflight effective candidate count is incorrect")
        if point["worst_case_live_proposals"] != worst_case_live_proposals(n, point["K_s"]):
            raise RQ4ValidationError("preflight proposal count is incorrect")
    return {"status": "valid", "format_version": PREFLIGHT_FORMAT, "semantic_digest": expected}


def _small_source(source: Mapping[str, Any], count: int = SMOKE_CANDIDATE_COUNT) -> dict[str, Any]:
    """Project committed profile rows into a bounded, non-formal smoke fixture."""

    ordered = frozen_candidate_prefix(source, "all")
    if len(ordered) < count or count <= FIXED_B:
        raise RQ4ValidationError("smoke fixture needs more than B eligible candidates")
    selected = set(ordered[:count])
    profile = source["singleton_profile"]
    projected = dataclasses.replace(
        profile,
        candidate_profiles=tuple(
            item for item in profile.candidate_profiles if item.candidate_id in selected
        ),
        frozen_ordered_candidate_ids=tuple(ordered[:count]),
        semantic_digest=None,
        runtime_metadata={
            **dict(profile.runtime_metadata),
            "sensitivity_smoke_projection": True,
            "source_profile_semantic_digest": profile.computed_semantic_digest,
        },
    )
    eligible = copy.deepcopy(source["eligible_universe"])
    eligible["eligible_candidates"] = [
        item for item in eligible["eligible_candidates"] if item["candidate_id"] in selected
    ]
    eligible["semantic_digest"] = semantic_digest(
        {key: item for key, item in eligible.items() if key != "semantic_digest"}
    )
    return {**source, "singleton_profile": projected, "eligible_universe": eligible}


def _bounded_smoke_inputs(source: Mapping[str, Any], modules: Mapping[str, Any]) -> dict[str, Any]:
    """Bind utility/evaluation to three existing positive-weight queries.

    The planner still receives the immutable source snapshot so its sandbox
    and native-repository identities remain valid.  Only the bounded smoke's
    utility and evaluator query list are projected to this small subset.
    """

    from extstats_advisor.snapshot.model import Workload, WorkloadQuery

    queries = tuple(query for query in source["snapshot"].workload.queries if query.weight > 0)[
        :SMOKE_QUERY_COUNT
    ]
    if len(queries) != SMOKE_QUERY_COUNT:
        raise RQ4ValidationError("live smoke source has fewer than three positive-weight queries")
    query_ids = tuple(query.query_id for query in queries)
    workload = Workload(
        source["snapshot"].workload.workload_id,
        tuple(WorkloadQuery(query.query_id, query.sql, query.weight) for query in queries),
        {**source["snapshot"].workload.provenance, "rq4_ks_smoke_query_subset": True},
    )
    truths = tuple(truth for truth in source["ground_truth"].truths if truth.query_id in query_ids)
    if len(truths) != SMOKE_QUERY_COUNT:
        raise RQ4ValidationError("live smoke source lacks truth for every bounded query")
    smoke_truth = dataclasses.replace(
        source["ground_truth"],
        truths=truths,
        runtime_metadata={
            **dict(source["ground_truth"].runtime_metadata),
            "rq4_ks_smoke_query_subset": True,
        },
        semantic_digest=None,
    )
    return {
        **source,
        "smoke_query_ids": query_ids,
        "smoke_workload": workload,
        "smoke_ground_truth": smoke_truth,
    }


def _profile_bounded_live_smoke(
    source: Mapping[str, Any],
    modules: Mapping[str, Any],
    planner: Any,
) -> tuple[Any, dict[str, Any]]:
    """Build a transient profile whose objective covers only the smoke queries."""

    from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

    query_ids = tuple(source["smoke_query_ids"])
    utility = modules["WeightedWorkloadUtility"](
        source["smoke_workload"],
        modules["ArtifactGroundTruthProvider"](source["smoke_ground_truth"]),
        modules["QErrorLoss"](),
    )
    candidate_ids = tuple(
        item["candidate_id"] for item in source["eligible_universe"]["eligible_candidates"]
    )
    candidate_by_id = {
        candidate.candidate_id: candidate for candidate in source["candidate_universe"].candidates
    }
    native_by_id = {
        candidate.candidate_id: candidate
        for candidate in source["native_repository"].candidate_models
    }

    def evaluate(candidate_ids_to_activate: tuple[str, ...]) -> Any:
        planner.activate(PostgresStatisticsConfiguration(candidate_ids_to_activate))
        estimates = {
            estimate.query_id: estimate.estimated_rows
            for estimate in planner.estimate_queries(query_ids)
        }
        return utility.evaluate(estimates)

    started = time.perf_counter()
    baseline = evaluate(())
    candidate_results = []
    for candidate_id in candidate_ids:
        result = evaluate((candidate_id,))
        candidate = candidate_by_id[candidate_id]
        native = native_by_id[candidate_id]
        candidate_results.append(
            modules["CandidateSingletonProfile"](
                candidate_id,
                native.state,
                candidate.static_precedence_rank,
                result.objective,
                baseline.objective - result.objective,
                1 if native.state != "absent-native" else None,
            )
        )
    present = [item for item in candidate_results if item.native_state != "absent-native"]
    ordered = tuple(
        item.candidate_id
        for item in sorted(
            present,
            key=lambda item: (
                -item.improvement,
                item.static_precedence_rank,
                item.candidate_id,
            ),
        )
    )
    ranks = {candidate_id: rank for rank, candidate_id in enumerate(ordered, 1)}
    ranked_profiles = tuple(
        modules["CandidateSingletonProfile"](
            item.candidate_id,
            item.native_state,
            item.static_precedence_rank,
            item.singleton_objective,
            item.improvement,
            ranks.get(item.candidate_id),
        )
        for item in candidate_results
    )
    profile = modules["SingletonProfile"](
        source["snapshot"].semantic_digest,
        source["candidate_universe"].semantic_digest,
        source["native_repository"].semantic_digest,
        source["smoke_ground_truth"].computed_semantic_digest,
        modules["POSTGRES_PLANNER_SANDBOX_CONTRACT"],
        str(source["native_repository"].backend_contract),
        source["native_repository"].server_version,
        source["native_repository"].server_version_num,
        source["native_repository"].ordinary_stats_fingerprint,
        utility.utility_contract,
        utility.loss_contract,
        modules["SINGLETON_PRECEDENCE_POLICY"],
        modules["BaselineProfile"](baseline.objective),
        ranked_profiles,
        ordered,
        {
            "evaluation_scope": "bounded-live-smoke",
            "query_count": len(query_ids),
            "candidate_count": len(candidate_ids),
            "planner_query_estimate_count": len(query_ids) * (1 + len(candidate_ids)),
            "profiling_wall_clock_seconds": time.perf_counter() - started,
            "source_profile_semantic_digest": source["singleton_profile"].computed_semantic_digest,
        },
    )
    return profile, {
        "new_work": 1,
        "scope": "bounded-live-smoke",
        "profile_semantic_digest": profile.computed_semantic_digest,
        "query_count": len(query_ids),
        "candidate_count": len(candidate_ids),
        "planner_query_estimate_count": len(query_ids) * (1 + len(candidate_ids)),
        "wall_clock_seconds": profile.runtime_metadata["profiling_wall_clock_seconds"],
        "source_profile_semantic_digest": source["singleton_profile"].computed_semantic_digest,
    }


def build_smoke_artifact(
    *,
    research_root: Path,
    producer_research_sha: str,
    source: Mapping[str, Any] | None = None,
    candidate_count: int = SMOKE_CANDIDATE_COUNT,
    patched_dsn: str | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
) -> dict[str, Any]:
    """Create a bounded protocol smoke from committed profile evidence.

    With ``patched_dsn`` this executes only the six-candidate, positive-query
    sandbox fixture through the frozen Advisor planner.  Without it, the
    function still validates the exact prefix/plan contract offline.  Neither
    mode performs stock evaluation or new singleton profiling.
    """

    source = source or load_reusable_source("arecel-census13", research_root, advisor_root)
    small = _small_source(source, candidate_count)
    modules = _advisor_modules(advisor_root)
    if patched_dsn is not None:
        verify_git_sha(advisor_root, FROZEN_V2_ADVISOR_SHA)
        verify_git_sha(patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA)
        _require_incremental_backend(modules)
        small = _bounded_smoke_inputs(small, modules)
        # The reusable snapshot is catalog-bound to the stock database name.
        # Keep the backend patched while using the same catalog identity as the
        # frozen snapshot, matching the formal RQ4 runner contract.
        planner_dsn = _ensure_planner_catalog(patched_dsn, "extstats_stock")
        try:
            prepared = modules["prepare_postgres_planner_sandbox"](
                planner_dsn,
                small["snapshot"],
                source["candidate_universe"],
                source["native_repository"],
            )
            verified = modules["verify_postgres_planner_sandbox"](
                planner_dsn,
                small["snapshot"],
                source["candidate_universe"],
                source["native_repository"],
            )
            profiling_planner = modules["PostgresPlannerSession"](
                planner_dsn,
                small["snapshot"],
                source["candidate_universe"],
                source["native_repository"],
            )
            profiling_planner.open()
            try:
                bounded_profile, profiling_accounting = _profile_bounded_live_smoke(
                    small, modules, profiling_planner
                )
            finally:
                profiling_planner.close()
        except BaseException:
            modules["destroy_postgres_planner_sandbox"](planner_dsn)
            raise
        small["singleton_profile"] = bounded_profile
    else:
        planner_dsn = None
        prepared = None
        verified = None
        profiling_accounting = {
            "new_work": 0,
            "scope": "committed-offline-profile-reuse",
            "profile_semantic_digest": small["singleton_profile"].computed_semantic_digest,
        }
    points = []
    try:
        for width in (4, "all"):
            plan = build_sensitivity_plan(small, modules, width)
            prefix = tuple(plan.screened_candidate_ids)
            if patched_dsn is None:
                points.append(
                    {
                        "K_s": width,
                        "execution_mode": "plan-validated",
                        "effective_candidate_count": len(prefix),
                        "candidate_prefix": list(prefix),
                        "candidate_prefix_semantic_digest": candidate_prefix_digest(prefix),
                        "B": FIXED_B,
                        "selected_candidate_ids": [],
                        "selected_k": None,
                        "termination_reason": "not-executed",
                        "selection": {
                            "configuration_objective_evaluations": 0,
                            "postgresql_planner_query_calls": 0,
                            "wall_clock_seconds": 0.0,
                        },
                        "final_evaluation": {
                            "performed": False,
                            "stock_physical_evaluation": False,
                            "sandbox_evaluation": False,
                        },
                        "deterministic_replay": {
                            "prefix_equal_on_reconstruction": True,
                            "plan_semantic_digest": plan.computed_semantic_digest,
                        },
                    }
                )
            else:
                points.append(
                    _run_live_smoke_point(
                        small,
                        modules,
                        width,
                        planner_dsn,
                        plan,
                        advisor_root,
                    )
                )
    finally:
        if patched_dsn is not None:
            modules["destroy_postgres_planner_sandbox"](planner_dsn)
    hardening_path = research_root / (
        "experiments/rq4/integration-smoke/advisor-greedy-incremental-hardening-v2.json"
    )
    hardening = read_json(hardening_path)
    smoke_scope = LIVE_SMOKE_SCOPE if patched_dsn else OFFLINE_SMOKE_SCOPE
    artifact = {
        "format_version": SMOKE_FORMAT,
        "experiment_id": "rq4-ks-sensitivity-smoke-v1",
        "status": smoke_scope,
        "formal_confirmatory_experiment": False,
        "scope": smoke_scope,
        "research_commit_sha": producer_research_sha,
        "dataset_id": "arecel-census13",
        "candidate_universe_size": candidate_count,
        "backend_mode": "patched-postgresql-sandbox" if patched_dsn else "offline-plan-validation",
        "advisor_commit_sha": FROZEN_V2_ADVISOR_SHA if patched_dsn else None,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA if patched_dsn else None,
        "source_profile_semantic_digest": source["singleton_profile"].computed_semantic_digest,
        "source_singleton_profiling": {
            **profiling_accounting,
            "source_accounting": dict(source["singleton_profile"].runtime_metadata),
        },
        "tested_screening_widths": [4, "all"],
        "fixed_B": FIXED_B,
        "points": points,
        "incremental_merged_map_evidence": {
            "path": str(hardening_path.relative_to(research_root)),
            "semantic_digest": hardening["artifact_digest"],
            "reused_live_gate": True,
        },
        "stock_physical_evaluation": {"performed": False},
        "formal_arecel_runs": {dataset: False for dataset in FORMAL_DATASETS},
    }
    if patched_dsn is not None:
        artifact["sandbox_lifecycle"] = {
            "prepared": prepared.metadata.to_dict(),
            "verified": verified,
            "destroyed_after_run": True,
        }
    artifact["semantic_digest"] = semantic_digest(artifact)
    return artifact


def build_live_smoke_artifact(
    *,
    research_root: Path,
    producer_research_sha: str,
    patched_dsn: str,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
) -> dict[str, Any]:
    """Build the distinct frozen-v2 live smoke artifact."""

    artifact = build_smoke_artifact(
        research_root=research_root,
        producer_research_sha=producer_research_sha,
        patched_dsn=patched_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
    )
    artifact["format_version"] = LIVE_SMOKE_FORMAT
    artifact["experiment_id"] = LIVE_SMOKE_FORMAT
    artifact["advisor_commit_sha"] = FROZEN_V2_ADVISOR_SHA
    artifact["patched_postgres_commit_sha"] = FROZEN_PATCHED_POSTGRES_SHA
    artifact["live_fixture"] = {
        "candidate_universe_size": SMOKE_CANDIDATE_COUNT,
        "positive_weight_query_count": SMOKE_QUERY_COUNT,
        "screening_points": [4, "all"],
        "stock_physical_evaluation": False,
    }
    artifact["semantic_digest"] = semantic_digest(
        {key: value for key, value in artifact.items() if key != "semantic_digest"}
    )
    return artifact


def _run_live_smoke_point(
    source: Mapping[str, Any],
    modules: Mapping[str, Any],
    width: int | str,
    patched_dsn: str,
    plan: Any,
    advisor_root: Path,
) -> dict[str, Any]:
    """Run one bounded patched-sandbox smoke point and an exact replay."""

    from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

    query_ids = tuple(source["smoke_query_ids"])
    utility = modules["WeightedWorkloadUtility"](
        source["smoke_workload"],
        modules["ArtifactGroundTruthProvider"](source["smoke_ground_truth"]),
        modules["QErrorLoss"](),
    )
    replay_records = []
    for replay_index in (1, 2):
        planner = modules["PostgresPlannerSession"](
            patched_dsn,
            source["snapshot"],
            source["candidate_universe"],
            source["native_repository"],
        )
        planner.open()
        try:
            identity = _planner_identity(planner, advisor_root, source["snapshot"])
            evaluator = modules["IncrementalPostgresSearchEvaluator"](
                planner,
                source["snapshot"],
                source["candidate_universe"],
                plan,
                utility,
                query_ids=query_ids,
                expected_baseline_objective=source["singleton_profile"].baseline.objective,
            )
            started = time.perf_counter()
            result = modules["greedy_add_search_incremental"](
                source["singleton_profile"],
                plan,
                utility,
                evaluator,
                identity,
                prepare_initial_configuration=evaluator.prepare_initial_configuration,
                commit_configuration=evaluator.commit_configuration,
                discard_proposals=evaluator.discard_proposals,
                runtime_metadata_provider=evaluator.runtime_metadata,
            )
            selected = tuple(result.final_ordered_candidate_ids)
            planner.activate(PostgresStatisticsConfiguration(selected))
            estimates = {
                estimate.query_id: estimate.estimated_rows
                for estimate in planner.estimate_queries(query_ids)
            }
            final_utility = utility.evaluate(estimates)
            replay_records.append(
                {
                    "replay_index": replay_index,
                    "selected_candidate_ids": list(selected),
                    "selected_k": len(selected),
                    "termination_reason": result.termination_reason,
                    "search_result": result.to_manifest(),
                    "selection_accounting": dict(evaluator.runtime_metadata()),
                    "selection_wall_clock_seconds": time.perf_counter() - started,
                    "final_sandbox_evaluation": {
                        "performed": True,
                        "stock_physical_evaluation": False,
                        "sandbox_evaluation": True,
                        "sandbox_objective": float(final_utility.objective),
                        "postgresql_planner_query_calls": len(query_ids),
                        "planner_estimates_semantic_digest": semantic_digest(estimates),
                    },
                }
            )
        finally:
            planner.close()
    first, second = replay_records
    projection_fields = (
        "selected_candidate_ids",
        "selected_k",
        "termination_reason",
        "final_sandbox_evaluation",
    )
    if any(first[field] != second[field] for field in projection_fields):
        raise RQ4ValidationError(f"non-deterministic sensitivity smoke replay for K_s={width}")

    def replay_projection(record: Mapping[str, Any]) -> dict[str, Any]:
        search = record["search_result"]
        return {
            "selected_candidate_ids": record["selected_candidate_ids"],
            "selected_k": record["selected_k"],
            "termination_reason": record["termination_reason"],
            "final_sandbox_evaluation": record["final_sandbox_evaluation"],
            "search_result": {
                key: search.get(key)
                for key in (
                    "baseline_objective",
                    "final_objective",
                    "improvement",
                    "final_ordered_candidate_ids",
                    "termination_reason",
                    "first_round_evaluations",
                    "completed_rounds",
                    "accepted_moves",
                )
            },
        }

    replay_projections = [replay_projection(first), replay_projection(second)]
    if replay_projections[0] != replay_projections[1]:
        raise RQ4ValidationError(f"non-deterministic sensitivity SearchResult for K_s={width}")
    return {
        "K_s": width,
        "execution_mode": "executed",
        "effective_candidate_count": len(plan.screened_candidate_ids),
        "candidate_prefix": list(plan.screened_candidate_ids),
        "candidate_prefix_semantic_digest": candidate_prefix_digest(plan.screened_candidate_ids),
        "B": FIXED_B,
        "selected_candidate_ids": first["selected_candidate_ids"],
        "selected_k": first["selected_k"],
        "termination_reason": first["termination_reason"],
        "selection": first["selection_accounting"],
        "selection_wall_clock_seconds": first["selection_wall_clock_seconds"],
        "final_evaluation": first["final_sandbox_evaluation"],
        "deterministic_replay": {
            "replay_count": 2,
            "prefix_equal_on_reconstruction": True,
            "selection_projection_equal": True,
            "final_evaluation_equal": True,
            "search_result_projection_equal": True,
            "replay_semantic_digests": [semantic_digest(item) for item in replay_projections],
            "deterministic_search_result_projection": replay_projections[0]["search_result"],
        },
    }


def _validate_smoke_artifact(path: Path, expected_format: str) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != expected_format:
        raise RQ4ValidationError("unsupported K_s sensitivity smoke format")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if value.get("semantic_digest") != expected:
        raise RQ4ValidationError("K_s sensitivity smoke digest mismatch")
    if value.get("formal_confirmatory_experiment") is not False:
        raise RQ4ValidationError("sensitivity smoke must not be formal")
    expected_scope = (
        LIVE_SMOKE_SCOPE
        if value.get("backend_mode") == "patched-postgresql-sandbox"
        else OFFLINE_SMOKE_SCOPE
    )
    if value.get("scope") != expected_scope or value.get("status") != expected_scope:
        raise RQ4ValidationError("sensitivity smoke scope does not match its backend mode")
    if value.get("fixed_B") != FIXED_B or value.get("tested_screening_widths") != [4, "all"]:
        raise RQ4ValidationError("sensitivity smoke constants drifted")
    n = value.get("candidate_universe_size")
    if not isinstance(n, int) or n <= FIXED_B:
        raise RQ4ValidationError("sensitivity smoke must have all > B candidates")
    points = {point.get("K_s"): point for point in value.get("points", [])}
    if set(points) != {4, "all"}:
        raise RQ4ValidationError("sensitivity smoke must cover K_s=4 and all")
    for width, point in points.items():
        if point.get("effective_candidate_count") != effective_candidate_count(n, width):
            raise RQ4ValidationError("sensitivity smoke prefix width is incorrect")
        if len(point.get("candidate_prefix", [])) != point["effective_candidate_count"]:
            raise RQ4ValidationError("sensitivity smoke prefix is incomplete")
        if point.get("B") != FIXED_B:
            raise RQ4ValidationError("sensitivity smoke B is incorrect")
        selection = point.get("selection", {})
        if (
            selection.get("configuration_objective_evaluations", 0)
            > MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS
        ):
            raise RQ4ValidationError("sensitivity smoke exceeded the preregistered evaluation cap")
        if value.get("backend_mode") == "patched-postgresql-sandbox":
            if point.get("execution_mode") != "executed":
                raise RQ4ValidationError("live sensitivity smoke point was not executed")
            if point.get("final_evaluation", {}).get("performed") is not True:
                raise RQ4ValidationError("live sensitivity smoke lacks final sandbox evidence")
            replay = point.get("deterministic_replay", {})
            if replay.get("replay_count") != 2:
                raise RQ4ValidationError("live sensitivity smoke lacks exact replay evidence")
            digests = replay.get("replay_semantic_digests")
            if (
                replay.get("selection_projection_equal") is not True
                or replay.get("final_evaluation_equal") is not True
                or replay.get("search_result_projection_equal") is not True
                or not isinstance(digests, list)
                or len(digests) != 2
                or digests[0] != digests[1]
            ):
                raise RQ4ValidationError("live sensitivity smoke replay determinism gate failed")
        elif point.get("final_evaluation", {}).get("performed"):
            raise RQ4ValidationError("sensitivity smoke performed a forbidden final evaluation")
    if value.get("stock_physical_evaluation", {}).get("performed") is not False:
        raise RQ4ValidationError("sensitivity smoke performed stock physical evaluation")
    if expected_format == LIVE_SMOKE_FORMAT:
        if value.get("backend_mode") != "patched-postgresql-sandbox":
            raise RQ4ValidationError("live sensitivity smoke is not bound to the patched sandbox")
        if value.get("advisor_commit_sha") != FROZEN_V2_ADVISOR_SHA:
            raise RQ4ValidationError("live sensitivity smoke Advisor SHA is not frozen v2")
        if value.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
            raise RQ4ValidationError("live sensitivity smoke PostgreSQL SHA is not frozen")
        fixture = value.get("live_fixture", {})
        if fixture.get("candidate_universe_size") != SMOKE_CANDIDATE_COUNT:
            raise RQ4ValidationError("live sensitivity smoke candidate bound drifted")
        if fixture.get("positive_weight_query_count") != SMOKE_QUERY_COUNT:
            raise RQ4ValidationError("live sensitivity smoke query bound drifted")
        lifecycle = value.get("sandbox_lifecycle", {})
        if lifecycle.get("destroyed_after_run") is not True:
            raise RQ4ValidationError("live sensitivity smoke did not record sandbox cleanup")
    return {"status": "valid", "format_version": expected_format, "semantic_digest": expected}


def validate_smoke_artifact(path: Path) -> dict[str, Any]:
    return _validate_smoke_artifact(path, SMOKE_FORMAT)


def validate_live_smoke_artifact(path: Path) -> dict[str, Any]:
    return _validate_smoke_artifact(path, LIVE_SMOKE_FORMAT)


__all__ = [
    "EXPERIMENT_ID",
    "FIXED_B",
    "FORMAL_DATASETS",
    "FORMAT_VERSION",
    "LIVE_SMOKE_FORMAT",
    "LIVE_SMOKE_SCOPE",
    "MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS",
    "OFFLINE_SMOKE_SCOPE",
    "PREFLIGHT_FORMAT",
    "SCREENING_WIDTHS",
    "SEARCH_WALL_CLOCK_SECONDS",
    "SMOKE_FORMAT",
    "build_live_smoke_artifact",
    "build_preflight",
    "build_ranked_plan",
    "build_sensitivity_plan",
    "build_smoke_artifact",
    "candidate_prefix_digest",
    "effective_candidate_count",
    "frozen_candidate_prefix",
    "normalize_screening_width",
    "validate_all_reuse_gate",
    "validate_live_smoke_artifact",
    "validate_preflight",
    "validate_smoke_artifact",
    "worst_case_live_proposals",
]
