"""Formal RQ2 sample-to-full-data transfer campaign.

The canonical advisor runner remains responsible for producing the sample-side
AdvisorSnapshot, GroundTruthSet, candidate universe, native repository,
singleton profile, search, and Recommendation.  This module only orchestrates
that frozen pipeline and the stock PostgreSQL P0/P1/P2 measurement.  It does
not implement PostgreSQL estimation, native statistics construction, or the
cardinality loss.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .analysis.audit import build_per_query_records
from .arecel_truth import authoritative_truth_spec
from .canonical_runner import _run_canonical
from .datasets import census13, dmv11, forest10, power7
from .full_data_transfer import (
    build_transfer_records,
    summarize_transfer,
)
from .pins import verify_research_repository
from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
from .provenance import read_json, reject_credentials, semantic_digest, sha256_file, write_json
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_STOCK_POSTGRES_SHA,
    formal_system_freeze_v2_identity,
    verify_frozen_systems_v2,
)
from .transfer_engine import run_transfer_phases

FORMAL_FORMAT = "rq2-transfer-v1"
FORMAL_EXPERIMENT = "rq2-formal-sample-to-full-transfer-v1"
RQ2_DATASETS = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
K_S = 8
B = 8
T_SECONDS = 300
SEED_IDENTIFIER = 123
SETSEED_SQL = "SELECT setseed(1.0 / 123)"


@dataclass(frozen=True)
class DatasetSpec:
    dataset_id: str
    dataset: Any
    loader: Callable[..., dict[str, Any]]
    relation: str
    full_format_version: str
    compact_format_version: str
    expected_candidate_count: int | None = None


DATASET_SPECS = {
    item.dataset_id: item
    for item in (
        DatasetSpec(
            census13.BENCHMARK_ID,
            census13,
            load_census13,
            "census13",
            "arecel-census13-rq2-full-data-target100-v2",
            "arecel-census13-rq2-canonical-v2",
        ),
        DatasetSpec(
            forest10.BENCHMARK_ID,
            forest10,
            load_forest10,
            "forest10",
            "arecel-forest10-rq2-full-data-target100-v2",
            "arecel-forest10-rq2-canonical-v2",
        ),
        DatasetSpec(
            power7.BENCHMARK_ID,
            power7,
            load_power7,
            "power7",
            "arecel-power7-rq2-full-data-target100-v2",
            "arecel-power7-rq2-canonical-v2",
        ),
        DatasetSpec(
            dmv11.BENCHMARK_ID,
            dmv11,
            load_dmv11,
            "dmv11",
            "arecel-dmv11-rq2-full-data-target100-v2",
            "arecel-dmv11-rq2-canonical-v2",
            110,
        ),
    )
}


def _run_json(command: list[str]) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {command[0]}\n{completed.stderr[-4000:]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(f"command produced no JSON result: {command[0]}")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"command did not produce JSON: {command[0]}") from exc
    return value, round(time.monotonic() - started, 6)


def _source_paths(source_run: Path) -> dict[str, Path]:
    return {
        "snapshot": source_run / "advisor-snapshot",
        "candidate_universe": source_run / "candidate-universe.json",
        "native_repository": source_run / "native-stats-repository",
        "ground_truth": source_run / "ground-truth-v1.json",
        "singleton_profile": source_run / "singleton-profile.json",
        "optimization_plan": source_run / "optimization-plan.json",
        "search_result": source_run / "search-result.json",
        "recommendation": source_run / "recommendation.json",
        "workload": source_run / "workload.json",
    }


def _artifact_digest(path: Path) -> str:
    value = read_json(path / "manifest.json") if path.is_dir() else read_json(path)
    digest = value.get("semantic_digest")
    if not isinstance(digest, str):
        raise TypeError(f"artifact has no semantic_digest: {path}")
    return digest


def _historical_preflight(dataset_id: str, research_root: Path) -> dict[str, Any]:
    """Build a conservative stage preflight from immutable pilot timings.

    This is only a launch gate.  Formal timings are recorded separately after
    execution, and a missing historical timing is explicitly reported as
    unknown rather than treated as zero cost.
    """

    stages = (
        "snapshot/sample preparation",
        "singleton profiling",
        "greedy search",
        "sample-side paired evaluation",
        "stock data load",
        "P0 EXPLAIN",
        "deployment + ANALYZE",
        "P1 EXPLAIN",
        "P2 EXPLAIN",
        "artifact analysis/write",
    )
    pilot = (
        research_root / f"experiments/{dataset_id}/full-data-transfer-k8/full-data-transfer-v1.json"
    )
    historical = read_json(pilot) if pilot.is_file() else {}
    timings = historical.get("timings_seconds", {})
    manifest_candidates = sorted(
        (research_root / "runs").glob("*/manifest.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    manifest = next(
        (
            read_json(path)
            for path in manifest_candidates
            if read_json(path).get("benchmark_id") == dataset_id
        ),
        {},
    )
    canonical = manifest.get("stage_timings", {})
    observed = {
        "snapshot/sample preparation": canonical.get(
            "snapshot_capture", canonical.get("stock_load_initial_analyze")
        ),
        "singleton profiling": canonical.get("singleton_profiling"),
        "greedy search": canonical.get("optimization_search"),
        "sample-side paired evaluation": None,
        "stock data load": canonical.get("stock_load_initial_analyze"),
        "P0 EXPLAIN": timings.get("p0_explain"),
        "deployment + ANALYZE": timings.get(
            "deployment_including_final_analyze", timings.get("deployment")
        ),
        "P1 EXPLAIN": timings.get("p1_explain"),
        "P2 EXPLAIN": timings.get("p2_explain"),
        "artifact analysis/write": timings.get("transfer_analysis_artifact_write"),
    }
    checks = [
        {
            "stage": stage,
            "historical_seconds": observed[stage],
            "gate": "pass"
            if observed[stage] is None or observed[stage] <= T_SECONDS
            else "blocked",
            "unknown_is_not_zero": observed[stage] is None,
        }
        for stage in stages
    ]
    blocked = [item for item in checks if item["gate"] == "blocked"]
    return {
        "protocol": "single-stage-hard-cap-v1",
        "hard_cap_seconds": T_SECONDS,
        "basis": "historical committed pilot timing when available; unknown stages remain explicit",
        "dataset_id": dataset_id,
        "checks": checks,
        "status": "blocked" if blocked else "pass",
        "blocked_stages": [item["stage"] for item in blocked],
    }


def preflight_rq2(dataset_id: str, research_root: Path | None = None) -> dict[str, Any]:
    """Return the non-mutating launch preflight for one formal child."""

    if dataset_id not in DATASET_SPECS:
        raise ValueError(f"unsupported RQ2 dataset: {dataset_id}")
    root = research_root or Path(__file__).resolve().parents[2]
    return _historical_preflight(dataset_id, root)


def _source_truth(source_run: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    truth_artifact = read_json(source_run / "ground-truth-v1.json")
    source = truth_artifact.get("source", {})
    if source.get("kind") != "authoritative-external-exact":
        raise ValueError("formal RQ2 requires authoritative-external-exact truth")
    if (
        truth_artifact.get("collection_contract") != "authoritative-exact-cardinality-v1"
        and truth_artifact.get("collection_contract")
        != "authoritative-external-exact-cardinality-v1"
    ):
        raise ValueError("formal RQ2 GroundTruthSet has the wrong collection contract")
    if truth_artifact.get("source_snapshot_semantic_digest") != _artifact_digest(
        source_run / "advisor-snapshot"
    ):
        raise ValueError("GroundTruthSet is not bound to this fresh AdvisorSnapshot")
    if len(truth_artifact.get("truths", [])) != 10_000:
        raise ValueError("formal RQ2 requires exactly 10,000 bound truths")
    return truth_artifact["truths"], truth_artifact


def _evaluate_sample(
    *,
    source_paths: dict[str, Path],
    planner_dsn: str,
    advisor_command: str,
    selected_ids: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    common = [
        advisor_command,
        "utility",
        "evaluate",
        "postgres",
        str(source_paths["snapshot"]),
        str(source_paths["candidate_universe"]),
        str(source_paths["native_repository"]),
        str(source_paths["ground_truth"]),
        "--dsn",
        planner_dsn,
    ]
    prepare_command = [
        advisor_command,
        "sandbox",
        "prepare",
        "postgres",
        str(source_paths["snapshot"]),
        str(source_paths["candidate_universe"]),
        str(source_paths["native_repository"]),
        "--dsn",
        planner_dsn,
    ]
    destroy_command = [advisor_command, "sandbox", "destroy", "postgres", "--dsn", planner_dsn]
    prepared = False
    started = time.monotonic()
    try:
        _run_json(prepare_command)
        prepared = True
        baseline, baseline_seconds = _run_json(common)
        final, final_seconds = _run_json(
            common + [value for candidate in selected_ids for value in ("--candidate", candidate)]
        )
    finally:
        if prepared:
            _run_json(destroy_command)
    records = build_per_query_records(baseline, final)
    if len(records) != 10_000:
        raise ValueError("sample paired evaluation did not cover exactly 10,000 queries")
    return records, {
        "baseline": {
            "weighted_objective": float(baseline["objective"]),
            "loss_contract": baseline["loss_contract"],
            "query_count": baseline["query_count"],
            "planner_calls": baseline["query_count"],
            "elapsed_seconds": baseline_seconds,
        },
        "final": {
            "weighted_objective": float(final["objective"]),
            "loss_contract": final["loss_contract"],
            "query_count": final["query_count"],
            "planner_calls": final["query_count"],
            "elapsed_seconds": final_seconds,
        },
        "runtime_seconds": round(time.monotonic() - started, 6),
        "configuration_evaluations": 2,
        "planner_query_calls": baseline["query_count"] + final["query_count"],
        "selection_repeated": False,
    }


def _rank(values: list[float]) -> list[float]:
    ordered = sorted(enumerate(values), key=lambda item: item[1])
    result = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average = (index + 1 + end) / 2.0
        for position in range(index, end):
            result[ordered[position][0]] = average
        index = end
    return result


def _spearman(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    x, y = _rank(left), _rank(right)
    x_mean = sum(x) / len(x)
    y_mean = sum(y) / len(y)
    numerator = sum((a - x_mean) * (b - y_mean) for a, b in zip(x, y))
    denominator_left = math.sqrt(sum((a - x_mean) ** 2 for a in x))
    denominator_right = math.sqrt(sum((b - y_mean) ** 2 for b in y))
    if denominator_left == 0 or denominator_right == 0:
        return None
    return numerator / (denominator_left * denominator_right)


def _relative(before: float, after: float) -> float:
    if before == 0:
        raise ValueError("relative improvement denominator is zero")
    return (before - after) / before


def _transfer_metrics(records: list[dict[str, Any]], summary: dict[str, Any]) -> dict[str, Any]:
    sample_baseline = summary["sandbox_baseline"]["weighted_objective"]
    sample_final = summary["sandbox_final"]["weighted_objective"]
    p0 = summary["p0"]["weighted_objective"]
    p1 = summary["p1"]["weighted_objective"]
    p2 = summary["p2"]["weighted_objective"]
    sample_relative = _relative(sample_baseline, sample_final)
    full_relative = _relative(p2, p1)
    sample_log: list[float] = []
    full_log: list[float] = []
    for record in records:
        sample_log.append(
            math.log(record["sandbox_baseline_qerror"] / record["sandbox_final_qerror"])
        )
        full_log.append(math.log(record["p2_qerror"] / record["p1_qerror"]))
    correspondence = summary["sandbox_production_correspondence"]
    sample_counts = {
        value: sum(record["sandbox_classification"] == value for record in records)
        for value in ("improved", "unchanged", "worsened")
    }
    matrix = {
        source: {
            target: correspondence[f"sandbox_{source}_production_{target}"]
            for target in ("improved", "unchanged", "worsened")
        }
        for source in ("improved", "unchanged", "worsened")
    }
    return {
        "sample": {
            "J_S_empty": sample_baseline,
            "J_S_M": sample_final,
            "delta_J_S": sample_baseline - sample_final,
            "relative_improvement_R_S": sample_relative,
            "direction_counts": sample_counts,
        },
        "full_data": {
            "J_D_P0": p0,
            "J_D_P1": p1,
            "J_D_P2": p2,
            "delta_operational_P0_to_P1": p0 - p1,
            "relative_operational_improvement": _relative(p0, p1),
            "delta_controlled_P2_to_P1": p2 - p1,
            "relative_controlled_improvement_R_D": full_relative,
            "P0_to_P1_direction_counts": summary["p0_to_p1_classification"],
            "P2_to_P1_direction_counts": summary["p2_to_p1_classification"],
        },
        "transfer": {
            "retention_ratio": full_relative / sample_relative if sample_relative > 0 else None,
            "retention_ratio_reason": None if sample_relative > 0 else "undefined-denominator",
            "same_direction_count": correspondence["same_direction"],
            "opposite_direction_count": correspondence["opposite_direction"],
            "unchanged_involved_count": correspondence["unchanged_involved"],
            "same_direction_fraction": correspondence["same_direction"] / len(records),
            "opposite_direction_fraction": correspondence["opposite_direction"] / len(records),
            "contingency_3x3": matrix,
            "spearman_gs_gd": _spearman(sample_log, full_log),
            "paired_query_count": len(records),
            "p_value": None,
        },
    }


def _write_per_query(path: Path, records: list[dict[str, Any]]) -> str:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    return sha256_file(path)


def _cleanup_managed_statistics(dsn: str, relation: str, names: list[str]) -> dict[str, Any]:
    import psycopg
    from psycopg import sql

    with psycopg.connect(dsn, autocommit=True) as connection:
        for name in names:
            connection.execute(
                sql.SQL("DROP STATISTICS {}.{}").format(
                    sql.Identifier("public"), sql.Identifier(name)
                )
            )
        remaining = connection.execute(
            """
            SELECT s.stxname
              FROM pg_catalog.pg_statistic_ext AS s
              JOIN pg_catalog.pg_namespace AS n ON n.oid=s.stxnamespace
              JOIN pg_catalog.pg_class AS c ON c.oid=s.stxrelid
             WHERE n.nspname='public' AND c.relname=%s
             ORDER BY s.stxname
            """,
            (relation,),
        ).fetchall()
    return {
        "dropped_names": names,
        "remaining_extended_statistics": [str(row[0]) for row in remaining],
    }


def _canonical_source_run(
    spec: DatasetSpec,
    *,
    stock_dsn: str,
    patched_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    runtime_root: Path,
    data_root: Path | None,
    advisor_command: str,
) -> dict[str, Any]:
    runtime_root.mkdir(parents=True, exist_ok=True)
    output_root = runtime_root / spec.dataset_id.removeprefix("arecel-") / "canonical"
    compact_root = runtime_root / spec.dataset_id.removeprefix("arecel-") / "compact"
    truth_spec = authoritative_truth_spec(spec.dataset_id, Path(__file__).resolve().parents[2])
    return _run_canonical(
        dataset=spec.dataset,
        loader=spec.loader,
        full_format_version=spec.full_format_version,
        compact_format_version=spec.compact_format_version,
        production_dsn=stock_dsn,
        planner_dsn=patched_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
        stock_postgres_root=stock_postgres_root,
        output_root=output_root,
        sample_rows=SAMPLE_ROWS,
        sample_seed=SAMPLE_SEED,
        statistics_target=STATISTICS_TARGET,
        candidate_limit=K_S,
        search_wall_clock_seconds=T_SECONDS,
        data_root=data_root,
        reset_disposable=True,
        advisor_command=advisor_command,
        expected_candidate_count=spec.expected_candidate_count,
        authoritative_truth=truth_spec,
        seed_identifier=SEED_IDENTIFIER,
        compact_evidence_directory=compact_root,
        system_freeze_v2=True,
    )


def run_rq2_child(
    dataset_id: str,
    *,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    stock_postgres_root: Path = Path("/home/wqts/projects/postgresql-src"),
    data_root: Path | None = None,
    advisor_command: str = "extstats-advisor",
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Run one fresh v2 Recommendation and one formal RQ2 child."""

    if dataset_id not in DATASET_SPECS:
        raise ValueError(f"unsupported RQ2 dataset: {dataset_id}")
    if not stock_dsn or not patched_dsn:
        raise ValueError("stock and patched DSNs are required and never serialized")
    research_root = Path(__file__).resolve().parents[2]
    preflight = preflight_rq2(dataset_id, research_root)
    print(json.dumps({"event": "rq2-preflight", **preflight}, sort_keys=True))
    if preflight["status"] == "blocked":
        raise RuntimeError(f"RQ2 preflight blocked stages: {preflight['blocked_stages']}")
    verify_research_repository(research_root)
    systems = verify_frozen_systems_v2(advisor_root, patched_postgres_root, stock_postgres_root)
    spec = DATASET_SPECS[dataset_id]
    runtime = runtime_root or research_root / ".runtime" / "rq2-formal"
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"formal RQ2 output already exists: {output}")
    output.mkdir(parents=True, exist_ok=False)
    per_query_path = output / "per-query-transfer-v1.jsonl"
    started_total = time.monotonic()

    canonical_result = _canonical_source_run(
        spec,
        stock_dsn=stock_dsn,
        patched_dsn=patched_dsn,
        advisor_root=advisor_root,
        patched_postgres_root=patched_postgres_root,
        stock_postgres_root=stock_postgres_root,
        runtime_root=runtime,
        data_root=data_root,
        advisor_command=advisor_command,
    )
    source_run = Path(canonical_result["run_directory"])
    paths = _source_paths(source_run)
    manifest = read_json(source_run / "manifest.json")
    if (
        manifest.get("research_commit_sha")
        != verify_research_repository(research_root)["research_commit_sha"]
    ):
        raise ValueError("formal source run research SHA is not the current clean producer SHA")
    if manifest.get("advisor_commit_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("formal source run does not bind the v2 Advisor SHA")
    truths, truth_artifact = _source_truth(source_run)
    workload = read_json(paths["workload"])["queries"]
    search = read_json(paths["search_result"])
    recommendation = read_json(paths["recommendation"])
    selected_ids = list(recommendation["deployment_ordered_candidate_ids"])
    if len(selected_ids) != len(recommendation["selected_candidate_ids"]):
        raise ValueError("Recommendation membership/order fields disagree")
    sample_records, sample_runtime = _evaluate_sample(
        source_paths=paths,
        planner_dsn=patched_dsn,
        advisor_command=advisor_command,
        selected_ids=selected_ids,
    )
    sample_by_id = {row["query_id"]: row for row in sample_records}
    if set(sample_by_id) != {row["query_id"] for row in truths}:
        raise ValueError("sample utility and authoritative truth query IDs differ")

    load_started = time.monotonic()
    load = spec.loader(
        stock_dsn,
        data_root=data_root,
        reset_disposable=True,
        statistics_target=STATISTICS_TARGET,
        seed_identifier=SEED_IDENTIFIER,
    )
    load_seconds = round(time.monotonic() - load_started, 6)
    source_for_transfer = dict(paths)
    phases = run_transfer_phases(
        production_dsn=stock_dsn,
        source_paths=source_for_transfer,
        workload=workload,
        relation=spec.relation,
        load=load,
        expected_order=tuple(selected_ids),
        output_directory=output,
        advisor_command=advisor_command,
    )
    estimates = {
        query_id: {
            "p0": phases["p0"][query_id],
            "p1": phases["p1"][query_id],
            "p2": phases["p2"][query_id],
        }
        for query_id in phases["p0"]
    }
    sample_transfer_records = {
        query_id: {
            "baseline_estimate": row["baseline_estimated_rows"],
            "final_estimate": row["final_estimated_rows"],
            "baseline_qerror": row["baseline_qerror"],
            "final_qerror": row["final_qerror"],
        }
        for query_id, row in sample_by_id.items()
    }
    records = build_transfer_records(truths, sample_transfer_records, estimates)
    for record in records:
        sample = sample_by_id[record["query_id"]]
        record.update(
            {
                "sample_baseline_estimate": sample["baseline_estimated_rows"],
                "sample_final_estimate": sample["final_estimated_rows"],
                "sample_baseline_qerror": sample["baseline_qerror"],
                "sample_final_qerror": sample["final_qerror"],
                "sample_classification": sample["classification"],
            }
        )
    summary = summarize_transfer(records)
    transfer = _transfer_metrics(records, summary)
    cleanup = _cleanup_managed_statistics(
        stock_dsn,
        spec.relation,
        [item["name"] for item in phases["managed_stats"]],
    )
    if cleanup["remaining_extended_statistics"]:
        raise ValueError("formal RQ2 cleanup left extended statistics behind")
    source_artifacts = {
        name: _artifact_digest(path) for name, path in paths.items() if name != "workload"
    }
    source_artifacts["workload"] = sha256_file(paths["workload"])
    artifact = {
        "format_version": FORMAL_FORMAT,
        "formal_experiment": FORMAL_EXPERIMENT,
        "execution_status": "complete",
        "dataset_id": dataset_id,
        "system_freeze": formal_system_freeze_v2_identity(),
        "producer_research_sha": manifest["research_commit_sha"],
        "advisor_sha": systems["advisor_commit_sha"],
        "patched_postgres_sha": systems["patched_postgres_commit_sha"],
        "stock_postgres_sha": systems["stock_postgres_commit_sha"],
        "postgres_version": load["server_version"],
        "canonical_parameters": {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "K_s": K_S,
            "B": B,
            "T_seconds": T_SECONDS,
            "experiment_seed_identifier": SEED_IDENTIFIER,
            "postgresql_setseed_sql": SETSEED_SQL,
        },
        "preflight": preflight,
        "source_run": {
            "run_id": source_run.name,
            "directory": str(source_run.relative_to(research_root)),
            "artifact_digests": source_artifacts,
            "snapshot_digest": source_artifacts["snapshot"],
            "ground_truth_digest": source_artifacts["ground_truth"],
            "workload_id": manifest["workload_id"],
            "workload_sha256": manifest["workload_sha256"],
            "truth_capture": manifest.get("truth_capture"),
        },
        "snapshot_digest": source_artifacts["snapshot"],
        "ground_truth_digest": truth_artifact["semantic_digest"],
        "truth_provenance": {
            "kind": truth_artifact["source"]["kind"],
            "authority": truth_artifact["source"]["authority"],
            "dataset_identity": truth_artifact["source"]["dataset_identity"],
            "source_revision": truth_artifact["source"]["source_revision"],
            "observations_sha256": truth_artifact["source"]["source_artifact_sha256"],
            "collection_contract": truth_artifact["collection_contract"],
            "snapshot_binding": truth_artifact["source_snapshot_semantic_digest"],
            "workload_id": truth_artifact["workload_id"],
            "query_count": len(truth_artifact["truths"]),
            "external_labels_not_recounted": True,
            "production_exact_sanity_is_not_truth_source": True,
        },
        "candidate_universe_digest": source_artifacts["candidate_universe"],
        "native_repository_digest": source_artifacts["native_repository"],
        "singleton_profile_digest": source_artifacts["singleton_profile"],
        "optimization_plan_digest": source_artifacts["optimization_plan"],
        "search_result_digest": source_artifacts["search_result"],
        "recommendation_digest": source_artifacts["recommendation"],
        "actual_selected_k": len(selected_ids),
        "termination_reason": search["termination_reason"],
        "screened_candidate_ids": [
            item["candidate_id"]
            for item in read_json(paths["optimization_plan"])["screened_candidates"]
        ],
        "accepted_move_order": [item["added_candidate_id"] for item in search["accepted_moves"]],
        "selected_candidate_ids": selected_ids,
        "sample_evaluation": {
            "baseline": transfer["sample"]["J_S_empty"],
            "final": transfer["sample"]["J_S_M"],
            "runtime": sample_runtime,
            "per_query_path": per_query_path.name,
        },
        "full_data": {
            "P0": summary["p0"],
            "P1": summary["p1"],
            "P2": summary["p2"],
            "deployment": {
                "path": "deployment-result-v1.json",
                "semantic_digest": phases["deployment"]["semantic_digest"],
                "commit_status": phases["deployment"]["commit_status"],
                "post_commit_verified": phases["deployment"]["post_commit_verified"],
                "object_count": len(phases["managed_stats"]),
            },
            "ordinary_stats_fingerprint_p0": phases["ordinary_stats_fingerprint_p0"],
            "ordinary_stats_fingerprint_p1": phases["ordinary_stats_fingerprint_p1"],
            "ordinary_stats_fingerprint_p2": phases["ordinary_stats_fingerprint_p2"],
            "ordinary_stats_p1_p2_equal": phases["ordinary_stats_p1_p2_equal"],
            "physical_statistics": phases["managed_stats"],
            "payload_verification": {
                "all_payloads_nonempty": all(item["payload"] for item in phases["managed_stats"]),
                "all_target_100": all(item["target"] == 100 for item in phases["managed_stats"]),
                "statistics_kind": sorted({item["kind"] for item in phases["managed_stats"]}),
            },
            "runtime": {"stock_load_seconds": load_seconds, **phases["timings"]},
        },
        "rq2a_summary": transfer["full_data"],
        "rq2b_summary": transfer,
        "summary": summary,
        "raw_per_query_path": per_query_path.name,
        "cleanup": cleanup,
        "native_analyze_realizations": 1,
        "historical_pilot_paths": [f"experiments/{dataset_id}/full-data-transfer-k8"],
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "truth_source_kind": "authoritative-external-exact",
        "credentials_recorded": False,
        "stage_timings_seconds": {
            "canonical_source_run": manifest.get("stage_timings", {}),
            "sample_side_paired_evaluation": sample_runtime["runtime_seconds"],
            "stock_load": load_seconds,
            "full_data_transfer": phases["timings"],
        },
        "live_experiment_elapsed_seconds": round(time.monotonic() - started_total, 6),
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output / "rq2-transfer-v1.json", artifact)
    _write_per_query(per_query_path, records)
    # The raw JSONL digest is part of the artifact contract, so finalize the
    # semantic digest after the file has been written.
    artifact["per_query_sha256"] = sha256_file(per_query_path)
    artifact.pop("semantic_digest", None)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output / "rq2-transfer-v1.json", artifact)
    return {
        "status": "complete",
        "dataset_id": dataset_id,
        "output_directory": str(output),
        "artifact": str(output / "rq2-transfer-v1.json"),
        "semantic_digest": artifact["semantic_digest"],
        "actual_selected_k": len(selected_ids),
        "termination_reason": search["termination_reason"],
        "recommendation_digest": source_artifacts["recommendation"],
        "preflight": preflight,
    }


def validate_rq2_artifact(path: Path) -> dict[str, Any]:
    """Validate a formal child without accessing PostgreSQL."""

    value = read_json(path)
    if value.get("format_version") != FORMAL_FORMAT:
        raise ValueError("unsupported RQ2 formal artifact format")
    if value.get("formal_experiment") != FORMAL_EXPERIMENT:
        raise ValueError("artifact is not the formal RQ2 experiment")
    if value.get("execution_status") not in {"complete", "blocked", "failed-validation"}:
        raise ValueError("invalid RQ2 execution status")
    if value.get("system_freeze") != formal_system_freeze_v2_identity():
        raise ValueError("RQ2 artifact is not bound to system-freeze-v2")
    for key in (
        "producer_research_sha",
        "advisor_sha",
        "patched_postgres_sha",
        "stock_postgres_sha",
    ):
        if not isinstance(value.get(key), str) or len(value[key]) != 40:
            raise ValueError(f"RQ2 artifact has invalid {key}")
    if value.get("advisor_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("RQ2 artifact Advisor SHA is not frozen v2")
    if value.get("patched_postgres_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("RQ2 artifact patched PostgreSQL SHA is not frozen")
    if value.get("stock_postgres_sha") != FROZEN_STOCK_POSTGRES_SHA:
        raise ValueError("RQ2 artifact stock PostgreSQL SHA is not frozen")
    if value.get("truth_source_kind") != "authoritative-external-exact":
        raise ValueError("RQ2 artifact truth source is not authoritative external exact")
    if value.get("truth_provenance", {}).get("kind") != "authoritative-external-exact":
        raise ValueError("RQ2 truth provenance is not authoritative external exact")
    if value.get("canonical_parameters") != {
        "sample_rows": SAMPLE_ROWS,
        "sample_seed": SAMPLE_SEED,
        "statistics_target": STATISTICS_TARGET,
        "K_s": K_S,
        "B": B,
        "T_seconds": T_SECONDS,
        "experiment_seed_identifier": SEED_IDENTIFIER,
        "postgresql_setseed_sql": SETSEED_SQL,
    }:
        raise ValueError("RQ2 canonical parameters drifted")
    if value.get("execution_status") == "complete":
        required = (
            "snapshot_digest",
            "ground_truth_digest",
            "candidate_universe_digest",
            "native_repository_digest",
            "singleton_profile_digest",
            "optimization_plan_digest",
            "search_result_digest",
            "recommendation_digest",
            "sample_evaluation",
            "full_data",
            "rq2a_summary",
            "rq2b_summary",
            "cleanup",
            "per_query_sha256",
        )
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"complete RQ2 artifact is missing fields: {missing}")
        if value["actual_selected_k"] != len(value["selected_candidate_ids"]):
            raise ValueError("RQ2 selected-k field does not match membership")
        if value["full_data"]["ordinary_stats_p1_p2_equal"] is not True:
            raise ValueError("RQ2 P1/P2 ordinary-statistics equality is not verified")
        if value["cleanup"].get("remaining_extended_statistics"):
            raise ValueError("RQ2 cleanup left extended statistics")
    body = dict(value)
    digest = body.pop("semantic_digest", None)
    if digest != semantic_digest(body):
        raise ValueError("RQ2 semantic digest does not match artifact body")
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAL_FORMAT,
        "dataset_id": value.get("dataset_id"),
        "execution_status": value.get("execution_status"),
        "semantic_digest": digest,
        "actual_selected_k": value.get("actual_selected_k"),
    }


def inspect_rq2_artifact(path: Path) -> dict[str, Any]:
    value = read_json(path)
    return {
        "format_version": value.get("format_version"),
        "dataset_id": value.get("dataset_id"),
        "execution_status": value.get("execution_status"),
        "semantic_digest": value.get("semantic_digest"),
        "actual_selected_k": value.get("actual_selected_k"),
        "termination_reason": value.get("termination_reason"),
        "rq2a_summary": value.get("rq2a_summary"),
        "rq2b_summary": value.get("rq2b_summary"),
    }


def validate_rq2_cross_dataset(paths: list[Path]) -> dict[str, Any]:
    """Validate and derive the cross-dataset RQ2 table from formal children."""

    values = []
    for path in paths:
        validate_rq2_artifact(path)
        values.append(read_json(path))
    if len(values) < 2:
        raise ValueError("cross-dataset RQ2 summary requires at least two complete children")
    datasets = {value["dataset_id"] for value in values}
    if len(datasets) != len(values):
        raise ValueError("cross-dataset RQ2 summary contains duplicate datasets")
    rows = []
    for value in sorted(values, key=lambda item: item["dataset_id"]):
        rq2a = value["rq2a_summary"]
        rq2b = value["rq2b_summary"]
        rows.append(
            {
                "dataset_id": value["dataset_id"],
                "actual_selected_k": value["actual_selected_k"],
                "J_D_P0": rq2a["J_D_P0"],
                "J_D_P1": rq2a["J_D_P1"],
                "J_D_P2": rq2a["J_D_P2"],
                "operational_relative_improvement": rq2a["relative_operational_improvement"],
                "controlled_relative_improvement": rq2a["relative_controlled_improvement_R_D"],
                "sample_relative_improvement": rq2b["sample"]["relative_improvement_R_S"],
                "retention_ratio": rq2b["transfer"]["retention_ratio"],
                "same_direction_fraction": rq2b["transfer"]["same_direction_fraction"],
                "opposite_direction_fraction": rq2b["transfer"]["opposite_direction_fraction"],
                "spearman_gs_gd": rq2b["transfer"]["spearman_gs_gd"],
            }
        )
    return {"format_version": "rq2-cross-dataset-transfer-summary-v1", "rows": rows}


__all__ = [
    "FORMAL_EXPERIMENT",
    "FORMAL_FORMAT",
    "K_S",
    "RQ2_DATASETS",
    "SAMPLE_ROWS",
    "SAMPLE_SEED",
    "SETSEED_SQL",
    "STATISTICS_TARGET",
    "T_SECONDS",
    "B",
    "inspect_rq2_artifact",
    "preflight_rq2",
    "run_rq2_child",
    "validate_rq2_artifact",
    "validate_rq2_cross_dataset",
]
