"""Forest10 formal RQ4 fixed-k canary.

This module is deliberately an orchestration layer.  Selection objectives and
planner estimates come from the frozen advisor/PostgreSQL backend; native
statistics realization and full-data EXPLAINs come from the shared physical
runner.  The module binds those two evidence layers into one dataset-scoped
artifact without changing advisor or PostgreSQL semantics.
"""

from __future__ import annotations

import copy
import re
import statistics
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from .arecel_truth import authoritative_truth_spec
from .datasets import forest10
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_forest10
from .postgres_lab import _read_identity, reinit_role, role_spec, stop_role
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq1_canary import _ensure_planner_catalog
from .rq4_ablation import (
    INFORMATION_ACCESS_POLICY,
    LEGACY_METHOD_IDS,
    METHOD_IDS,
    RANDOM_SEEDS,
    EvaluationBudget,
    RQ4ValidationError,
    run_rq4_ablation,
)
from .rq4_determinism import build_design_determinism_smoke_artifact
from .rq4_physical import (
    _runtime_free,
    build_shared_stock_realization,
    validate_shared_stock_realization,
)
from .rq4_postgres import (
    FrozenPostgresRQ4Backend,
    RQ4ArtifactPaths,
    profile_singleton_utility,
)
from .system_freeze import (
    DEFAULT_SYSTEM_FREEZE_PATH,
    STOCK_POSTGRES_SHA,
    load_system_freeze,
)

FORMAL_FORMAT = "rq4-forest10-fixed-k-v2"
LEGACY_FORMAL_FORMAT = "rq4-forest10-fixed-k-v1"
DESIGN_FORMAT = "rq4-design-evaluation-v1"
FIXED_K = 4
QUERY_COUNT = 10_000
SEED_IDENTIFIER = 123
SETSEED_SQL = "SELECT setseed(1.0 / 123)"
SELECTION_MAX_CONFIGURATION_EVALUATIONS = 2_000
SELECTION_WALL_CLOCK_SECONDS = 3_600.0
CANONICAL_METHOD_ORDER = tuple(f"random-k-seed-{seed}" for seed in RANDOM_SEEDS) + tuple(
    method for method in METHOD_IDS if method != "random-k"
)
METHOD_ORDER = tuple(f"random-k-seed-{seed}" for seed in RANDOM_SEEDS) + tuple(
    method for method in LEGACY_METHOD_IDS if method != "random-k"
)
_RUN_ID = re.compile(r"^[0-9a-f]{24}$")


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path.resolve())


def _forest_input_paths(research_root: Path) -> tuple[RQ4ArtifactPaths, dict[str, Any]]:
    """Locate the current frozen-advisor Forest10 source run.

    The canonical compact summary is committed; its source run is intentionally
    under ``.runtime`` and is checked against the summary/manifest before use.
    """

    summary_path = (
        research_root
        / "experiments/arecel-forest10/rq1-confirmatory/advisor-canonical-k8"
        / "canonical-k8-summary-v1.json"
    )
    summary = read_json(summary_path)
    run_directory = Path(summary["run_directory"])
    if not run_directory.is_absolute():
        run_directory = research_root / run_directory
    manifest_path = run_directory / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest.get("benchmark_id") != forest10.BENCHMARK_ID:
        raise RQ4ValidationError("Forest10 source run benchmark identity mismatch")
    if manifest.get("advisor_commit_sha") != FROZEN_ADVISOR_SHA:
        raise RQ4ValidationError("Forest10 source run is not from the frozen advisor")
    if manifest.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise RQ4ValidationError("Forest10 source run is not from the frozen patched PostgreSQL")
    artifacts = manifest.get("artifacts", {})
    required = ("snapshot", "candidate_universe", "native_repository", "ground_truth")
    resolved: dict[str, Path] = {}
    for name in required:
        record = artifacts.get(name)
        if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
            raise RQ4ValidationError(f"Forest10 source manifest lacks {name}")
        path = run_directory / record["path"]
        if not path.exists():
            raise FileNotFoundError(path)
        resolved[name] = path
    source = {
        "summary_path": _relative(summary_path, research_root),
        "summary_semantic_digest": summary["semantic_digest"],
        "run_directory": _relative(run_directory, research_root),
        "run_id": manifest.get("run_id"),
        "manifest_semantic_digest": semantic_digest(manifest),
        "artifacts": {
            name: {
                "logical_path": _relative(path, research_root),
                "semantic_digest": artifacts[name].get("semantic_digest"),
            }
            for name, path in resolved.items()
        },
    }
    return RQ4ArtifactPaths(**resolved), source


def _truth_binding(research_root: Path) -> dict[str, Any]:
    spec = authoritative_truth_spec(forest10.BENCHMARK_ID, research_root)
    observations = Path(spec["observations_path"])
    audit_path = research_root / "truth/arecel/forest10/audit-v1.json"
    audit = read_json(audit_path)
    policy = read_json(research_root / "paper/benchmark-truth-policy-v1.json")
    policy_entry = next(
        item for item in policy["datasets"] if item["dataset_id"] == forest10.BENCHMARK_ID
    )
    if policy_entry["status"] != "validated-provenance":
        raise RQ4ValidationError("Forest10 truth policy status changed unexpectedly")
    if audit.get("observation_sha256") != spec["observations_sha256"]:
        raise RQ4ValidationError("Forest10 truth audit/observation SHA mismatch")
    return {
        "source_kind": "authoritative-external-exact",
        "policy_status": policy_entry["status"],
        "policy_identity": policy["policy_identity"],
        "policy_semantic_digest": semantic_digest(policy),
        "authority": spec["authority"],
        "source_revision": spec["source_revision"],
        "dataset_identity": spec["dataset_identity"],
        "workload_id": "arecel_forest10_test_v1",
        "query_count": spec["query_count"],
        "authoritative_observations_sha256": spec["observations_sha256"],
        "authoritative_observations_semantic_digest": audit["observation_semantic_digest"],
        "audit_semantic_digest": audit["observation_semantic_digest"],
        "observations_logical_path": _relative(observations, research_root),
    }


def _backend_evidence(backend: FrozenPostgresRQ4Backend) -> dict[str, Any]:
    truth = backend.ground_truth
    snapshot = backend.snapshot
    universe = backend.candidate_universe
    native = backend.native_repository
    truth_vector = sorted((item.query_id, int(item.cardinality)) for item in truth.truths)
    return {
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "snapshot_semantic_digest": snapshot.semantic_digest,
        "candidate_universe_semantic_digest": universe.semantic_digest,
        "native_repository_semantic_digest": native.semantic_digest,
        "ground_truth_semantic_digest": truth.computed_semantic_digest,
        "bound_truth_source_kind": getattr(truth.source, "value", str(truth.source)),
        "workload_id": snapshot.workload.workload_id,
        "workload_query_count": len(snapshot.workload.queries),
        "positive_weight_query_count": len(
            [query for query in snapshot.workload.queries if query.weight > 0]
        ),
        "cardinality_vector_digest": semantic_digest(truth_vector),
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "hypothetical_registration": "backend-local-catalogless-postgresql",
    }


def _run_selection_bundle(
    *,
    advisor_root: Path,
    planner_dsn: str,
    paths: RQ4ArtifactPaths,
    singleton_scores: Mapping[str, float] | None = None,
    include_singleton_profile: bool,
) -> dict[str, Any]:
    snapshot = _load_snapshot(paths.snapshot)
    query_ids = tuple(query.query_id for query in snapshot.workload.queries if query.weight > 0)
    if len(query_ids) != QUERY_COUNT:
        raise RQ4ValidationError("Forest10 RQ4 requires all 10,000 positive-weight queries")
    with FrozenPostgresRQ4Backend(
        advisor_root=advisor_root,
        patched_dsn=planner_dsn,
        paths=paths,
        query_ids=query_ids,
    ) as backend:
        eligible = backend.eligible_universe()
        signals = backend.sample_side_signals(eligible)
        candidate_ids = tuple(item["candidate_id"] for item in eligible["eligible_candidates"])
        if include_singleton_profile:
            singleton, singleton_accounting = profile_singleton_utility(backend, candidate_ids)
        else:
            if singleton_scores is None:
                raise RQ4ValidationError("random replicate is missing frozen singleton signal map")
            singleton = dict(singleton_scores)
            singleton_accounting = None
        base = run_rq4_ablation(
            eligible_universe=eligible,
            backend=backend,
            fixed_k=FIXED_K,
            mode="fixed_k_quality",
            budget=EvaluationBudget(
                SELECTION_MAX_CONFIGURATION_EVALUATIONS, SELECTION_WALL_CLOCK_SECONDS
            ),
            random_seed=RANDOM_SEEDS[0],
            singleton_profile_accounting=singleton_accounting,
            singleton_utility=singleton,
            method_ids=METHOD_IDS if include_singleton_profile else ("random-k",),
            **signals,
        )
        merged_methods: dict[str, Any] = {}
        if include_singleton_profile:
            first_random = copy.deepcopy(base["methods"]["random-k"])
            first_random["method_id"] = "random-k-seed-1"
            first_random["replicate_seed"] = 1
            merged_methods["random-k-seed-1"] = first_random
            for method in METHOD_IDS:
                if method != "random-k":
                    result = copy.deepcopy(base["methods"][method])
                    result["method_id"] = method
                    merged_methods[method] = result
            for seed in RANDOM_SEEDS[1:]:
                replicate = run_rq4_ablation(
                    eligible_universe=eligible,
                    backend=backend,
                    fixed_k=FIXED_K,
                    mode="fixed_k_quality",
                    budget=EvaluationBudget(
                        SELECTION_MAX_CONFIGURATION_EVALUATIONS, SELECTION_WALL_CLOCK_SECONDS
                    ),
                    random_seed=seed,
                    singleton_profile_accounting=None,
                    singleton_utility=singleton,
                    method_ids=("random-k",),
                    **signals,
                )
                result = copy.deepcopy(replicate["methods"]["random-k"])
                result["method_id"] = f"random-k-seed-{seed}"
                result["replicate_seed"] = seed
                merged_methods[f"random-k-seed-{seed}"] = result
            method_order = list(CANONICAL_METHOD_ORDER)
        else:
            result = copy.deepcopy(base["methods"]["random-k"])
            result["method_id"] = f"random-k-seed-{base['methods']['random-k']['random_seed']}"
            result["replicate_seed"] = base["methods"]["random-k"]["random_seed"]
            merged_methods[result["method_id"]] = result
            method_order = [result["method_id"]]
        comparison = copy.deepcopy(base)
        comparison["method_order"] = method_order
        comparison["methods"] = merged_methods
        comparison["random_seed_policy"] = list(RANDOM_SEEDS)
        comparison["fixed_k"] = FIXED_K
        evidence = _backend_evidence(backend)
        return {
            "eligible_universe": eligible,
            "signals": signals,
            "singleton_scores": singleton,
            "singleton_profile_accounting": singleton_accounting,
            "backend_evidence": evidence,
            "comparison": comparison,
        }


def _load_snapshot(path: Path) -> Any:
    from extstats_advisor.snapshot.bundle import load_snapshot

    return load_snapshot(path)


def _compact_trace(value: Any) -> Any:
    if isinstance(value, Mapping):
        result = {str(key): _compact_trace(item) for key, item in value.items()}
        metadata = result.get("backend_metadata")
        if isinstance(metadata, dict) and "planner_estimates" in metadata:
            estimates = metadata.pop("planner_estimates")
            metadata["planner_estimates_semantic_digest"] = semantic_digest(estimates)
            if result.get("purpose") != "independent-final-evaluation":
                result["planner_estimates"] = None
            else:
                result["planner_estimates"] = estimates
        return result
    if isinstance(value, list):
        return [_compact_trace(item) for item in value]
    return value


def _compact_comparison(comparison: Mapping[str, Any]) -> dict[str, Any]:
    return _compact_trace(copy.deepcopy(dict(comparison)))


def _method_metrics(comparison: Mapping[str, Any], baseline: float) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for method in comparison["method_order"]:
        result = comparison["methods"][method]
        evaluation = result.get("evaluation", {})
        objective = evaluation.get("sandbox_objective")
        metrics[method] = {
            "selected_membership": list(result.get("selected_membership", [])),
            "object_count": len(result.get("selected_membership", [])),
            "sandbox_objective": objective,
            "delta_vs_empty_configuration": (
                float(baseline) - float(objective) if objective is not None else None
            ),
            "configuration_objective_evaluations": result.get("accounting", {})
            .get("total", {})
            .get("configuration_objective_evaluations"),
            "postgresql_planner_query_calls": result.get("accounting", {})
            .get("total", {})
            .get("postgresql_planner_query_calls"),
            "preprocessing_wall_clock_seconds": result.get("selection_preprocessing", {}).get(
                "wall_clock_seconds"
            ),
            "selection_wall_clock_seconds": result.get("accounting", {})
            .get("selection", {})
            .get("elapsed_wall_clock_seconds"),
            "total_design_time_wall_clock_seconds": result.get("wall_time_seconds"),
            "termination_status": result.get("status"),
            "termination_or_censoring_reason": result.get("termination_or_censoring_reason"),
        }
    return metrics


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    return 1.0 if not a and not b else len(a & b) / len(a | b)


def _overlap(comparison: Mapping[str, Any]) -> dict[str, Any]:
    methods = comparison["method_order"]
    pairwise: dict[str, float] = {}
    for index, left in enumerate(methods):
        for right in methods[index + 1 :]:
            pairwise[f"{left}__{right}"] = _jaccard(
                comparison["methods"][left]["selected_membership"],
                comparison["methods"][right]["selected_membership"],
            )

    def lookup(left: str, right: str) -> float | None:
        return pairwise.get(f"{left}__{right}", pairwise.get(f"{right}__{left}"))

    return {
        "pairwise_jaccard": pairwise,
        "greedy_vs_singleton": lookup("greedy-ADD", "singleton-utility-top-k"),
        "greedy_vs_frequency": lookup("greedy-ADD", "workload-frequency-top-k"),
        "greedy_vs_native_payload_size": lookup("greedy-ADD", "native-payload-size-top-k")
        if "native-payload-size-top-k" in methods
        else lookup("greedy-ADD", "dependency-correlation-top-k"),
        # Preserve the historical derived field as an alias; new reports use
        # greedy_vs_native_payload_size.
        "greedy_vs_correlation": lookup("greedy-ADD", "native-payload-size-top-k")
        if "native-payload-size-top-k" in methods
        else lookup("greedy-ADD", "dependency-correlation-top-k"),
    }


def _random_aggregate(metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    values = [
        float(metrics[f"random-k-seed-{seed}"]["sandbox_objective"])
        for seed in RANDOM_SEEDS
        if metrics[f"random-k-seed-{seed}"]["sandbox_objective"] is not None
    ]
    if len(values) != len(RANDOM_SEEDS):
        raise RQ4ValidationError("all five random RQ4a replicates must have objectives")
    return {
        "seeds": list(RANDOM_SEEDS),
        "mean": statistics.mean(values),
        "std": statistics.pstdev(values),
        "min": min(values),
        "max": max(values),
        "individual_objectives": values,
    }


def _write_physical_children(
    *,
    shared: Mapping[str, Any],
    output_directory: Path,
    research_root: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    children: dict[str, Any] = {}
    digests: dict[str, str] = {}
    common = {
        key: value for key, value in shared.items() if key not in {"methods", "semantic_digest"}
    }
    for method in CANONICAL_METHOD_ORDER:
        if method not in shared["methods"]:
            raise RQ4ValidationError(f"shared physical realization lacks {method}")
        child = copy.deepcopy(common)
        child.update(
            {
                "experiment_id": "rq4-stock-physical-evaluation-v1",
                "formal_confirmatory_experiment": True,
                "status": "formal-confirmatory-child",
                "method_id": method,
                "methods": {method: copy.deepcopy(shared["methods"][method])},
            }
        )
        child["semantic_digest"] = semantic_digest(_runtime_free(child))
        path = output_directory / "physical" / f"{method}.json"
        write_json(path, child)
        validation = validate_shared_stock_realization(path)
        children[method] = {
            "logical_path": _relative(path, research_root),
            "semantic_digest": child["semantic_digest"],
            "validation": validation,
            "metrics": child["methods"][method]["metrics"],
            "selected_membership": child["methods"][method]["selected_membership"],
            "physical_object_count": len(child["methods"][method]["physical_statistics"]),
        }
        digests[method] = child["semantic_digest"]
    return children, digests


def _input_summary(
    paths: RQ4ArtifactPaths, source: Mapping[str, Any], research_root: Path
) -> dict[str, Any]:
    return {
        **dict(source),
        "source_paths": {
            "snapshot": _relative(paths.snapshot, research_root),
            "candidate_universe": _relative(paths.candidate_universe, research_root),
            "native_repository": _relative(paths.native_repository, research_root),
            "ground_truth": _relative(paths.ground_truth, research_root),
        },
        "source_sha256": {
            name: sha256_file(path)
            for name, path in {
                "snapshot_manifest": paths.snapshot / "manifest.json",
                "candidate_universe": paths.candidate_universe,
                "native_repository_manifest": paths.native_repository / "manifest.json",
                "ground_truth": paths.ground_truth,
            }.items()
        },
    }


def run_forest10_fixed_k(
    *,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    data_root: Path | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
) -> dict[str, Any]:
    """Run only the Forest10 formal RQ4 fixed-k canary."""

    if output.exists():
        raise FileExistsError(output)
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    system_freeze = load_system_freeze(DEFAULT_SYSTEM_FREEZE_PATH)
    verify_frozen_systems(advisor_root, patched_postgres_root)
    stock_identity = _read_identity(role_spec("stock"))
    if stock_identity.get("source_commit_sha") != STOCK_POSTGRES_SHA:
        raise RQ4ValidationError("stock lab is not on the frozen PostgreSQL source SHA")
    paths, source = _forest_input_paths(research_root)
    truth = _truth_binding(research_root)
    dataset_metadata = forest10.inspect(data_root)
    runtime_output = output.parent
    runtime_output.mkdir(parents=True, exist_ok=True)

    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
    )
    from extstats_advisor.native_stats import load_native_stats_repository

    planner_dsn = ""
    sandbox_prepared = False
    first: dict[str, Any] | None = None
    replay: dict[str, Any] | None = None
    try:
        reinit_role("patched")
        planner_dsn = _ensure_planner_catalog(patched_dsn, role_spec("stock").database)
        snapshot = _load_snapshot(paths.snapshot)
        universe = load_candidate_universe(paths.candidate_universe, snapshot)
        native = load_native_stats_repository(paths.native_repository)
        prepare_postgres_planner_sandbox(planner_dsn, snapshot, universe, native)
        sandbox_prepared = True

        first = _run_selection_bundle(
            advisor_root=advisor_root,
            planner_dsn=planner_dsn,
            paths=paths,
            include_singleton_profile=True,
        )
        try:
            replay = build_design_determinism_smoke_artifact(
                run_once=lambda: _run_selection_bundle(
                    advisor_root=advisor_root,
                    planner_dsn=planner_dsn,
                    paths=paths,
                    include_singleton_profile=True,
                )["comparison"],
                candidate_universe_digest=first["eligible_universe"]["semantic_digest"],
                system_freeze=system_freeze,
                research_commit_sha=research_identity["research_commit_sha"],
                fixture={
                    "dataset_id": forest10.BENCHMARK_ID,
                    "workload_id": "arecel_forest10_test_v1",
                    "query_count": QUERY_COUNT,
                    "formal_scope": "Forest10 RQ4 fixed-k canary",
                },
            )
        except RQ4ValidationError as exc:
            diagnostic = {
                "format_version": "rq4-forest10-replay-failure-v1",
                "experiment_id": "rq4-forest10-fixed-k-replay-failure",
                "status": "failed-closed",
                "formal_confirmatory_experiment": False,
                "failure": str(exc),
                "research_commit_sha": research_identity["research_commit_sha"],
                "system_freeze_semantic_digest": semantic_digest(system_freeze),
                "dataset_id": forest10.BENCHMARK_ID,
                "workload_id": "arecel_forest10_test_v1",
                "candidate_universe_semantic_digest": first["eligible_universe"]["semantic_digest"],
                "selection_budget": {
                    "unit": "configuration-objective-evaluations",
                    "max_configuration_evaluations": SELECTION_MAX_CONFIGURATION_EVALUATIONS,
                    "wall_clock_seconds": SELECTION_WALL_CLOCK_SECONDS,
                },
                "first_run_method_statuses": {
                    method: result.get("status")
                    for method, result in first["comparison"]["methods"].items()
                },
                "first_run_comparison_semantic_digest": semantic_digest(first["comparison"]),
                "rq4b_started": False,
            }
            diagnostic["semantic_digest"] = semantic_digest(diagnostic)
            write_json(runtime_output / "rq4-design-determinism-failure-v1.json", diagnostic)
            raise
    finally:
        if sandbox_prepared:
            destroy_postgres_planner_sandbox(planner_dsn)

    assert first is not None and replay is not None
    if replay.get("checks", {}).get("semantic_projection_stable") is not True:
        raise RQ4ValidationError("formal Forest10 RQ4a deterministic replay failed")

    baseline = float(first["singleton_profile_accounting"]["baseline_objective"])
    rq4a_metrics = _method_metrics(first["comparison"], baseline)
    if any(
        result["termination_status"] not in {"complete", "at-most-k-local-optimum"}
        for result in rq4a_metrics.values()
    ):
        raise RQ4ValidationError("formal Forest10 RQ4a has a censored or infeasible method")
    rq4a_path = runtime_output / "rq4-design-evaluation-v1.json"
    replay_path = runtime_output / "rq4-design-determinism-smoke-v1.json.gz"
    write_json(replay_path, replay)
    rq4a_child = {
        "format_version": DESIGN_FORMAT,
        "experiment_id": DESIGN_FORMAT,
        "formal_confirmatory_experiment": True,
        "status": "formal-confirmatory",
        "research_commit_sha": research_identity["research_commit_sha"],
        "system_freeze": system_freeze,
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "dataset": {
            "dataset_id": forest10.BENCHMARK_ID,
            "content_identity": dataset_metadata["dataset_content_identity"],
        },
        "workload": {
            "workload_id": "arecel_forest10_test_v1",
            "query_count": QUERY_COUNT,
        },
        "truth_binding": truth,
        "backend_evidence": first["backend_evidence"],
        "eligible_universe": first["eligible_universe"],
        "selection_signals": {
            "workload_frequency": first["signals"]["workload_frequency"],
            "native_payload_size": first["signals"]["native_payload_size"],
            "singleton_utility": first["singleton_scores"],
            "information_access_policy": INFORMATION_ACCESS_POLICY,
            "tie_breaking": "score descending, then canonical static precedence/candidate ID",
            "random_policy": "IDs plus pre-registered seed only",
            "singleton_profile_accounting": first["singleton_profile_accounting"],
        },
        "comparison_mode": "fixed_k_quality",
        "fixed_k": FIXED_K,
        "comparison": _compact_comparison(first["comparison"]),
        "baseline_objective": baseline,
        "metrics": rq4a_metrics,
        "random_aggregate": _random_aggregate(rq4a_metrics),
        "membership_overlap": _overlap(first["comparison"]),
        "deterministic_replay": {
            "artifact": _relative(replay_path, research_root),
            "artifact_sha256": sha256_file(replay_path),
            "compression": "gzip",
            "semantic_digest": replay["semantic_digest"],
            "replay_semantic_digests": replay["replay_semantic_digests"],
            "checks": replay["checks"],
        },
    }
    rq4a_child["semantic_digest"] = semantic_digest(rq4a_child)
    write_json(rq4a_path, rq4a_child)

    selected_by_method = {
        method: first["comparison"]["methods"][method]["selected_membership"]
        for method in CANONICAL_METHOD_ORDER
    }
    try:
        reinit_role("stock")
        load = load_forest10(
            stock_dsn,
            data_root=data_root,
            reset_disposable=True,
            statistics_target=100,
            seed_identifier=SEED_IDENTIFIER,
        )
        shared = build_shared_stock_realization(
            stock_dsn=stock_dsn,
            advisor_root=advisor_root,
            snapshot_path=paths.snapshot,
            candidate_universe_path=paths.candidate_universe,
            ground_truth_path=paths.ground_truth,
            selected_by_method=selected_by_method,
            system_freeze=system_freeze,
            research_commit_sha=research_identity["research_commit_sha"],
            statistics_target=100,
            query_count=QUERY_COUNT,
            dataset_id=forest10.BENCHMARK_ID,
            formal_confirmatory_experiment=True,
            experiment_id="rq4-stock-shared-realization-v1",
        )
        physical_children, child_digests = _write_physical_children(
            shared=shared,
            output_directory=runtime_output,
            research_root=research_root,
        )
    finally:
        stop_role("stock")
        stop_role("patched")

    artifact: dict[str, Any] = {
        "format_version": FORMAL_FORMAT,
        "experiment_id": FORMAL_FORMAT,
        "formal_confirmatory_experiment": True,
        "status": "complete",
        "research_commit_sha": research_identity["research_commit_sha"],
        "system_freeze": system_freeze,
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "dataset": {
            "dataset_id": forest10.BENCHMARK_ID,
            "content_identity": dataset_metadata["dataset_content_identity"],
            "rows": forest10.EXPECTED_ROWS,
            "schema_contract_id": forest10.SCHEMA_CONTRACT_ID,
        },
        "workload": {
            "workload_id": "arecel_forest10_test_v1",
            "query_count": QUERY_COUNT,
        },
        "truth_binding": {
            **truth,
            "bound_ground_truth_set_semantic_digest": first["backend_evidence"][
                "ground_truth_semantic_digest"
            ],
            "cardinality_vector_digest": first["backend_evidence"]["cardinality_vector_digest"],
        },
        "frozen_parameters": {
            "sample_rows": 10_000,
            "sample_seed": 42,
            "statistics_target": 100,
            "fixed_k": FIXED_K,
            "candidate_kinds": ["postgresql.mcv", "postgresql.dependencies"],
            "experiment_seed_identifier": SEED_IDENTIFIER,
            "postgresql_setseed_sql": SETSEED_SQL,
            "parameter_selection_basis": "pre-registered Forest10 RQ4 fixed-k canary protocol",
            "selection_budget": {
                "unit": "configuration-objective-evaluations",
                "max_configuration_evaluations": SELECTION_MAX_CONFIGURATION_EVALUATIONS,
                "wall_clock_seconds": SELECTION_WALL_CLOCK_SECONDS,
                "not_fixed_evaluation_budget_comparison": True,
            },
        },
        "source_inputs": _input_summary(paths, source, research_root),
        "candidate_universe": {
            "full_candidate_count": len(read_json(paths.candidate_universe)["candidates"]),
            "eligible_candidate_count": len(first["eligible_universe"]["eligible_candidates"]),
            "excluded_candidate_count": len(first["eligible_universe"]["excluded_candidates"]),
            "eligible_universe_semantic_digest": first["eligible_universe"]["semantic_digest"],
            "eligible_candidate_ids": [
                item["candidate_id"] for item in first["eligible_universe"]["eligible_candidates"]
            ],
            "excluded_candidates": first["eligible_universe"]["excluded_candidates"],
            "eligibility_frozen_before_utility": True,
        },
        "rq4a": {
            "evidence_role": "primary deterministic patched-sandbox design quality",
            "artifact": _relative(rq4a_path, research_root),
            "semantic_digest": rq4a_child["semantic_digest"],
            "metrics": rq4a_metrics,
            "random_aggregate": rq4a_child["random_aggregate"],
            "membership_overlap": rq4a_child["membership_overlap"],
            "deterministic_replay_passed": True,
        },
        "rq4b": {
            "evidence_role": "secondary controlled stock full-data physical consequence",
            "shared_realization_format": shared["shared_realization"]["format_version"],
            "shared_realization_id": shared["shared_realization"]["realization_id"],
            "union_candidate_ids": shared["shared_realization"]["union_candidate_ids"],
            "union_size": len(shared["shared_realization"]["union_candidate_ids"]),
            "analyze_count": shared["shared_realization"]["analyze_count"],
            "parent_realization_digest": semantic_digest(
                shared["shared_realization"]["physical_statistics"]
            ),
            "stock_load": load,
            "children": physical_children,
            "child_semantic_digests": child_digests,
            "metrics": {method: value["metrics"] for method, value in physical_children.items()},
        },
        "progress": {
            "forest10_rq4_fixed_k": "complete",
            "census13_rq4_fixed_k": "planned",
            "power7_rq4_fixed_k": "planned",
            "dmv11_rq4_fixed_k": "planned",
            "rq4_fixed_k_ablations": "ready-to-run",
            "rq4_fixed_evaluation_budget": "implementation-needed",
            "rq4_native_analyze_stability": "planned",
        },
        "cleanup": {
            "stock_cluster_stopped": True,
            "patched_cluster_stopped": True,
            "pgdata_logs_sockets_credentials_serialized": False,
        },
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output, artifact)
    validate_forest10_fixed_k_artifact(output)
    return artifact


def validate_forest10_fixed_k_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    format_version = artifact.get("format_version")
    if format_version not in {FORMAL_FORMAT, LEGACY_FORMAL_FORMAT}:
        raise RQ4ValidationError("unsupported Forest10 formal RQ4 format")
    method_order = CANONICAL_METHOD_ORDER if format_version == FORMAL_FORMAT else METHOD_ORDER
    expected = semantic_digest(
        {key: value for key, value in artifact.items() if key != "semantic_digest"}
    )
    if artifact.get("semantic_digest") != expected:
        raise RQ4ValidationError("Forest10 formal RQ4 semantic digest mismatch")
    if artifact.get("formal_confirmatory_experiment") is not True:
        raise RQ4ValidationError("Forest10 formal RQ4 artifact is not marked confirmatory")
    if artifact.get("status") != "complete":
        raise RQ4ValidationError("Forest10 formal RQ4 artifact is not complete")
    if artifact.get("dataset", {}).get("dataset_id") != forest10.BENCHMARK_ID:
        raise RQ4ValidationError("Forest10 formal RQ4 dataset mismatch")
    if artifact.get("workload", {}).get("query_count") != QUERY_COUNT:
        raise RQ4ValidationError("Forest10 formal RQ4 is not a full-workload result")
    truth = artifact.get("truth_binding", {})
    if truth.get("source_kind") != "authoritative-external-exact":
        raise RQ4ValidationError("formal Forest10 RQ4 did not use authoritative external truth")
    if truth.get("policy_status") != "validated-provenance":
        raise RQ4ValidationError("formal Forest10 RQ4 truth policy status drifted")
    universe = artifact.get("candidate_universe", {})
    if not universe.get("eligibility_frozen_before_utility"):
        raise RQ4ValidationError("formal Forest10 eligibility was not frozen before utility")
    if universe.get("eligible_candidate_count", 0) + universe.get(
        "excluded_candidate_count", 0
    ) != universe.get("full_candidate_count"):
        raise RQ4ValidationError("formal Forest10 candidate counts do not partition the universe")
    rq4a_path = Path(artifact["rq4a"]["artifact"])
    if not rq4a_path.is_absolute():
        rq4a_path = Path(__file__).resolve().parents[2] / rq4a_path
    rq4a = read_json(rq4a_path)
    if (
        rq4a.get("format_version") != DESIGN_FORMAT
        or rq4a.get("formal_confirmatory_experiment") is not True
    ):
        raise RQ4ValidationError("formal Forest10 RQ4a child is missing")
    if rq4a.get("semantic_digest") != artifact["rq4a"]["semantic_digest"]:
        raise RQ4ValidationError("formal Forest10 RQ4a child digest mismatch")
    if rq4a.get("comparison", {}).get("method_order") != list(method_order):
        raise RQ4ValidationError("formal Forest10 RQ4a method order mismatch")
    for method in method_order:
        result = rq4a["comparison"]["methods"].get(method)
        if not isinstance(result, Mapping):
            raise RQ4ValidationError(f"formal Forest10 RQ4a lacks {method}")
        if result.get("status") not in {"complete", "at-most-k-local-optimum"}:
            raise RQ4ValidationError(f"formal Forest10 RQ4a {method} is censored/infeasible")
        if not set(result.get("selected_membership", [])).issubset(
            set(universe["eligible_candidate_ids"])
        ):
            raise RQ4ValidationError(f"formal Forest10 RQ4a {method} selected an ineligible object")
    replay = rq4a.get("deterministic_replay", {})
    if replay.get("checks", {}).get("semantic_projection_stable") is not True:
        raise RQ4ValidationError("formal Forest10 RQ4a replay gate failed")
    children = artifact.get("rq4b", {}).get("children", {})
    if set(children) != set(method_order):
        raise RQ4ValidationError("formal Forest10 RQ4b child set is incomplete")
    for method, record in children.items():
        child_path = Path(record["logical_path"])
        if not child_path.is_absolute():
            child_path = Path(__file__).resolve().parents[2] / child_path
        child_validation = validate_shared_stock_realization(child_path)
        if child_validation["formal_confirmatory_experiment"] is not True:
            raise RQ4ValidationError(f"formal Forest10 RQ4b child {method} is not formal")
        child = read_json(child_path)
        if child.get("semantic_digest") != record.get("semantic_digest"):
            raise RQ4ValidationError(f"formal Forest10 RQ4b child {method} digest mismatch")
        physical = child["methods"][method]
        selected = set(rq4a["comparison"]["methods"][method]["selected_membership"])
        if set(physical["selected_membership"]) != selected:
            raise RQ4ValidationError(f"formal Forest10 {method} membership changed at deployment")
        if physical.get("post_drop_analyze_count") != 0:
            raise RQ4ValidationError(f"formal Forest10 {method} has post-DROP ANALYZE")
    progress = artifact.get("progress", {})
    if progress.get("rq4_fixed_k_ablations") != "ready-to-run":
        raise RQ4ValidationError("global RQ4 status was incorrectly completed by Forest10 only")
    return {
        "status": "valid",
        "format_version": format_version,
        "semantic_digest": expected,
        "dataset_id": forest10.BENCHMARK_ID,
        "method_count": len(method_order),
        "rq4a_replay": "pass",
        "rq4b_children": len(children),
        "global_rq4_status": progress["rq4_fixed_k_ablations"],
    }


__all__ = [
    "DESIGN_FORMAT",
    "FORMAL_FORMAT",
    "LEGACY_FORMAL_FORMAT",
    "METHOD_ORDER",
    "run_forest10_fixed_k",
    "validate_forest10_fixed_k_artifact",
]
