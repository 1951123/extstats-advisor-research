"""Audit the incidence-incremental singleton profiler against its old reference.

The harness deliberately reuses the frozen Advisor's snapshot, native payload,
planner session, utility provider, and q-error loss.  It does not implement a
second estimator.  Its only research responsibility is to bind both profiling
strategies to the same immutable source run and record the equivalence evidence.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from .pins import verify_git_sha, verify_research_repository
from .provenance import read_json, semantic_digest, sha256_file, write_json

FORMAT_VERSION = "advisor-singleton-incremental-equivalence-v1"
INCREMENTAL_ADVISOR_SHA = "9c93925ebbcda0dc3306e8e63d86ccd782ffc539"
CURRENT_INCREMENTAL_ADVISOR_SHA = "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3"
REFERENCE_ADVISOR_SHA = FROZEN_ADVISOR_SHA
HISTORICAL_PROFILE_DIGESTS = {
    "arecel-census13": "0e6330007b46ca57bc456183564c6cf236b5db5654883a3321bb9128f6f17ee9",
    "arecel-forest10": "80f515d9884e40caec8021dec8894cac2a866aa6901ae519eb5093471d5d9563",
    "arecel-power7": "d30f46440ba46ca97af815362e029788cc4552a8da4f1ab7d8f54267f485b776",
    "arecel-dmv11": "07e86d35e04d287814cca184a9bb18637e6346c95640265fbf5754f693d23c6e",
}
HISTORICAL_INCREMENTAL_FORMAT_VERSION = "advisor-singleton-incremental-historical-equivalence-v2"
MAX_VALIDATION_SECONDS = 300.0
RESEARCH_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_SOURCE_RUNS = {
    "arecel-census13": RESEARCH_ROOT
    / ".runtime/rq1-census13-canary/advisor-design/5c4b165c0c49cac00a1ff81a",
    "arecel-forest10": RESEARCH_ROOT
    / ".runtime/rq1-forest10-matched/advisor-design/2d6213e57c49975ef7a70c9c",
    "arecel-power7": RESEARCH_ROOT
    / ".runtime/rq1-power7-matched/advisor-design/35f7b8756b9bfed005536711",
    "arecel-dmv11": RESEARCH_ROOT
    / ".runtime/rq1-dmv11-matched/advisor-design/6fec0add37c8a4f4679d5053",
}

# These are immutable historical stage timings, not acceptance targets.  A
# missing value is intentionally a closed gate rather than an estimate guessed
# from a different dataset.
HISTORICAL_REFERENCE_SECONDS = {
    "arecel-forest10": 200.83157,
    "arecel-power7": 129.518826,
    "arecel-dmv11": 312.562326,
}


def _advisor_modules(advisor_root: Path) -> dict[str, Any]:
    import sys

    source = str(Path(advisor_root).resolve() / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
    from extstats_advisor.dbms.postgres.profiling import (
        profile_postgres_singletons,
        profile_postgres_singletons_full_workload_reference,
    )
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.optimization import (
        load_singleton_profile,
        validate_nonincident_estimates_unchanged,
    )
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.utility import WeightedWorkloadUtility
    from extstats_advisor.utility.loss import QErrorLoss

    return {
        "load_candidate_universe": load_candidate_universe,
        "PostgresPlannerSession": PostgresPlannerSession,
        "profile_postgres_singletons": profile_postgres_singletons,
        "profile_postgres_singletons_full_workload_reference": profile_postgres_singletons_full_workload_reference,
        "destroy_postgres_planner_sandbox": destroy_postgres_planner_sandbox,
        "prepare_postgres_planner_sandbox": prepare_postgres_planner_sandbox,
        "verify_postgres_planner_sandbox": verify_postgres_planner_sandbox,
        "load_ground_truth_set": load_ground_truth_set,
        "ArtifactGroundTruthProvider": ArtifactGroundTruthProvider,
        "load_native_stats_repository": load_native_stats_repository,
        "load_singleton_profile": load_singleton_profile,
        "load_snapshot": load_snapshot,
        "WeightedWorkloadUtility": WeightedWorkloadUtility,
        "QErrorLoss": QErrorLoss,
        "validate_nonincident_estimates_unchanged": validate_nonincident_estimates_unchanged,
    }


def _source_paths(source_run: Path) -> dict[str, Path]:
    return {
        "snapshot": source_run / "advisor-snapshot",
        "candidate_universe": source_run / "candidate-universe.json",
        "native_repository": source_run / "native-stats-repository",
        "ground_truth": source_run / "ground-truth-v1.json",
        "singleton_profile": source_run / "singleton-profile.json",
        "manifest": source_run / "manifest.json",
    }


def _load_source(source_run: Path, advisor_root: Path) -> dict[str, Any]:
    modules = _advisor_modules(advisor_root)
    paths = _source_paths(source_run)
    if any(not path.exists() for path in paths.values()):
        missing = [str(path) for path in paths.values() if not path.exists()]
        raise FileNotFoundError(f"source run is incomplete: {missing}")
    snapshot = modules["load_snapshot"](paths["snapshot"])
    universe = modules["load_candidate_universe"](paths["candidate_universe"], snapshot)
    repository = modules["load_native_stats_repository"](paths["native_repository"])
    truth = modules["load_ground_truth_set"](paths["ground_truth"], snapshot)
    profile = modules["load_singleton_profile"](paths["singleton_profile"])
    manifest = read_json(paths["manifest"])
    return {
        "modules": modules,
        "paths": paths,
        "snapshot": snapshot,
        "universe": universe,
        "repository": repository,
        "truth": truth,
        "profile": profile,
        "manifest": manifest,
    }


def _positive_query_ids(snapshot: Any) -> tuple[str, ...]:
    return tuple(query.query_id for query in snapshot.workload.queries if query.weight > 0)


def preflight_dataset(
    dataset_id: str,
    source_run: Path,
    *,
    advisor_root: Path,
    historical_reference_seconds: float | None = None,
) -> dict[str, Any]:
    """Compute validation cost from committed source artifacts only."""

    source = _load_source(source_run, advisor_root)
    snapshot = source["snapshot"]
    universe = source["universe"]
    repository = source["repository"]
    profile = source["profile"]
    query_ids = _positive_query_ids(snapshot)
    present = [item for item in repository.candidate_models if item.state == "present"]
    counts = {
        item.candidate_id: len(
            set(universe.query_ids_for_candidate(item.candidate_id)).intersection(query_ids)
        )
        for item in present
    }
    old_calls = len(query_ids) * (1 + len(present))
    incremental_calls = len(query_ids) + sum(counts.values())
    runtime = dict(profile.runtime_metadata)
    if runtime.get("planner_query_estimate_count") != old_calls:
        raise ValueError(f"{dataset_id} historical profile has inconsistent old call count")
    projected_incremental_seconds = None
    projected_total_seconds = None
    gate = "blocked-missing-historical-reference-wall-clock"
    if historical_reference_seconds is not None:
        projected_incremental_seconds = historical_reference_seconds * incremental_calls / old_calls
        projected_total_seconds = historical_reference_seconds + projected_incremental_seconds
        gate = (
            "eligible-within-300-second-validation-gate"
            if projected_total_seconds <= MAX_VALIDATION_SECONDS
            else "blocked-over-300-second-validation-gate"
        )
    return {
        "dataset_id": dataset_id,
        "source_run": str(source_run),
        "source_manifest_sha256": sha256_file(source["paths"]["manifest"]),
        "source_singleton_profile_semantic_digest": profile.computed_semantic_digest,
        "snapshot_semantic_digest": snapshot.semantic_digest,
        "candidate_universe_semantic_digest": universe.semantic_digest,
        "native_repository_semantic_digest": repository.semantic_digest,
        "ground_truth_semantic_digest": truth_digest(source["truth"]),
        "positive_query_count": len(query_ids),
        "candidate_count": len(universe.candidates),
        "present_candidate_count": len(present),
        "absent_candidate_count": len(universe.candidates) - len(present),
        "old_reference_planner_query_estimate_count": old_calls,
        "incremental_planner_query_estimate_count": incremental_calls,
        "saved_planner_query_estimate_count": old_calls - incremental_calls,
        "planner_query_reduction_fraction": (old_calls - incremental_calls) / old_calls,
        "candidate_incidence_counts": counts,
        "candidate_incidence_summary": _summary(tuple(counts.values())),
        "historical_reference_wall_clock_seconds": historical_reference_seconds,
        "projected_incremental_wall_clock_seconds": projected_incremental_seconds,
        "projected_total_validation_seconds": projected_total_seconds,
        "validation_gate": gate,
        "historical_profile_runtime_metadata": runtime,
    }


def _summary(values: tuple[int, ...]) -> dict[str, float | int]:
    if not values:
        return {"min": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0}
    ordered = sorted(values)
    index = max(0, int(0.95 * len(ordered) + 0.999999) - 1)
    median = (
        float(ordered[len(ordered) // 2])
        if len(ordered) % 2
        else (ordered[len(ordered) // 2 - 1] + ordered[len(ordered) // 2]) / 2
    )
    return {
        "min": min(ordered),
        "mean": sum(ordered) / len(ordered),
        "median": median,
        "p95": float(ordered[index]),
        "max": max(ordered),
    }


def truth_digest(truth: Any) -> str:
    return str(truth.semantic_digest or truth.computed_semantic_digest)


def _canonical_candidate_profiles(profile: Any) -> tuple[tuple[str, dict[str, Any]], ...]:
    return tuple(
        sorted(
            (
                candidate.candidate_id,
                candidate.to_dict(),
            )
            for candidate in profile.candidate_profiles
        )
    )


def run_equivalence(
    dataset_id: str,
    source_run: Path,
    *,
    patched_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output: Path,
) -> dict[str, Any]:
    """Run reference and incremental profiles on one patched PostgreSQL build."""

    verify_git_sha(advisor_root, INCREMENTAL_ADVISOR_SHA)
    verify_git_sha(patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA)
    research_identity = verify_research_repository(RESEARCH_ROOT)
    source = _load_source(source_run, advisor_root)
    modules = source["modules"]
    snapshot = source["snapshot"]
    universe = source["universe"]
    repository = source["repository"]
    truth = source["truth"]
    truth_semantic_digest = truth_digest(truth)
    utility = modules["WeightedWorkloadUtility"](
        snapshot.workload,
        modules["ArtifactGroundTruthProvider"](truth),
        modules["QErrorLoss"](),
    )
    prepared = modules["prepare_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    verification = modules["verify_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    reference_audit: dict[str, Any] = {}
    incremental_audit: dict[str, Any] = {}
    try:
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            reference = modules["profile_postgres_singletons_full_workload_reference"](
                planner,
                snapshot,
                universe,
                repository,
                utility,
                ground_truth_semantic_digest=truth_semantic_digest,
                estimate_audit=reference_audit,
            )
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            incremental = modules["profile_postgres_singletons"](
                planner,
                snapshot,
                universe,
                repository,
                utility,
                ground_truth_semantic_digest=truth_semantic_digest,
                estimate_audit=incremental_audit,
            )
    finally:
        modules["destroy_postgres_planner_sandbox"](patched_dsn)

    query_ids = _positive_query_ids(snapshot)
    ref_maps = reference_audit.get("singletons", {})
    inc_maps = incremental_audit.get("singletons", {})
    audit_records = []
    for candidate in repository.candidate_models:
        if candidate.state != "present":
            continue
        affected = tuple(
            query_id
            for query_id in query_ids
            if query_id in set(universe.query_ids_for_candidate(candidate.candidate_id))
        )
        modules["validate_nonincident_estimates_unchanged"](
            reference_audit["baseline"], ref_maps[candidate.candidate_id], affected
        )
        if ref_maps[candidate.candidate_id] != inc_maps[candidate.candidate_id]:
            raise ValueError(f"incremental estimate map differs for {candidate.candidate_id}")
        audit_records.append(
            {
                "candidate_id": candidate.candidate_id,
                "affected_query_count": len(affected),
                "nonincident_estimates_unchanged": True,
            }
        )

    semantic_equal = reference.computed_semantic_digest == incremental.computed_semantic_digest
    if not semantic_equal:
        raise ValueError("incremental singleton profile semantic digest differs from reference")
    audit_summary = {
        "candidate_count": len(audit_records),
        "all_nonincident_estimates_unchanged": all(
            record["nonincident_estimates_unchanged"] for record in audit_records
        ),
        "estimate_map_semantic_digest": semantic_digest(
            {"reference": reference_audit, "incremental": incremental_audit}
        ),
        "records": audit_records,
    }
    payload = {
        "format_version": FORMAT_VERSION,
        "status": "complete",
        "experiment_id": f"{FORMAT_VERSION}:{dataset_id}",
        "dataset_id": dataset_id,
        "protocol": {
            "reference_strategy": "full-workload-reference-v1",
            "incremental_strategy": "incidence-incremental-v1",
            "same_snapshot_universe_native_repository": True,
            "same_patched_postgresql_binary": True,
            "nonincident_estimate_invariant": "fail-closed-audited",
        },
        "research_repository": research_identity,
        "advisor": {
            "repository": "1951123/extstats-advisor",
            "reference_commit_sha": REFERENCE_ADVISOR_SHA,
            "incremental_commit_sha": INCREMENTAL_ADVISOR_SHA,
        },
        "patched_postgresql": {
            "repository": "1951123/postgresql-pgextadv",
            "commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "postgres_version": "16.14",
        },
        "source_run": {
            "path": str(source_run),
            "manifest_sha256": sha256_file(source["paths"]["manifest"]),
            "historical_singleton_profile_semantic_digest": source[
                "profile"
            ].computed_semantic_digest,
            "snapshot_semantic_digest": snapshot.semantic_digest,
            "candidate_universe_semantic_digest": universe.semantic_digest,
            "native_repository_semantic_digest": repository.semantic_digest,
            "ground_truth_semantic_digest": truth_semantic_digest,
        },
        "sandbox_verification": {
            "prepared": prepared.metadata.to_dict(),
            "verified": verification,
        },
        "reference": {
            "profile_semantic_digest": reference.computed_semantic_digest,
            "runtime_metadata": dict(reference.runtime_metadata),
        },
        "incremental": {
            "profile_semantic_digest": incremental.computed_semantic_digest,
            "runtime_metadata": dict(incremental.runtime_metadata),
        },
        "equivalence": {
            "semantic_output_equal": semantic_equal,
            "candidate_profiles_equal": reference.candidate_profiles
            == incremental.candidate_profiles,
            "frozen_order_equal": reference.frozen_ordered_candidate_ids
            == incremental.frozen_ordered_candidate_ids,
            "baseline_objective_equal": reference.baseline.objective
            == incremental.baseline.objective,
            "audit": audit_summary,
        },
    }
    payload["artifact_digest"] = semantic_digest(payload)
    write_json(output, payload)
    return inspect_artifact(output)


def inspect_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return {
        "format_version": value.get("format_version"),
        "status": value.get("status"),
        "dataset_id": value.get("dataset_id"),
        "artifact_digest": value.get("artifact_digest"),
        "semantic_output_equal": value.get("equivalence", {}).get("semantic_output_equal"),
        "incremental_planner_query_estimate_count": value.get("incremental", {})
        .get("runtime_metadata", {})
        .get("planner_query_estimate_count"),
    }


def run_historical_incremental_equivalence(
    dataset_id: str,
    source_run: Path,
    *,
    patched_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    output: Path,
) -> dict[str, Any]:
    """Run only the current incremental profiler against an immutable v1 oracle.

    This is intentionally not the old reference-plus-incremental experiment.
    The historical profile is loaded and validated as a semantic oracle; the
    live work only executes the current incidence-incremental profiler once.
    """

    verify_git_sha(advisor_root, CURRENT_INCREMENTAL_ADVISOR_SHA)
    verify_git_sha(patched_postgres_root, FROZEN_PATCHED_POSTGRES_SHA)
    research_identity = verify_research_repository(RESEARCH_ROOT)
    source = _load_source(source_run, advisor_root)
    manifest = source["manifest"]
    source_profile = source["profile"]
    if dataset_id not in HISTORICAL_PROFILE_DIGESTS:
        raise ValueError(f"no historical singleton oracle is registered for {dataset_id}")
    if manifest.get("benchmark_id") != dataset_id:
        raise ValueError("historical singleton source benchmark identity mismatch")
    if manifest.get("advisor_commit_sha") != REFERENCE_ADVISOR_SHA:
        raise ValueError("historical singleton source advisor identity mismatch")
    if manifest.get("patched_postgres_commit_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("historical singleton source PostgreSQL identity mismatch")
    historical_digest = HISTORICAL_PROFILE_DIGESTS[dataset_id]
    if source_profile.computed_semantic_digest != historical_digest:
        raise ValueError("historical singleton profile oracle digest mismatch")

    modules = source["modules"]
    snapshot = source["snapshot"]
    universe = source["universe"]
    repository = source["repository"]
    truth = source["truth"]
    truth_semantic_digest = truth_digest(truth)
    utility = modules["WeightedWorkloadUtility"](
        snapshot.workload,
        modules["ArtifactGroundTruthProvider"](truth),
        modules["QErrorLoss"](),
    )
    prepared = modules["prepare_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    verification = modules["verify_postgres_planner_sandbox"](
        patched_dsn, snapshot, universe, repository
    )
    incremental_audit: dict[str, Any] = {}
    try:
        with modules["PostgresPlannerSession"](
            patched_dsn, snapshot, universe, repository
        ) as planner:
            incremental = modules["profile_postgres_singletons"](
                planner,
                snapshot,
                universe,
                repository,
                utility,
                ground_truth_semantic_digest=truth_semantic_digest,
                estimate_audit=incremental_audit,
            )
    finally:
        modules["destroy_postgres_planner_sandbox"](patched_dsn)

    query_ids = _positive_query_ids(snapshot)
    present = [item for item in repository.candidate_models if item.state == "present"]
    audit_records = []
    for candidate in present:
        affected = tuple(
            query_id
            for query_id in query_ids
            if query_id in set(universe.query_ids_for_candidate(candidate.candidate_id))
        )
        modules["validate_nonincident_estimates_unchanged"](
            incremental_audit["baseline"],
            incremental_audit["singletons"][candidate.candidate_id],
            affected,
        )
        audit_records.append(
            {
                "candidate_id": candidate.candidate_id,
                "affected_query_count": len(affected),
                "nonincident_estimates_unchanged": True,
            }
        )

    runtime = dict(incremental.runtime_metadata)
    expected_calls = len(query_ids) + sum(
        record["affected_query_count"] for record in audit_records
    )
    equivalence = {
        "historical_identity_validated": True,
        "historical_profile_oracle_digest_equal": source_profile.computed_semantic_digest
        == historical_digest,
        "semantic_output_equal": incremental.computed_semantic_digest
        == source_profile.computed_semantic_digest,
        "candidate_profiles_equal": _canonical_candidate_profiles(incremental)
        == _canonical_candidate_profiles(source_profile),
        "frozen_order_equal": incremental.frozen_ordered_candidate_ids
        == source_profile.frozen_ordered_candidate_ids,
        "baseline_objective_equal": incremental.baseline.objective
        == source_profile.baseline.objective,
        "runtime_incremental_call_count_equal": runtime.get("planner_query_estimate_count")
        == expected_calls,
        "audit": {
            "all_nonincident_estimates_unchanged": all(
                record["nonincident_estimates_unchanged"] for record in audit_records
            ),
            "records": audit_records,
        },
    }
    complete = (
        all(
            equivalence[field]
            for field in (
                "historical_identity_validated",
                "historical_profile_oracle_digest_equal",
                "semantic_output_equal",
                "candidate_profiles_equal",
                "frozen_order_equal",
                "baseline_objective_equal",
                "runtime_incremental_call_count_equal",
            )
        )
        and equivalence["audit"]["all_nonincident_estimates_unchanged"]
    )
    payload = {
        "format_version": HISTORICAL_INCREMENTAL_FORMAT_VERSION,
        "status": "complete" if complete else "failed",
        "experiment_id": f"{HISTORICAL_INCREMENTAL_FORMAT_VERSION}:{dataset_id}",
        "dataset_id": dataset_id,
        "protocol": {
            "strategy": "incidence-incremental-v1",
            "historical_profile_role": "immutable-semantic-oracle",
            "reference_profile_rerun": False,
            "same_snapshot_universe_native_repository": True,
            "same_patched_postgresql_binary": True,
            "nonincident_estimate_invariant": "fail-closed-audited",
            "validation_wall_clock_gate_seconds": MAX_VALIDATION_SECONDS,
        },
        "research_repository": research_identity,
        "advisor": {
            "current_incremental_commit_sha": CURRENT_INCREMENTAL_ADVISOR_SHA,
            "historical_oracle_commit_sha": REFERENCE_ADVISOR_SHA,
        },
        "patched_postgresql": {
            "repository": "1951123/postgresql-pgextadv",
            "commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "postgres_version": "16.14",
        },
        "source_run": {
            "path": str(source_run),
            "manifest_sha256": sha256_file(source["paths"]["manifest"]),
            "historical_singleton_profile_semantic_digest": source_profile.computed_semantic_digest,
            "snapshot_semantic_digest": snapshot.semantic_digest,
            "candidate_universe_semantic_digest": universe.semantic_digest,
            "native_repository_semantic_digest": repository.semantic_digest,
            "ground_truth_semantic_digest": truth_semantic_digest,
        },
        "sandbox_verification": {
            "prepared": prepared.metadata.to_dict(),
            "verified": verification,
        },
        "historical_oracle": {
            "profile_semantic_digest": source_profile.computed_semantic_digest,
            "runtime_metadata": dict(source_profile.runtime_metadata),
        },
        "incremental": {
            "profile_semantic_digest": incremental.computed_semantic_digest,
            "runtime_metadata": runtime,
        },
        "equivalence": equivalence,
    }
    payload["artifact_digest"] = semantic_digest(payload)
    write_json(output, payload)
    return inspect_historical_incremental_artifact(output)


def inspect_historical_incremental_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return {
        "format_version": value.get("format_version"),
        "status": value.get("status"),
        "dataset_id": value.get("dataset_id"),
        "artifact_digest": value.get("artifact_digest"),
        "semantic_output_equal": value.get("equivalence", {}).get("semantic_output_equal"),
        "incremental_planner_query_estimate_count": value.get("incremental", {})
        .get("runtime_metadata", {})
        .get("planner_query_estimate_count"),
    }


def validate_historical_incremental_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != HISTORICAL_INCREMENTAL_FORMAT_VERSION:
        raise ValueError("unsupported historical incremental equivalence artifact")
    body = dict(value)
    digest = body.pop("artifact_digest", None)
    if digest != semantic_digest(body):
        raise ValueError("historical incremental equivalence artifact digest mismatch")
    if value.get("status") != "complete":
        raise ValueError("historical incremental equivalence artifact is not complete")
    equivalence = value.get("equivalence", {})
    required = (
        "historical_identity_validated",
        "historical_profile_oracle_digest_equal",
        "semantic_output_equal",
        "candidate_profiles_equal",
        "frozen_order_equal",
        "baseline_objective_equal",
        "runtime_incremental_call_count_equal",
    )
    if not all(equivalence.get(field) is True for field in required):
        raise ValueError("historical incremental semantic gate failed")
    if equivalence.get("audit", {}).get("all_nonincident_estimates_unchanged") is not True:
        raise ValueError("historical incremental nonincident audit failed")
    return inspect_historical_incremental_artifact(path) | {"status": "valid"}


def validate_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    if value.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported singleton equivalence artifact")
    digest = value.get("artifact_digest")
    body = dict(value)
    body.pop("artifact_digest", None)
    if digest != semantic_digest(body):
        raise ValueError("singleton equivalence artifact digest mismatch")
    if value.get("status") != "complete":
        raise ValueError("singleton equivalence artifact is not complete")
    equivalence = value.get("equivalence", {})
    if not all(
        equivalence.get(field) is True
        for field in (
            "semantic_output_equal",
            "candidate_profiles_equal",
            "frozen_order_equal",
            "baseline_objective_equal",
        )
    ):
        raise ValueError("singleton equivalence semantic gate failed")
    if not equivalence.get("audit", {}).get("all_nonincident_estimates_unchanged"):
        raise ValueError("singleton equivalence nonincident audit failed")
    return inspect_artifact(path) | {"status": "valid"}


def write_preflight(
    path: Path, *, advisor_root: Path = Path("/home/wqts/projects/extstats-advisor")
) -> dict[str, Any]:
    value = {
        "format_version": f"{FORMAT_VERSION}-preflight",
        "datasets": [
            preflight_dataset(
                dataset_id,
                source_run,
                advisor_root=advisor_root,
                historical_reference_seconds=HISTORICAL_REFERENCE_SECONDS.get(dataset_id),
            )
            for dataset_id, source_run in DEFAULT_SOURCE_RUNS.items()
        ],
    }
    value["artifact_digest"] = semantic_digest(value)
    write_json(path, value)
    return value
