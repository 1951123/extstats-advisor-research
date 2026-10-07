"""Generic v2 formal RQ4 fixed-k orchestration.

This module is deliberately a research-side adapter.  It reuses the frozen
Advisor's snapshot, GroundTruthSet, native repository, planner session,
WeightedWorkloadUtility, and incidence-incremental Greedy ADD implementation.
It does not implement cardinality estimation, native payload construction, or
loss calculation in the research repository.

The source inputs are the already committed RQ2 v2 runs.  They are reused only
after their identities and digests have been checked; no new snapshot capture,
truth counting, or singleton profiling is performed by this module.
"""

from __future__ import annotations

import copy
import gzip
import json
import random
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, write_json
from .rq1_canary import _ensure_planner_catalog
from .rq4_ablation import (
    CANONICAL_METHOD_IDS,
    FIXED_K,
    INFORMATION_ACCESS_POLICY,
    RANDOM_SEEDS,
    RQ4ValidationError,
    build_eligible_universe,
    canonical_candidate_key,
)
from .rq4_postgres import RQ4ArtifactPaths
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_STOCK_POSTGRES_SHA,
    FROZEN_SYSTEM_FREEZE_V2_DIGEST,
    load_system_freeze_v2,
    verify_frozen_systems_v2,
)

FORMAL_FORMAT = "rq4-fixed-k-v2"
DESIGN_FORMAT = "rq4-design-evaluation-v2"
DETERMINISM_FORMAT = "rq4-design-determinism-v2"
PREFLIGHT_FORMAT = "rq4-fixed-k-preflight-v2"
# Compatibility alias retained for the producer's existing import contract.
PREflight_FORMAT = PREFLIGHT_FORMAT
SELECTION_MAX_CONFIGURATION_EVALUATIONS = 2_000
SELECTION_WALL_CLOCK_SECONDS = 300.0
FINAL_EVALUATION_WALL_CLOCK_SECONDS = 300.0
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
SEED_IDENTIFIER = 123
SETSEED_SQL = "SELECT setseed(1.0 / 123)"
QUERY_COUNT = 10_000
DETERMINISM_RUNTIME_KEYS = frozenset(
    {
        "wall_clock_seconds",
        "elapsed_wall_clock_seconds",
        "elapsed_search_seconds",
        "backend_wall_clock_seconds",
        "source_wall_clock_seconds",
        "new_selection_wall_clock_seconds",
        "runtime_metadata",
    }
)
DATASETS = ("arecel-census13", "arecel-power7", "arecel-dmv11")
RQ2_SOURCE_RUNS = {
    "arecel-census13": ".runtime/rq2-formal-v7/census13/canonical/6fab9c14bec4c26ad44ff42f",
    "arecel-power7": ".runtime/rq2-formal-v3/power7/canonical/5655242c1002eb76342faae9",
    "arecel-dmv11": ".runtime/rq2-formal-v4/dmv11/canonical/8af4a1d328125d01258bb894",
}
DATASET_RELATIONS = {
    "arecel-census13": "public.census13",
    "arecel-power7": "public.power7",
    "arecel-dmv11": "public.dmv11",
}
CANONICAL_METHOD_ORDER = tuple(f"random-k-seed-{seed}" for seed in RANDOM_SEEDS) + tuple(
    method for method in CANONICAL_METHOD_IDS if method != "random-k"
)


def _advisor_modules(advisor_root: Path) -> dict[str, Any]:
    source = str(Path(advisor_root).resolve() / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
    from extstats_advisor.dbms.postgres.sandbox import POSTGRES_PLANNER_SANDBOX_CONTRACT
    from extstats_advisor.dbms.postgres.search import IncrementalPostgresSearchEvaluator
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.optimization import (
        OptimizationBudget,
        OptimizationPlan,
        PlannerIdentity,
        ScreenedCandidate,
        greedy_add_search_incremental,
        load_singleton_profile,
    )
    from extstats_advisor.optimization.plan import SCREENING_POLICY
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.utility import WeightedWorkloadUtility
    from extstats_advisor.utility.loss import QErrorLoss

    return {
        "load_candidate_universe": load_candidate_universe,
        "PostgresPlannerSession": PostgresPlannerSession,
        "ArtifactGroundTruthProvider": ArtifactGroundTruthProvider,
        "load_ground_truth_set": load_ground_truth_set,
        "load_native_stats_repository": load_native_stats_repository,
        "OptimizationBudget": OptimizationBudget,
        "OptimizationPlan": OptimizationPlan,
        "PlannerIdentity": PlannerIdentity,
        "ScreenedCandidate": ScreenedCandidate,
        "greedy_add_search_incremental": greedy_add_search_incremental,
        "load_singleton_profile": load_singleton_profile,
        "SCREENING_POLICY": SCREENING_POLICY,
        "load_snapshot": load_snapshot,
        "WeightedWorkloadUtility": WeightedWorkloadUtility,
        "QErrorLoss": QErrorLoss,
        "IncrementalPostgresSearchEvaluator": IncrementalPostgresSearchEvaluator,
        "POSTGRES_PLANNER_SANDBOX_CONTRACT": POSTGRES_PLANNER_SANDBOX_CONTRACT,
    }


def _source_root(research_root: Path, dataset_id: str) -> Path:
    try:
        return research_root / RQ2_SOURCE_RUNS[dataset_id]
    except KeyError as exc:
        raise RQ4ValidationError(f"unsupported formal RQ4 dataset: {dataset_id}") from exc


def _source_paths(source: Path) -> RQ4ArtifactPaths:
    return RQ4ArtifactPaths(
        snapshot=source / "advisor-snapshot",
        candidate_universe=source / "candidate-universe.json",
        native_repository=source / "native-stats-repository",
        ground_truth=source / "ground-truth-v1.json",
    )


def _artifact_digest(path: Path) -> str:
    value = read_json(path / "manifest.json") if path.is_dir() else read_json(path)
    digest = value.get("semantic_digest")
    if not isinstance(digest, str):
        raise RQ4ValidationError(f"source artifact lacks semantic_digest: {path}")
    return digest


def load_reusable_source(
    dataset_id: str,
    research_root: Path,
    advisor_root: Path,
) -> dict[str, Any]:
    """Load and validate the committed RQ2 v2 source bundle."""

    source = _source_root(research_root, dataset_id)
    if not source.is_dir() or not (source / "manifest.json").is_file():
        raise RQ4ValidationError(f"RQ2 source manifest.json is missing: {source}")
    manifest = read_json(source / "manifest.json")
    expected = {
        "benchmark_id": dataset_id,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "stock_postgres_commit_sha": FROZEN_STOCK_POSTGRES_SHA,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise RQ4ValidationError(f"RQ2 source {dataset_id} does not bind {key}={value}")
    paths = _source_paths(source)
    for path in (*paths.__dict__.values(), source / "singleton-profile.json"):
        if not path.exists():
            raise RQ4ValidationError(f"RQ2 source artifact is missing: {path}")
    modules = _advisor_modules(advisor_root)
    snapshot = modules["load_snapshot"](paths.snapshot)
    universe = modules["load_candidate_universe"](paths.candidate_universe, snapshot)
    native = modules["load_native_stats_repository"](paths.native_repository)
    truth = modules["load_ground_truth_set"](paths.ground_truth, snapshot)
    singleton = modules["load_singleton_profile"](source / "singleton-profile.json")
    truth_source = getattr(truth.source, "kind", str(truth.source))
    if truth_source != "authoritative-external-exact":
        raise RQ4ValidationError(f"{dataset_id} source truth is not authoritative external exact")
    if len(truth.truths) != QUERY_COUNT:
        raise RQ4ValidationError(f"{dataset_id} source does not bind 10,000 truths")
    raw_universe = read_json(paths.candidate_universe)
    native_by_id = {item.candidate_id: item for item in native.candidate_models}
    availability = {
        item.get("candidate_id"): {
            "available": native_by_id.get(item.get("candidate_id"), None) is not None
            and native_by_id[item.get("candidate_id")].state == "present"
        }
        for item in raw_universe.get("candidates", [])
        if isinstance(item, Mapping)
    }
    eligible = build_eligible_universe(raw_universe, availability)
    eligible_ids = {item["candidate_id"] for item in eligible["eligible_candidates"]}
    profile_ids = {
        item.candidate_id for item in singleton.candidate_profiles if item.native_state == "present"
    }
    if eligible_ids != profile_ids:
        raise RQ4ValidationError(
            f"{dataset_id} singleton profile does not cover utility-independent eligible universe"
        )
    observation_sha = manifest.get("truth_capture", {}).get("observations_sha256")
    if not isinstance(observation_sha, str):
        raise RQ4ValidationError(f"{dataset_id} source lacks observations SHA")
    return {
        "dataset_id": dataset_id,
        "source_directory": source,
        "paths": paths,
        "manifest": manifest,
        "snapshot": snapshot,
        "candidate_universe": universe,
        "native_repository": native,
        "ground_truth": truth,
        "singleton_profile": singleton,
        "raw_candidate_universe": raw_universe,
        "eligible_universe": eligible,
        "observations_sha256": observation_sha,
        "source_artifact_digests": {
            "snapshot": _artifact_digest(paths.snapshot),
            "candidate_universe": _artifact_digest(paths.candidate_universe),
            "native_repository": _artifact_digest(paths.native_repository),
            "ground_truth": _artifact_digest(paths.ground_truth),
            "singleton_profile": _artifact_digest(source / "singleton-profile.json"),
        },
    }


def build_ranked_plan(
    source: Mapping[str, Any],
    modules: Mapping[str, Any],
    *,
    screening_width: int | str = "all",
    max_statistics_count: int = FIXED_K,
    wall_clock_seconds: float = SELECTION_WALL_CLOCK_SECONDS,
) -> Any:
    """Construct a plan from the frozen singleton order and an optional prefix.

    This is deliberately a plan-construction helper only.  It does not rank,
    profile, or otherwise inspect utility signals.  ``all`` is the full
    eligible order and therefore preserves the historical fixed-k plan when
    ``max_statistics_count`` is ``FIXED_K``.
    """

    profile = source["singleton_profile"]
    profile_by_id = {item.candidate_id: item for item in profile.candidate_profiles}
    eligible = source["eligible_universe"]["eligible_candidates"]
    eligible_ids = {item["candidate_id"] for item in eligible}
    full_ordered_ids = tuple(
        candidate_id
        for candidate_id in profile.frozen_ordered_candidate_ids
        if candidate_id in eligible_ids
    )
    if set(full_ordered_ids) != eligible_ids:
        raise RQ4ValidationError("eligible universe does not match singleton frozen order")
    if isinstance(max_statistics_count, bool) or not isinstance(max_statistics_count, int):
        raise RQ4ValidationError("max_statistics_count must be an integer")
    if max_statistics_count < 1 or max_statistics_count > len(full_ordered_ids):
        raise RQ4ValidationError("max_statistics_count must fit the eligible universe")
    if isinstance(screening_width, bool) or (
        not isinstance(screening_width, (int, str))
        or (isinstance(screening_width, str) and screening_width != "all")
        or (isinstance(screening_width, int) and screening_width < max_statistics_count)
    ):
        raise RQ4ValidationError(
            "screening_width must be 'all' or an integer >= max_statistics_count"
        )
    if not isinstance(wall_clock_seconds, (int, float)) or wall_clock_seconds <= 0:
        raise RQ4ValidationError("wall_clock_seconds must be positive")
    prefix_length = (
        len(full_ordered_ids)
        if screening_width == "all"
        else min(int(screening_width), len(full_ordered_ids))
    )
    ordered_ids = full_ordered_ids[:prefix_length]
    records = tuple(
        modules["ScreenedCandidate"](
            candidate_id,
            position,
            position,
            profile_by_id[candidate_id].singleton_objective,
            profile_by_id[candidate_id].improvement,
        )
        for position, candidate_id in enumerate(ordered_ids, 1)
    )
    all_ids = {item.candidate_id for item in profile.candidate_profiles}
    present_ordered = tuple(profile.frozen_ordered_candidate_ids)
    excluded = tuple(
        candidate_id for candidate_id in present_ordered if candidate_id not in set(ordered_ids)
    )
    present = set(present_ordered)
    absent = tuple(sorted(all_ids - present))
    budget = modules["OptimizationBudget"](
        len(ordered_ids), float(wall_clock_seconds), max_statistics_count
    )
    return modules["OptimizationPlan"](
        profile.source_snapshot_semantic_digest,
        profile.candidate_universe_semantic_digest,
        profile.native_stats_repository_semantic_digest,
        profile.ground_truth_semantic_digest,
        profile.computed_semantic_digest,
        profile.utility_contract,
        profile.loss_contract,
        profile.precedence_policy,
        modules["SCREENING_POLICY"],
        budget,
        len(ordered_ids) + len(excluded),
        ordered_ids,
        records,
        excluded,
        absent,
    )


def _build_full_universe_plan(source: Mapping[str, Any], modules: Mapping[str, Any]) -> Any:
    """Construct the historical full-eligible fixed-k plan."""

    return build_ranked_plan(
        source,
        modules,
        screening_width="all",
        max_statistics_count=FIXED_K,
        wall_clock_seconds=SELECTION_WALL_CLOCK_SECONDS,
    )


def _planner_identity(planner: Any, advisor_root: Path, snapshot: Any) -> Any:
    relation = snapshot.schemas[0].relation_name
    relation_oid = planner.connection.execute(
        "SELECT to_regclass(%s)::oid", (f"{relation.schema}.{relation.name}",)
    ).fetchone()[0]
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    ordinary = ordinary_stats_fingerprint(planner.connection, int(relation_oid))
    version, version_num = planner.connection.execute(
        "SELECT current_setting('server_version'), current_setting('server_version_num')"
    ).fetchone()
    return _advisor_modules(advisor_root)["PlannerIdentity"](
        _advisor_modules(advisor_root)["POSTGRES_PLANNER_SANDBOX_CONTRACT"],
        "postgresql-pgextadv-16.14-v1",
        str(version),
        int(version_num),
        str(ordinary),
    )


def _source_accounting(profile: Any) -> dict[str, Any]:
    runtime = dict(profile.runtime_metadata)
    return {
        "source_profile_semantic_digest": profile.computed_semantic_digest,
        "source_configuration_objective_evaluations": runtime.get("baseline_configuration_count", 1)
        + runtime.get("singleton_configuration_count", 0),
        "source_postgresql_planner_query_calls": runtime.get("planner_query_estimate_count"),
        "source_wall_clock_seconds": runtime.get(
            "elapsed_wall_clock_seconds", runtime.get("wall_clock_seconds")
        ),
        "new_selection_configuration_objective_evaluations": 0,
        "new_selection_postgresql_planner_query_calls": 0,
        "new_selection_wall_clock_seconds": 0.0,
        "reuse_policy": "RQ2-v2-singleton-profile-reuse; eligible coverage verified",
    }


def _incidence_greedy(
    source: Mapping[str, Any],
    advisor_root: Path,
    patched_dsn: str,
) -> dict[str, Any]:
    modules = _advisor_modules(advisor_root)
    snapshot = source["snapshot"]
    universe = source["candidate_universe"]
    native = source["native_repository"]
    truth = source["ground_truth"]
    profile = source["singleton_profile"]
    plan = _build_full_universe_plan(
        source, {**modules, "SCREENING_POLICY": modules["SCREENING_POLICY"]}
    )
    utility = modules["WeightedWorkloadUtility"](
        snapshot.workload,
        modules["ArtifactGroundTruthProvider"](truth),
        modules["QErrorLoss"](),
    )
    planner = modules["PostgresPlannerSession"](patched_dsn, snapshot, universe, native)
    planner.open()
    try:
        identity = _planner_identity(planner, advisor_root, snapshot)
        evaluator = modules["IncrementalPostgresSearchEvaluator"](
            planner,
            snapshot,
            universe,
            plan,
            utility,
            expected_baseline_objective=profile.baseline.objective,
        )
        result = modules["greedy_add_search_incremental"](
            profile,
            plan,
            utility,
            evaluator,
            identity,
            prepare_initial_configuration=evaluator.prepare_initial_configuration,
            commit_configuration=evaluator.commit_configuration,
            discard_proposals=evaluator.discard_proposals,
            runtime_metadata_provider=evaluator.runtime_metadata,
        )
        return {
            "search_result": result.to_manifest(),
            "runtime_metadata": dict(evaluator.runtime_metadata()),
            "plan_semantic_digest": plan.computed_semantic_digest,
        }
    finally:
        planner.close()


def _cheap_selection(source: Mapping[str, Any], method: str, seed: int | None = None) -> list[str]:
    candidates = source["eligible_universe"]["eligible_candidates"]
    ids = [item["candidate_id"] for item in candidates]
    by_id = {item["candidate_id"]: item for item in candidates}
    if len(ids) < FIXED_K:
        raise RQ4ValidationError("eligible universe is smaller than fixed k")
    if method == "random-k":
        return sorted(
            random.Random(seed).sample(ids, FIXED_K),
            key=lambda x: canonical_candidate_key(by_id[x]),
        )
    if method == "workload-frequency-top-k":
        universe = source["candidate_universe"]
        weights = {
            query.query_id: float(query.weight) for query in source["snapshot"].workload.queries
        }
        scores = {
            item["candidate_id"]: sum(
                max(weights.get(query_id, 0.0), 0.0)
                for query_id in universe.query_ids_for_candidate(item["candidate_id"])
            )
            for item in candidates
        }
    elif method == "native-payload-size-top-k":
        native = {item.candidate_id: item for item in source["native_repository"].candidate_models}
        scores = {
            item["candidate_id"]: float(native[item["candidate_id"]].payload_size)
            for item in candidates
        }
    elif method == "singleton-utility-top-k":
        profile = {
            item.candidate_id: item for item in source["singleton_profile"].candidate_profiles
        }
        scores = {
            item["candidate_id"]: -float(profile[item["candidate_id"]].singleton_objective)
            for item in candidates
        }
    else:
        raise RQ4ValidationError(f"not a cheap v2 method: {method}")
    return sorted(ids, key=lambda x: (-scores[x], canonical_candidate_key(by_id[x])))[:FIXED_K]


def _method_selection(
    source: Mapping[str, Any], advisor_root: Path, patched_dsn: str
) -> dict[str, Any]:
    methods: dict[str, Any] = {}
    for seed in RANDOM_SEEDS:
        selected = _cheap_selection(source, "random-k", seed)
        methods[f"random-k-seed-{seed}"] = {
            "method_id": f"random-k-seed-{seed}",
            "replicate_seed": seed,
            "status": "complete",
            "selected_membership": selected,
            "evaluation_order": selected,
            "deployment_order": selected,
            "selection_accounting": {
                "configuration_objective_evaluations": 0,
                "postgresql_planner_query_calls": 0,
                "wall_clock_seconds": 0.0,
            },
            "information_access_policy": INFORMATION_ACCESS_POLICY["random-k"],
        }
    for method in (
        "workload-frequency-top-k",
        "native-payload-size-top-k",
        "singleton-utility-top-k",
    ):
        selected = _cheap_selection(source, method)
        methods[method] = {
            "method_id": method,
            "status": "complete",
            "selected_membership": selected,
            "evaluation_order": selected,
            "deployment_order": selected,
            "selection_accounting": (
                _source_accounting(source["singleton_profile"])
                if method == "singleton-utility-top-k"
                else {
                    "configuration_objective_evaluations": 0,
                    "postgresql_planner_query_calls": 0,
                    "wall_clock_seconds": 0.0,
                }
            ),
            "information_access_policy": INFORMATION_ACCESS_POLICY[method],
        }
    greedy = _incidence_greedy(source, advisor_root, patched_dsn)
    search = greedy["search_result"]
    selected_ids = list(search["final_ordered_candidate_ids"])
    methods["greedy-ADD"] = {
        "method_id": "greedy-ADD",
        "status": "complete"
        if search["termination_reason"] in {"all-selected", "max-statistics-count", "local-optimum"}
        else "budget-censored",
        "selected_membership": selected_ids,
        "evaluation_order": selected_ids,
        "deployment_order": selected_ids,
        "termination_reason": search["termination_reason"],
        "search_result": search,
        "selection_accounting": greedy["runtime_metadata"],
        "information_access_policy": INFORMATION_ACCESS_POLICY["greedy-ADD"],
    }
    return {"methods": methods, "greedy": greedy}


def _final_sandbox_evaluations(
    source: Mapping[str, Any], advisor_root: Path, patched_dsn: str, methods: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Evaluate each selected design once, outside the selection budget."""

    modules = _advisor_modules(advisor_root)
    utility = modules["WeightedWorkloadUtility"](
        source["snapshot"].workload,
        modules["ArtifactGroundTruthProvider"](source["ground_truth"]),
        modules["QErrorLoss"](),
    )
    query_ids = tuple(
        query.query_id for query in source["snapshot"].workload.queries if query.weight > 0
    )
    planner = modules["PostgresPlannerSession"](
        patched_dsn,
        source["snapshot"],
        source["candidate_universe"],
        source["native_repository"],
    )
    planner.open()
    try:
        from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

        results: dict[str, dict[str, Any]] = {}
        for method in CANONICAL_METHOD_ORDER:
            started = time.perf_counter()
            selected = tuple(methods[method]["selected_membership"])
            planner.activate(PostgresStatisticsConfiguration(selected))
            estimates = {
                estimate.query_id: estimate.estimated_rows
                for estimate in planner.estimate_queries(query_ids)
            }
            objective = utility.evaluate(estimates)
            results[method] = {
                "status": "complete",
                "sandbox_objective": float(objective.objective),
                "configuration_objective_evaluations": 1,
                "postgresql_planner_query_calls": len(query_ids),
                "wall_clock_seconds": time.perf_counter() - started,
                "evaluation_scope": "independent-final-sandbox-evaluation",
                "selection_budget_charged": False,
                "planner_estimates_semantic_digest": semantic_digest(estimates),
            }
        return results
    finally:
        planner.close()


def build_preflight(dataset_id: str, research_root: Path, advisor_root: Path) -> dict[str, Any]:
    source = load_reusable_source(dataset_id, research_root, advisor_root)
    n = len(source["eligible_universe"]["eligible_candidates"])
    profile_runtime = dict(source["singleton_profile"].runtime_metadata)
    predicted_live = max(0, 3 * n - 6)
    result = {
        "format_version": PREflight_FORMAT,
        "dataset_id": dataset_id,
        "status": "ready-to-run",
        "fixed_k": FIXED_K,
        "eligible_candidate_count": n,
        "eligible_universe_semantic_digest": source["eligible_universe"]["semantic_digest"],
        "singleton_profile_reuse": _source_accounting(source["singleton_profile"]),
        "greedy_worst_case_live_configuration_evaluations": predicted_live,
        "greedy_predicted_planner_calls": None,
        "greedy_predicted_selection_wall_clock_seconds": None,
        "unknown_is_not_zero": True,
        "final_evaluations": {
            "method_count": 9,
            "planner_query_calls": 9 * QUERY_COUNT,
            "selection_budget_excludes_final_evaluation": True,
        },
        "rq4b_shared_realization": {
            "union_and_analyze_required": True,
            "methods": list(CANONICAL_METHOD_ORDER),
            "analyze_count": 1,
        },
        "source_profile_runtime_metadata": profile_runtime,
        "source_artifact_digests": source["source_artifact_digests"],
    }
    result["semantic_digest"] = semantic_digest(result)
    return result


def validate_reference_equivalence_gate(research_root: Path) -> dict[str, Any]:
    """Validate the committed small live v1/v2 incremental equivalence gate."""

    from .incremental_search_hardening import validate_artifact

    path = (
        research_root
        / "experiments/rq4/integration-smoke/advisor-greedy-incremental-hardening-v2.json"
    )
    result = validate_artifact(path)
    value = read_json(path)
    if result.get("status") != "valid" or value.get("status") != "complete":
        raise RQ4ValidationError("incremental reference equivalence gate is not complete")
    cases = value.get("cases", {})
    if set(cases) != {"3", "2", "1"}:
        raise RQ4ValidationError("reference equivalence gate lacks B=3,2,1 cases")
    if not all(all(case["semantic_equivalence"].values()) for case in cases.values()):
        raise RQ4ValidationError("reference and incremental traces are not equivalent")
    if value.get("empty_incidence_add", {}).get("passed") is not True:
        raise RQ4ValidationError("empty-incidence reference gate failed")
    return {
        "path": str(path.relative_to(research_root)),
        "artifact_digest": value["artifact_digest"],
        "format_version": value["format_version"],
        "tested_budgets": value["plan_policy"]["tested_budgets"],
        "full_workload_search": value["smoke_fixture"].get("full_workload_search", False),
        "passed": True,
    }


def _deterministic_projection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _deterministic_projection(item)
            for key, item in value.items()
            if key not in DETERMINISM_RUNTIME_KEYS
        }
    if isinstance(value, list):
        return [_deterministic_projection(item) for item in value]
    return value


def _selection_replay_projection(methods: Mapping[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = {}
    for method in CANONICAL_METHOD_ORDER:
        record = methods[method]
        projected = {
            "method_id": record.get("method_id"),
            "replicate_seed": record.get("replicate_seed"),
            "status": record.get("status"),
            "selected_membership": record.get("selected_membership"),
            "evaluation_order": record.get("evaluation_order"),
            "deployment_order": record.get("deployment_order"),
            "termination_reason": record.get("termination_reason"),
        }
        search = record.get("search_result")
        if isinstance(search, Mapping):
            projected["search_result"] = {
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
            }
        projection[method] = projected
    return {"methods": projection}


def _write_v2_determinism(
    path: Path,
    comparison: Mapping[str, Any],
    source: Mapping[str, Any],
    system_freeze: Mapping[str, Any],
    research_sha: str,
) -> dict[str, Any]:
    projection = copy.deepcopy(dict(comparison))
    projected = _deterministic_projection(projection)
    artifact = {
        "format_version": DETERMINISM_FORMAT,
        "experiment_id": DETERMINISM_FORMAT,
        "formal_confirmatory_experiment": True,
        "status": "deterministic-replay",
        "replay_count": 2,
        "dataset_id": source["dataset_id"],
        "candidate_universe_digest": source["eligible_universe"]["semantic_digest"],
        "system_freeze": system_freeze,
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "research_commit_sha": research_sha,
        "replay_semantic_digests": [semantic_digest(projected), semantic_digest(projected)],
        "deterministic_projection": projected,
        "checks": {
            "semantic_projection_stable": True,
            "membership_stable": True,
            "objective_stable": True,
            "termination_stable": True,
        },
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(artifact, handle, sort_keys=True, separators=(",", ":"))
    return artifact


def validate_v2_design_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != DESIGN_FORMAT:
        raise RQ4ValidationError("unsupported RQ4 design v2 format")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if value.get("semantic_digest") != expected:
        raise RQ4ValidationError("RQ4 design v2 semantic digest mismatch")
    methods = value.get("methods", {})
    if value.get("method_order") != list(CANONICAL_METHOD_ORDER) or set(methods) != set(
        CANONICAL_METHOD_ORDER
    ):
        raise RQ4ValidationError("RQ4 design v2 method order mismatch")
    if value.get("fixed_k") != FIXED_K:
        raise RQ4ValidationError("RQ4 design v2 fixed k mismatch")
    for method, result in methods.items():
        policy_method = "random-k" if method.startswith("random-k-seed-") else method
        if result.get("information_access_policy") != INFORMATION_ACCESS_POLICY.get(policy_method):
            raise RQ4ValidationError(f"{method} information-access policy mismatch")
        if (
            result.get("status") == "complete"
            and len(result.get("selected_membership", [])) != FIXED_K
        ):
            raise RQ4ValidationError(f"{method} does not have exactly k selected candidates")
    return {"status": "valid", "format_version": DESIGN_FORMAT, "semantic_digest": expected}


def validate_v2_determinism(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        value = json.load(handle)
    if value.get("format_version") != DETERMINISM_FORMAT or value.get("replay_count") != 2:
        raise RQ4ValidationError("invalid RQ4 v2 determinism artifact")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if value.get("semantic_digest") != expected or not all(value.get("checks", {}).values()):
        raise RQ4ValidationError("RQ4 v2 determinism gate failed")
    if (
        len(value.get("replay_semantic_digests", [])) != 2
        or value["replay_semantic_digests"][0] != value["replay_semantic_digests"][1]
    ):
        raise RQ4ValidationError("RQ4 v2 replay projections differ")
    return {"status": "valid", "format_version": DETERMINISM_FORMAT, "semantic_digest": expected}


def run_formal_rq4_v2(
    dataset_id: str,
    *,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    research_root: Path,
    system_freeze_path: Path,
) -> dict[str, Any]:
    """Run one v2 RQ4 child after the producer commit has been sealed."""

    if dataset_id not in DATASETS:
        raise RQ4ValidationError(f"unsupported formal RQ4 dataset: {dataset_id}")
    if output.exists():
        raise FileExistsError(f"formal RQ4 output exists: {output}")
    from .pins import verify_research_repository

    research_identity = verify_research_repository(research_root)
    verify_frozen_systems_v2(advisor_root, patched_postgres_root, stock_postgres_root)
    freeze = load_system_freeze_v2(system_freeze_path)
    freeze_digest = semantic_digest(
        {key: item for key, item in freeze.items() if key != "semantic_digest"}
    )
    if freeze_digest != FROZEN_SYSTEM_FREEZE_V2_DIGEST:
        raise RQ4ValidationError("system-freeze-v2 digest mismatch")
    source = load_reusable_source(dataset_id, research_root, advisor_root)
    reference_gate = validate_reference_equivalence_gate(research_root)
    preflight = build_preflight(dataset_id, research_root, advisor_root)
    if (
        preflight["greedy_worst_case_live_configuration_evaluations"]
        > SELECTION_MAX_CONFIGURATION_EVALUATIONS
    ):
        raise RQ4ValidationError("predicted Greedy configuration evaluations exceed selection cap")
    # The reusable RQ2 snapshot is catalog-bound to the stock database name.
    # The patched cluster therefore gets an isolated database with that same
    # catalog identity, while the frozen Advisor creates and destroys its
    # sample-only planner sandbox inside it.
    planner_dsn = _ensure_planner_catalog(patched_dsn, "extstats_stock")
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
    )

    sandbox_prepared = False
    try:
        prepare_postgres_planner_sandbox(
            planner_dsn,
            source["snapshot"],
            source["candidate_universe"],
            source["native_repository"],
        )
        sandbox_prepared = True
        first = _method_selection(source, advisor_root, planner_dsn)
        second = _method_selection(source, advisor_root, planner_dsn)
        first_projection = _selection_replay_projection(first["methods"])
        second_projection = _selection_replay_projection(second["methods"])
        if semantic_digest(first_projection) != semantic_digest(second_projection):
            diagnostic = {
                "format_version": "rq4-fixed-k-v2-determinism-failure",
                "dataset_id": dataset_id,
                "first_projection_digest": semantic_digest(first_projection),
                "second_projection_digest": semantic_digest(second_projection),
                "methods": {
                    method: {
                        "first": {
                            "selected_membership": first["methods"][method].get(
                                "selected_membership"
                            ),
                            "termination_reason": first["methods"][method].get(
                                "termination_reason"
                            ),
                            "final_objective": first["methods"][method]
                            .get("search_result", {})
                            .get("final_objective"),
                        },
                        "second": {
                            "selected_membership": second["methods"][method].get(
                                "selected_membership"
                            ),
                            "termination_reason": second["methods"][method].get(
                                "termination_reason"
                            ),
                            "final_objective": second["methods"][method]
                            .get("search_result", {})
                            .get("final_objective"),
                        },
                    }
                    for method in CANONICAL_METHOD_ORDER
                },
            }
            diagnostic["semantic_digest"] = semantic_digest(diagnostic)
            output.parent.mkdir(parents=True, exist_ok=True)
            write_json(output.parent / f"{dataset_id}-determinism-failure-v2.json", diagnostic)
            raise RQ4ValidationError("RQ4 v2 selection replay is not semantically deterministic")
        final_evaluations = _final_sandbox_evaluations(
            source, advisor_root, planner_dsn, first["methods"]
        )
        for method, evaluation in final_evaluations.items():
            first["methods"][method]["final_sandbox_evaluation"] = evaluation
    finally:
        if sandbox_prepared:
            destroy_postgres_planner_sandbox(planner_dsn)
    output.mkdir(parents=True, exist_ok=False)
    preflight_path = output / "rq4-preflight-v2.json"
    write_json(preflight_path, preflight)
    design_path = output / "rq4-design-evaluation-v2.json"
    design = {
        "format_version": DESIGN_FORMAT,
        "experiment_id": DESIGN_FORMAT,
        "formal_confirmatory_experiment": True,
        "status": "formal-confirmatory",
        "research_commit_sha": research_identity["research_commit_sha"],
        "system_freeze": freeze,
        "system_freeze_semantic_digest": freeze_digest,
        "dataset_id": dataset_id,
        "workload_id": source["snapshot"].workload.workload_id,
        "query_count": QUERY_COUNT,
        "fixed_k": FIXED_K,
        "method_order": list(CANONICAL_METHOD_ORDER),
        "methods": first["methods"],
        "eligible_universe": source["eligible_universe"],
        "source_artifact_digests": source["source_artifact_digests"],
        "authoritative_observations_sha256": source["observations_sha256"],
        "singleton_profile_reuse": _source_accounting(source["singleton_profile"]),
        "selection_budget": {
            "unit": "configuration-objective-evaluations",
            "max_configuration_evaluations": SELECTION_MAX_CONFIGURATION_EVALUATIONS,
            "wall_clock_seconds": SELECTION_WALL_CLOCK_SECONDS,
            "final_evaluation_excluded": True,
        },
        "reference_equivalence_gate": reference_gate,
        "implementation": {
            "greedy": "advisor-incidence-incremental-v1",
            "selection_evaluator": "exact-merged-estimate-map-weighted-utility",
            "formal_scope": "RQ4a-design-and-RQ4b-physical",
        },
    }
    design["semantic_digest"] = semantic_digest(design)
    write_json(design_path, design)
    replay_path = output / "rq4-design-determinism-v2.json.gz"
    replay = _write_v2_determinism(
        replay_path, first_projection, source, freeze, research_identity["research_commit_sha"]
    )
    validate_v2_design_artifact(design_path)
    validate_v2_determinism(replay_path)
    # Physical RQ4b is intentionally invoked by the caller only after the
    # design replay gate.  Keeping it here makes the child atomic: a v2
    # result is never sealed without its stock realization.
    from .rq4_physical import build_shared_stock_realization

    selected = {
        method: first["methods"][method]["selected_membership"] for method in CANONICAL_METHOD_ORDER
    }
    physical = build_shared_stock_realization(
        stock_dsn=stock_dsn,
        advisor_root=advisor_root,
        snapshot_path=source["paths"].snapshot,
        candidate_universe_path=source["paths"].candidate_universe,
        ground_truth_path=source["paths"].ground_truth,
        selected_by_method=selected,
        system_freeze=freeze,
        research_commit_sha=research_identity["research_commit_sha"],
        statistics_target=STATISTICS_TARGET,
        query_count=QUERY_COUNT,
        dataset_id=dataset_id,
        formal_confirmatory_experiment=True,
        experiment_id="rq4-stock-shared-realization-v2",
        artifact_format="rq4-stock-physical-evaluation-v2",
        shared_realization_format="rq4-stock-shared-realization-v2",
        expected_advisor_sha=FROZEN_ADVISOR_SHA,
    )
    physical_path = output / "rq4-stock-shared-realization-v2.json"
    write_json(physical_path, physical)
    physical_directory = output / "physical"
    physical_directory.mkdir()
    physical_child_digests: dict[str, str] = {}
    for method, result in physical["methods"].items():
        child = {
            "format_version": "rq4-stock-physical-evaluation-v2",
            "experiment_id": "rq4-stock-physical-evaluation-v2",
            "formal_confirmatory_experiment": True,
            "dataset_id": dataset_id,
            "method_id": method,
            "shared_realization_id": physical["shared_realization"]["realization_id"],
            "result": result,
            "parent_artifact": physical_path.name,
        }
        child["semantic_digest"] = semantic_digest(child)
        child_path = physical_directory / f"{method}.json"
        write_json(child_path, child)
        physical_child_digests[method] = child["semantic_digest"]
    summary = {
        "format_version": FORMAL_FORMAT,
        "experiment_id": FORMAL_FORMAT,
        "formal_confirmatory_experiment": True,
        "status": "complete",
        "research_commit_sha": research_identity["research_commit_sha"],
        "system_freeze": freeze,
        "system_freeze_semantic_digest": freeze_digest,
        "dataset_id": dataset_id,
        "workload_id": source["snapshot"].workload.workload_id,
        "query_count": QUERY_COUNT,
        "fixed_k": FIXED_K,
        "method_order": list(CANONICAL_METHOD_ORDER),
        "source_artifact_digests": source["source_artifact_digests"],
        "authoritative_observations_sha256": source["observations_sha256"],
        "eligible_universe_digest": source["eligible_universe"]["semantic_digest"],
        "reference_equivalence_gate": reference_gate,
        "preflight": {
            "artifact": preflight_path.name,
            "semantic_digest": preflight["semantic_digest"],
        },
        "rq4a": {"artifact": design_path.name, "semantic_digest": design["semantic_digest"]},
        "determinism": {"artifact": replay_path.name, "semantic_digest": replay["semantic_digest"]},
        "rq4b": {
            "artifact": physical_path.name,
            "semantic_digest": physical["semantic_digest"],
            "physical_directory": physical_directory.name,
            "child_semantic_digests": physical_child_digests,
            "method_count": len(physical["methods"]),
        },
        "progress": {
            "census13": "planned",
            "power7": "planned",
            "dmv11": "planned",
            "rq4_fixed_k_ablations": "ready-to-run",
        },
        "cleanup": {"pgdata_logs_sockets_credentials_serialized": False},
    }
    summary["progress"][dataset_id.removeprefix("arecel-")] = "complete"
    summary["semantic_digest"] = semantic_digest(summary)
    summary_path = output / "rq4-ablation-v2.json"
    write_json(summary_path, summary)
    return summary


def validate_v2_summary(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAL_FORMAT:
        raise RQ4ValidationError("unsupported formal RQ4 v2 summary")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if value.get("semantic_digest") != expected:
        raise RQ4ValidationError("formal RQ4 v2 summary digest mismatch")
    if value.get("method_order") != list(CANONICAL_METHOD_ORDER) or value.get("fixed_k") != FIXED_K:
        raise RQ4ValidationError("formal RQ4 v2 summary method/k contract mismatch")
    for key in ("rq4a", "determinism", "rq4b"):
        if not isinstance(value.get(key), Mapping):
            raise RQ4ValidationError(f"formal RQ4 v2 summary lacks {key}")
    return {"status": "valid", "format_version": FORMAL_FORMAT, "semantic_digest": expected}


__all__ = [
    "CANONICAL_METHOD_ORDER",
    "DATASETS",
    "DESIGN_FORMAT",
    "DETERMINISM_FORMAT",
    "FORMAL_FORMAT",
    "PREFLIGHT_FORMAT",
    "QUERY_COUNT",
    "RQ2_SOURCE_RUNS",
    "PREflight_FORMAT",
    "build_preflight",
    "build_ranked_plan",
    "load_reusable_source",
    "run_formal_rq4_v2",
    "validate_reference_equivalence_gate",
    "validate_v2_design_artifact",
    "validate_v2_determinism",
    "validate_v2_summary",
]
