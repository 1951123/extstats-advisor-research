"""Census13 RQ1 matched-comparison canary and its derived artifact contract.

The canary deliberately separates the advisor's patched design-time sandbox
objective from the stock PostgreSQL full-data deployment that supplies the
RQ1 headline metrics.  This module owns orchestration and evidence checks; it
does not implement a second estimator or statistics search algorithm.
"""

from __future__ import annotations

import copy
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

from .datasets import census13
from .full_data_transfer import _explain_rows, _ordinary_fingerprint, _stats
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_census13
from .postgres_lab import _read_identity, reinit_role, role_spec, stop_role
from .provenance import read_json, reject_credentials, semantic_digest, sha256_file, write_json
from .runner import run_census13
from .system_freeze import (
    DEFAULT_SYSTEM_FREEZE_PATH,
    PATCHED_POSTGRES_SHA,
    POSTGRES_VERSION,
    STOCK_POSTGRES_SHA,
    load_system_freeze,
    validate_system_freeze,
)

RQ1_FORMAT = "rq1-matched-comparison-v1"
PAPER_SPECIFICATION = "paper-experiment-v1"
EXPERIMENT_ID = "rq1-confirmatory-matched-baselines"
CANARY_DATASET_ID = "arecel-census13"
WORKLOAD_ID = "arecel_census13_test_v1"
TRUTH_SEMANTIC_DIGEST = "2cb7c9e89d020581c8a632c20a3a971cee3fd7e44a6a67e91bcea28d57038509"
DEFAULT_TRUTH_ARTIFACT = Path("runs/197e9b890ac58bc4fbcfb218/ground-truth-v1.json")
SEED_IDENTIFIER = 123
SETSEED_SQL = "SELECT setseed(1.0 / 123)"
ARM_IDS = ("pg16-default", "pg16-target10000", "pg16-advisor")
DATASET_PROGRESS = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")


def qerror(estimate: int, truth: int) -> float:
    """Return q-error under qerror-cardinality-floor-1-v1."""

    if isinstance(estimate, bool) or isinstance(truth, bool):
        raise TypeError("estimate and truth must be integers")
    if not isinstance(estimate, int) or not isinstance(truth, int):
        raise TypeError("estimate and truth must be integers")
    if estimate < 0 or truth < 0:
        raise ValueError("estimate and truth must be non-negative")
    estimated = max(estimate, 1)
    actual = max(truth, 1)
    return max(estimated / actual, actual / estimated)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("cannot summarize an empty workload")
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def summarize_per_query(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        raise ValueError("per-query records must not be empty")
    values: list[float] = []
    total_weight = 0.0
    weighted_total = 0.0
    seen: set[str] = set()
    for record in records:
        query_id = record.get("query_id")
        if not isinstance(query_id, str) or not query_id or query_id in seen:
            raise ValueError("per-query query IDs must be unique, non-empty strings")
        seen.add(query_id)
        estimate = record.get("estimate")
        truth = record.get("truth")
        weight = record.get("weight", 1.0)
        if not isinstance(estimate, int) or isinstance(estimate, bool) or estimate < 0:
            raise ValueError(f"invalid estimate for {query_id}")
        if not isinstance(truth, int) or isinstance(truth, bool) or truth < 0:
            raise ValueError(f"invalid truth for {query_id}")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight <= 0:
            raise ValueError(f"invalid positive weight for {query_id}")
        expected = qerror(estimate, truth)
        if float(record.get("qerror", -1.0)) != expected:
            raise ValueError(f"q-error is inconsistent for {query_id}")
        values.append(expected)
        total_weight += float(weight)
        weighted_total += float(weight) * expected
    return {
        "query_count": len(records),
        "arithmetic_mean_qerror": sum(values) / len(values),
        "p50_qerror": _percentile(values, 0.50),
        "p95_qerror": _percentile(values, 0.95),
        "p99_qerror": _percentile(values, 0.99),
        "max_qerror": max(values),
        "weighted_workload_mean_qerror": weighted_total / total_weight,
        "loss_contract": "qerror-cardinality-floor-1-v1",
        "direction": "lower-is-better",
    }


def _pair_summary(
    reference: list[dict[str, Any]],
    advisor: list[dict[str, Any]],
    *,
    include_per_query: bool = True,
) -> dict[str, Any]:
    left = {row["query_id"]: row for row in reference}
    right = {row["query_id"]: row for row in advisor}
    if set(left) != set(right):
        raise ValueError("paired arms do not cover identical query IDs")
    paired: list[dict[str, Any]] = []
    counts = {"improved": 0, "unchanged": 0, "worsened": 0}
    for query_id in sorted(left):
        reference_qerror = float(left[query_id]["qerror"])
        advisor_qerror = float(right[query_id]["qerror"])
        if advisor_qerror < reference_qerror:
            classification = "improved"
        elif advisor_qerror > reference_qerror:
            classification = "worsened"
        else:
            classification = "unchanged"
        counts[classification] += 1
        paired.append(
            {
                "query_id": query_id,
                "reference_qerror": reference_qerror,
                "advisor_qerror": advisor_qerror,
                "classification": classification,
            }
        )
    result: dict[str, Any] = {"classification_counts": counts, "query_count": len(paired)}
    if include_per_query:
        result["per_query"] = paired
    return result


def _artifact_payload(artifact: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in artifact.items() if key != "semantic_digest"}


def _validate_identity_digest(value: Any, label: str) -> None:
    if not isinstance(value, str) or _SHA1.fullmatch(value) is None:
        raise ValueError(f"{label} must be a full commit SHA")


def _build_identity(arm: dict[str, Any], expected_sha: str, label: str) -> None:
    identity = arm.get(label)
    if not isinstance(identity, dict):
        raise TypeError(f"{label} is missing")
    if identity.get("source_commit_sha") != expected_sha:
        raise ValueError(f"{label} does not match the frozen source SHA")
    if identity.get("postgres_version") != POSTGRES_VERSION:
        raise ValueError(f"{label} does not match PostgreSQL {POSTGRES_VERSION}")
    if identity.get("locale") != "C.utf8" or identity.get("encoding") != "UTF8":
        raise ValueError(f"{label} locale/encoding does not match the system freeze")
    if not isinstance(identity.get("build_identity_digest"), str):
        raise TypeError(f"{label} lacks a semantic build identity digest")


def validate_rq1_artifact(artifact: Any) -> dict[str, Any]:
    if not isinstance(artifact, dict):
        raise TypeError("RQ1 artifact must be an object")
    if artifact.get("format_version") != RQ1_FORMAT:
        raise ValueError("unsupported RQ1 artifact format")
    if artifact.get("experiment_id") != "rq1-matched-comparison-v1":
        raise ValueError("RQ1 artifact has the wrong experiment identity")
    if artifact.get("paper_specification") != PAPER_SPECIFICATION:
        raise ValueError("RQ1 artifact is not bound to paper-experiment-v1")
    _validate_identity_digest(artifact.get("research_commit_sha"), "research_commit_sha")
    system = artifact.get("system_freeze")
    validate_system_freeze(system)
    if artifact.get("system_freeze_semantic_digest") != semantic_digest(system):
        raise ValueError("RQ1 artifact system-freeze digest mismatch")
    if artifact.get("arms") != list(ARM_IDS):
        raise ValueError("RQ1 artifact must contain exactly the three frozen arms")
    dataset = artifact.get("dataset")
    workload = artifact.get("workload")
    truth = artifact.get("truth")
    if not isinstance(dataset, dict) or dataset.get("dataset_id") != CANARY_DATASET_ID:
        raise ValueError("RQ1 artifact is not a Census13 canary")
    if not isinstance(workload, dict) or workload.get("workload_id") != WORKLOAD_ID:
        raise ValueError("RQ1 artifact has the wrong workload identity")
    if not isinstance(truth, dict) or truth.get("source_kind") != "production-exact-execution":
        raise ValueError("RQ1 artifact must use production-exact-execution truth")
    if truth.get("workload_id") != workload.get("workload_id"):
        raise ValueError("RQ1 truth and workload identities differ")
    if truth.get("semantic_digest") != TRUTH_SEMANTIC_DIGEST:
        raise ValueError("RQ1 artifact is not bound to the audited Census13 truth")
    per_arm = artifact.get("per_arm")
    if not isinstance(per_arm, dict) or set(per_arm) != set(ARM_IDS):
        raise ValueError("RQ1 artifact per_arm keys do not match arms")
    canonical_query_ids: set[str] | None = None
    summaries: dict[str, dict[str, Any]] = {}
    for arm_id in ARM_IDS:
        arm = per_arm[arm_id]
        if not isinstance(arm, dict):
            raise TypeError(f"RQ1 arm {arm_id} must be an object")
        _build_identity(arm, STOCK_POSTGRES_SHA, "deployment_build_identity")
        if arm_id == "pg16-advisor":
            _build_identity(arm, PATCHED_POSTGRES_SHA, "design_time_build_identity")
            if arm.get("evaluation_mode") != "stock-full-data-deployment":
                raise ValueError("advisor RQ1 headline must be full-data stock deployment")
            if arm.get("headline_source") != "full_data_evaluation_metrics":
                raise ValueError("advisor headline source is not full-data evaluation metrics")
            if not isinstance(arm.get("sandbox_optimization_objective"), dict):
                raise ValueError(
                    "advisor arm must record sandbox_optimization_objective separately"
                )
            if arm.get("full_data_evaluation_metrics") != arm.get("summary"):
                raise ValueError("advisor full-data metrics must be the arm summary")
        else:
            if arm.get("evaluation_mode") != "stock-full-data-physical":
                raise ValueError(f"{arm_id} has an invalid evaluation mode")
            if arm.get("physical_extended_statistics_count") != 0:
                raise ValueError(f"{arm_id} contains an extended-statistics leak")
            if arm.get("physical_extended_statistics") != []:
                raise ValueError(f"{arm_id} contains extended-statistics definitions")
        if arm.get("workload_identity") != workload:
            raise ValueError(f"{arm_id} workload identity differs from the matched workload")
        if arm.get("truth_identity") != truth:
            raise ValueError(f"{arm_id} truth identity differs from the matched truth")
        records = arm.get("per_query")
        if not isinstance(records, list) or not records:
            raise ValueError(f"{arm_id} lacks per-query q-error records")
        query_ids = {row.get("query_id") for row in records}
        if canonical_query_ids is None:
            canonical_query_ids = query_ids
        elif query_ids != canonical_query_ids:
            raise ValueError("RQ1 arms use different query IDs")
        summaries[arm_id] = summarize_per_query(records)
        if arm.get("summary") != summaries[arm_id]:
            raise ValueError(f"{arm_id} summary is not reproducible from per-query records")
        digest = arm.get("per_query_artifact", {}).get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"{arm_id} lacks a per-query artifact digest")
    paired = artifact.get("paired_comparison")
    if not isinstance(paired, dict):
        raise TypeError("RQ1 paired_comparison is missing")
    for reference_id, key in (
        ("pg16-default", "default_vs_advisor"),
        ("pg16-target10000", "target10000_vs_advisor"),
    ):
        expected = _pair_summary(
            per_arm[reference_id]["per_query"],
            per_arm["pg16-advisor"]["per_query"],
            include_per_query=False,
        )
        if paired.get(key) != expected:
            raise ValueError(f"RQ1 paired comparison {key} is not reproducible")
    progress = artifact.get("dataset_progress")
    if not isinstance(progress, dict) or set(progress) != set(DATASET_PROGRESS):
        raise ValueError("RQ1 dataset progress must list all four AreCEL datasets")
    if artifact.get("experiment_status") == "complete" and any(
        progress[item] != "complete" for item in DATASET_PROGRESS
    ):
        raise ValueError("global RQ1 cannot be complete while datasets remain incomplete")
    cleanup = artifact.get("cleanup")
    if not isinstance(cleanup, dict) or cleanup.get("clusters_stopped") is not True:
        raise ValueError("RQ1 artifact does not prove both lab clusters were stopped")
    reject_credentials(artifact)
    expected_digest = semantic_digest(_artifact_payload(artifact))
    if artifact.get("semantic_digest") != expected_digest:
        raise ValueError("RQ1 artifact semantic digest mismatch")
    return {
        "status": "valid",
        "format_version": RQ1_FORMAT,
        "experiment_id": artifact["experiment_id"],
        "dataset_id": CANARY_DATASET_ID,
        "arms": list(ARM_IDS),
        "query_count": len(canonical_query_ids or ()),
        "semantic_digest": expected_digest,
    }


def build_rq1_artifact(
    *,
    research_commit_sha: str,
    system_freeze: dict[str, Any],
    dataset: dict[str, Any],
    workload: dict[str, Any],
    truth: dict[str, Any],
    arms: dict[str, dict[str, Any]],
    dataset_progress: dict[str, str] | None = None,
    experiment_status: str = "planned",
    cleanup: dict[str, Any] | None = None,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_identity_digest(research_commit_sha, "research_commit_sha")
    validate_system_freeze(system_freeze)
    if set(arms) != set(ARM_IDS):
        raise ValueError("build_rq1_artifact requires exactly the three RQ1 arms")
    normalized = copy.deepcopy(arms)
    for arm_id in ARM_IDS:
        arm = normalized[arm_id]
        arm["summary"] = summarize_per_query(arm["per_query"])
        if arm_id == "pg16-advisor":
            arm["full_data_evaluation_metrics"] = copy.deepcopy(arm["summary"])
    artifact: dict[str, Any] = {
        "format_version": RQ1_FORMAT,
        "experiment_id": "rq1-matched-comparison-v1",
        "paper_specification": PAPER_SPECIFICATION,
        "system_freeze": copy.deepcopy(system_freeze),
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "research_commit_sha": research_commit_sha,
        "dataset": copy.deepcopy(dataset),
        "workload": copy.deepcopy(workload),
        "truth": copy.deepcopy(truth),
        "arms": list(ARM_IDS),
        "per_arm": normalized,
        "paired_comparison": {
            "default_vs_advisor": _pair_summary(
                normalized["pg16-default"]["per_query"],
                normalized["pg16-advisor"]["per_query"],
                include_per_query=False,
            ),
            "target10000_vs_advisor": _pair_summary(
                normalized["pg16-target10000"]["per_query"],
                normalized["pg16-advisor"]["per_query"],
                include_per_query=False,
            ),
        },
        "dataset_progress": dataset_progress
        or {
            dataset_id: ("complete" if dataset_id == CANARY_DATASET_ID else "planned")
            for dataset_id in DATASET_PROGRESS
        },
        "experiment_status": experiment_status,
        "cleanup": cleanup or {"clusters_stopped": True},
        "provenance": provenance or {},
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    validate_rq1_artifact(artifact)
    return artifact


def validate_rq1_file(path: Path) -> dict[str, Any]:
    return validate_rq1_artifact(read_json(path))


def inspect_rq1_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    summary = validate_rq1_artifact(artifact)
    return {
        **summary,
        "research_commit_sha": artifact["research_commit_sha"],
        "dataset_progress": artifact["dataset_progress"],
        "summaries": {arm_id: artifact["per_arm"][arm_id]["summary"] for arm_id in ARM_IDS},
        "paired_comparison": {
            key: {
                "query_count": value["query_count"],
                "classification_counts": value["classification_counts"],
            }
            for key, value in artifact["paired_comparison"].items()
        },
    }


def _run(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"command failed ({completed.returncode}): {command[0]}: {completed.stderr[-2000:]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {"status": "ok"}


def _same_catalog_dsn(dsn: str, database: str) -> str:
    """Use a disposable planner database with the snapshot's catalog name."""

    match = re.search(r"(?:^|\s)dbname=([^\s]+)", dsn)
    if match is None:
        return f"{dsn} dbname={database}"
    return f"{dsn[: match.start(1)]}{database}{dsn[match.end(1) :]}"


def _ensure_planner_catalog(dsn: str, database: str) -> str:
    import psycopg

    maintenance_dsn = _same_catalog_dsn(dsn, "postgres")
    with psycopg.connect(maintenance_dsn, autocommit=True) as connection:
        exists = bool(
            connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = %s)", (database,)
            ).fetchone()[0]
        )
        if not exists:
            connection.execute(f'CREATE DATABASE "{database}"')
    return _same_catalog_dsn(dsn, database)


def _semantic_build_identity(identity: dict[str, Any]) -> dict[str, Any]:
    semantic = {
        "format_version": identity["format_version"],
        "role": identity["role"],
        "source_commit_sha": identity["source_commit_sha"],
        "postgres_version": identity["postgres_version"].split(") ", 1)[-1],
        "configure_features": identity["configure_features"],
        "build_environment": identity["build_environment"],
        "compiler": Path(identity["compiler"]).name,
        "compiler_version": identity["compiler_version"],
        "make_version": identity["make_version"],
        "pg_config_configure": identity["pg_config_configure"],
        "locale": identity["locale"],
        "encoding": identity["encoding"],
    }
    return {**semantic, "build_identity_digest": semantic_digest(semantic)}


def _session_settings(connection: Any) -> dict[str, str]:
    for statement in (
        "SET TIME ZONE 'UTC'",
        "SET DateStyle TO 'ISO, YMD'",
        "SET jit = off",
        "SET plan_cache_mode = auto",
    ):
        connection.execute(statement)
    return {
        name: str(connection.execute(f"SHOW {name}").fetchone()[0])
        for name in ("TimeZone", "DateStyle", "jit", "plan_cache_mode")
    }


def _evaluate_arm(
    dsn: str,
    workload: list[dict[str, Any]],
    truth_rows: dict[str, int],
    raw_path: Path,
    *,
    arm_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str], str, list[dict[str, Any]]]:
    import psycopg

    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(
        dsn, application_name=f"extstats-research-rq1-{arm_id}", autocommit=True
    ) as connection:
        settings = _session_settings(connection)
        estimates = _explain_rows(connection, workload)
        stats = _stats(connection, "census13")
        ordinary = _ordinary_fingerprint(connection, "census13")
    records = [
        {
            "query_id": query["query_id"],
            "estimate": estimates[query["query_id"]],
            "truth": int(truth_rows[query["query_id"]]),
            "weight": float(query.get("weight", 1.0)),
            "qerror": qerror(estimates[query["query_id"]], int(truth_rows[query["query_id"]])),
        }
        for query in workload
    ]
    with raw_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return records, settings, ordinary, stats


def _truth_identity(truth: dict[str, Any]) -> dict[str, Any]:
    source = truth.get("source")
    if not isinstance(source, dict) or source.get("kind") != "production-exact-execution":
        raise ValueError("Census13 canary requires production-exact-execution truth")
    return {
        "source_kind": source["kind"],
        "semantic_digest": truth.get("semantic_digest"),
        "source_snapshot_semantic_digest": truth.get("source_snapshot_semantic_digest"),
        "source_view_token": source.get("source_view_token"),
        "workload_id": truth.get("workload_id"),
        "query_count": len(truth.get("truths", [])),
    }


def _truth_rows(truth: dict[str, Any], workload_id: str) -> dict[str, int]:
    if truth.get("workload_id") != workload_id:
        raise ValueError("Census13 truth is bound to a different workload")
    rows = {item["query_id"]: int(item["cardinality"]) for item in truth.get("truths", [])}
    if len(rows) != 10_000:
        raise ValueError("Census13 canary requires exactly 10000 truth labels")
    return rows


def _arm_common(
    *,
    workload_identity: dict[str, Any],
    truth_identity: dict[str, Any],
    build_identity: dict[str, Any],
    load: dict[str, Any],
    records: list[dict[str, Any]],
    raw_path: Path,
    logical_path: str,
    settings: dict[str, str],
    ordinary: str,
    stats: list[dict[str, Any]],
    evaluation_mode: str,
) -> dict[str, Any]:
    return {
        "evaluation_mode": evaluation_mode,
        "headline_source": "full_data_evaluation_metrics",
        "deployment_build_identity": build_identity,
        "data_identity": {
            "benchmark_id": load["benchmark_id"],
            "source_csv_sha256": load["source_csv_sha256"],
            "rows": load["rows"],
            "schema_contract_id": load["schema_contract_id"],
        },
        "workload_identity": workload_identity,
        "truth_identity": truth_identity,
        "statistics_policy": {
            "requested_target": load.get("statistics_target"),
            "actual_column_targets": load.get("statistics_targets"),
            "seed_identifier": load.get("seed_identifier"),
            "setseed_sql": load.get("seed_sql"),
        },
        "analyze_protocol": {"count": load.get("analyze_count"), "controlled": True},
        "physical_extended_statistics": stats,
        "physical_extended_statistics_count": len(stats),
        "ordinary_statistics_fingerprint": ordinary,
        "planner_session_settings": settings,
        "per_query_artifact": {
            "logical_path": logical_path,
            "sha256": sha256_file(raw_path),
        },
        "per_query": records,
    }


def run_census13_canary(
    *,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    data_root: Path | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    advisor_command: str = "extstats-advisor",
    truth_artifact: Path | None = None,
) -> dict[str, Any]:
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    system_freeze = load_system_freeze(DEFAULT_SYSTEM_FREEZE_PATH)
    verify_frozen_systems(advisor_root, patched_postgres_root)
    stock_lab = _read_identity(role_spec("stock"))
    patched_lab = _read_identity(role_spec("patched"))
    if stock_lab["source_commit_sha"] != STOCK_POSTGRES_SHA:
        raise ValueError("stock postgres-lab identity is not pinned to system-freeze-v1")
    if patched_lab["source_commit_sha"] != PATCHED_POSTGRES_SHA:
        raise ValueError("patched postgres-lab identity is not pinned to system-freeze-v1")
    stock_build = _semantic_build_identity(stock_lab)
    patched_build = _semantic_build_identity(patched_lab)
    dataset = census13.inspect(data_root)
    runtime = research_root / ".runtime" / "rq1-census13-canary"
    runtime.mkdir(parents=True, exist_ok=True)
    workload_path = runtime / "workload.json"
    census13.extract_workload(workload_path, data_root, "test")
    workload = read_json(workload_path)
    if workload["workload_id"] != WORKLOAD_ID or len(workload["queries"]) != 10_000:
        raise ValueError("Census13 canary workload is not the frozen 10000-query test workload")
    truth_path = truth_artifact or research_root / DEFAULT_TRUTH_ARTIFACT
    truth = read_json(truth_path)
    truth_identity = _truth_identity(truth)
    if truth_identity["semantic_digest"] != TRUTH_SEMANTIC_DIGEST:
        raise ValueError("selected Census13 truth is not the audited production-exact artifact")
    truths = _truth_rows(truth, workload["workload_id"])
    workload_identity = {
        "workload_id": workload["workload_id"],
        "sha256": sha256_file(workload_path),
        "query_count": len(workload["queries"]),
        "canonical_source_sha256": workload["provenance"]["canonical_source_sha256"],
    }
    dataset_identity = {
        "dataset_id": dataset["benchmark_id"],
        "content_identity": dataset["dataset_content_identity"],
        "csv_sha256": dataset["csv_sha256"],
        "rows": dataset["expected_rows"],
    }
    arms: dict[str, dict[str, Any]] = {}
    try:
        reinit_role("stock")
        load = load_census13(
            stock_dsn,
            data_root=data_root,
            reset_disposable=True,
            statistics_target=None,
            seed_identifier=SEED_IDENTIFIER,
        )
        raw = runtime / "pg16-default-per-query.jsonl"
        records, settings, ordinary, stats = _evaluate_arm(
            stock_dsn, workload["queries"], truths, raw, arm_id="pg16-default"
        )
        arms["pg16-default"] = _arm_common(
            workload_identity=workload_identity,
            truth_identity=truth_identity,
            build_identity=stock_build,
            load=load,
            records=records,
            raw_path=raw,
            logical_path=".runtime/rq1-census13-canary/pg16-default-per-query.jsonl",
            settings=settings,
            ordinary=ordinary,
            stats=stats,
            evaluation_mode="stock-full-data-physical",
        )
        reinit_role("stock")
        load = load_census13(
            stock_dsn,
            data_root=data_root,
            reset_disposable=True,
            statistics_target=10_000,
            seed_identifier=SEED_IDENTIFIER,
        )
        raw = runtime / "pg16-target10000-per-query.jsonl"
        records, settings, ordinary, stats = _evaluate_arm(
            stock_dsn, workload["queries"], truths, raw, arm_id="pg16-target10000"
        )
        arms["pg16-target10000"] = _arm_common(
            workload_identity=workload_identity,
            truth_identity=truth_identity,
            build_identity=stock_build,
            load=load,
            records=records,
            raw_path=raw,
            logical_path=".runtime/rq1-census13-canary/pg16-target10000-per-query.jsonl",
            settings=settings,
            ordinary=ordinary,
            stats=stats,
            evaluation_mode="stock-full-data-physical",
        )
        reinit_role("patched")
        reinit_role("stock")
        planner_catalog_dsn = _ensure_planner_catalog(patched_dsn, "extstats_stock")
        advisor_result = run_census13(
            production_dsn=stock_dsn,
            planner_dsn=planner_catalog_dsn,
            advisor_root=advisor_root,
            patched_postgres_root=patched_postgres_root,
            output_root=runtime / "advisor-design",
            sample_rows=10_000,
            sample_seed=42,
            statistics_target=100,
            candidate_limit=8,
            search_wall_clock_seconds=180,
            data_root=data_root,
            reset_disposable=True,
            seed_identifier=SEED_IDENTIFIER,
            advisor_command=advisor_command,
        )
        advisor_run = Path(advisor_result["run_directory"])
        generated_truth = read_json(advisor_run / "ground-truth-v1.json")
        if _truth_rows(generated_truth, WORKLOAD_ID) != truths:
            raise ValueError("advisor design-time exact truth differs from matched canary truth")
        _run([advisor_command, "sandbox", "destroy", "postgres", "--dsn", planner_catalog_dsn])
        reinit_role("stock")
        deployment_load = load_census13(
            stock_dsn,
            data_root=data_root,
            reset_disposable=True,
            statistics_target=100,
            seed_identifier=SEED_IDENTIFIER,
        )
        deployment_output = runtime / "deployment-result-v1.json"
        source_paths = {
            key: advisor_run / filename
            for key, filename in {
                "snapshot": "advisor-snapshot",
                "candidate_universe": "candidate-universe.json",
                "native_repository": "native-stats-repository",
                "ground_truth": "ground-truth-v1.json",
                "singleton_profile": "singleton-profile.json",
                "optimization_plan": "optimization-plan.json",
                "search_result": "search-result.json",
                "recommendation": "recommendation.json",
            }.items()
        }
        _run(
            [
                advisor_command,
                "deployment",
                "apply",
                "postgres",
                *[str(source_paths[key]) for key in source_paths],
                "--dsn",
                stock_dsn,
                "--output",
                str(deployment_output),
            ]
        )
        _run(
            [
                advisor_command,
                "deployment",
                "validate",
                str(deployment_output),
                str(source_paths["recommendation"]),
                "--snapshot",
                str(source_paths["snapshot"]),
                "--candidate-universe",
                str(source_paths["candidate_universe"]),
                "--native-repository",
                str(source_paths["native_repository"]),
                "--ground-truth",
                str(source_paths["ground_truth"]),
                "--singleton-profile",
                str(source_paths["singleton_profile"]),
                "--optimization-plan",
                str(source_paths["optimization_plan"]),
                "--search-result",
                str(source_paths["search_result"]),
            ]
        )
        deployment = read_json(deployment_output)
        raw = runtime / "pg16-advisor-per-query.jsonl"
        load = deployment_load
        records, settings, ordinary, stats = _evaluate_arm(
            stock_dsn, workload["queries"], truths, raw, arm_id="pg16-advisor"
        )
        recommendation = read_json(source_paths["recommendation"])
        search_result = read_json(source_paths["search_result"])
        arms["pg16-advisor"] = _arm_common(
            workload_identity=workload_identity,
            truth_identity=truth_identity,
            build_identity=stock_build,
            load=load,
            records=records,
            raw_path=raw,
            logical_path=".runtime/rq1-census13-canary/pg16-advisor-per-query.jsonl",
            settings=settings,
            ordinary=ordinary,
            stats=stats,
            evaluation_mode="stock-full-data-deployment",
        )
        arms["pg16-advisor"]["design_time_build_identity"] = patched_build
        arms["pg16-advisor"]["recommendation"] = {
            "semantic_digest": recommendation["semantic_digest"],
            "selected_candidate_ids": recommendation["selected_candidate_ids"],
            "deployment_ordered_candidate_ids": recommendation["deployment_ordered_candidate_ids"],
            "deployment_result_semantic_digest": deployment.get("semantic_digest"),
        }
        arms["pg16-advisor"]["sandbox_optimization_objective"] = {
            "contract": "weighted-workload-mean-v1",
            "baseline": search_result["baseline_objective"],
            "final": search_result["final_objective"],
            "termination_reason": search_result.get("termination_reason"),
            "source": "patched-postgresql-design-time-sandbox",
        }
        for arm in arms.values():
            arm["truth_identity"] = truth_identity
            arm["workload_identity"] = workload_identity
    finally:
        for role in ("stock", "patched"):
            try:
                stop_role(role)
            except (OSError, RuntimeError, ValueError):
                pass
    artifact = build_rq1_artifact(
        research_commit_sha=research_identity["research_commit_sha"],
        system_freeze=system_freeze,
        dataset=dataset_identity,
        workload=workload_identity,
        truth=truth_identity,
        arms=arms,
        cleanup={
            "clusters_stopped": True,
            "fresh_state_protocol": "postgres-lab reinit before every arm",
            "default_no_extstats_verified": True,
            "target10000_no_extstats_verified": True,
            "advisor_deployment_verified": True,
        },
        provenance={
            "truth_artifact_semantic_digest": truth_identity["semantic_digest"],
            "advisor_design_run_directory": ".runtime/rq1-census13-canary/advisor-design",
            "advisor_design_run_manifest_sha256": sha256_file(
                Path(advisor_result["run_directory"]) / "manifest.json"
            ),
            "patched_planner_catalog": "extstats_stock",
            "system_freeze_path": "paper/system-freeze-v1.json",
        },
    )
    write_json(output, artifact)
    return inspect_rq1_artifact(output)
