"""Shared stock-PostgreSQL P0/P1/P2 transfer mechanics.

Dataset wrappers validate their immutable source chain and provide the
relation/workload/Recommendation configuration.  This module owns the
physical experiment lifecycle so Forest10 and Power7 cannot drift in their
deployment, counterfactual, or rollback semantics.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .full_data_transfer import (
    DEPLOYMENT_POLICY,
    _drop_managed,
    _explain_rows,
    _ordinary_fingerprint,
    _stats,
)
from .provenance import reject_credentials


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed: {command[0]} ({completed.stderr[-2000:]})")


def _deployment_command(
    advisor_command: str,
    paths: dict[str, Path],
    production_dsn: str,
    deployment_path: Path,
) -> list[str]:
    return [
        advisor_command,
        "deployment",
        "apply",
        "postgres",
        *(
            str(paths[key])
            for key in (
                "snapshot",
                "candidate_universe",
                "native_repository",
                "ground_truth",
                "singleton_profile",
                "optimization_plan",
                "search_result",
                "recommendation",
            )
        ),
        "--dsn",
        production_dsn,
        "--output",
        str(deployment_path),
    ]


def _validate_deployment_command(
    advisor_command: str, paths: dict[str, Path], result: Path
) -> list[str]:
    return [
        advisor_command,
        "deployment",
        "validate",
        str(result),
        str(paths["recommendation"]),
        "--snapshot",
        str(paths["snapshot"]),
        "--candidate-universe",
        str(paths["candidate_universe"]),
        "--native-repository",
        str(paths["native_repository"]),
        "--ground-truth",
        str(paths["ground_truth"]),
        "--singleton-profile",
        str(paths["singleton_profile"]),
        "--optimization-plan",
        str(paths["optimization_plan"]),
        "--search-result",
        str(paths["search_result"]),
    ]


def validate_audited_truth_binding(
    truth_rows: dict[str, int], audit_rows: dict[str, dict[str, Any]], *, expected_count: int
) -> dict[str, int]:
    """Bind every immutable GroundTruthSet cardinality to the audited labels."""
    if len(truth_rows) != expected_count:
        raise ValueError(f"GroundTruthSet does not contain exactly {expected_count} truths")
    if len(audit_rows) != expected_count or set(truth_rows) != set(audit_rows):
        raise ValueError("source truth/audit query IDs do not bind exactly")
    mismatched = [
        query_id
        for query_id, cardinality in truth_rows.items()
        if cardinality != int(audit_rows[query_id]["true_rows"])
    ]
    if mismatched:
        raise ValueError(f"source truth/audit labels differ: {mismatched[:3]}")
    return {"query_count": expected_count, "matched": expected_count, "mismatched": 0}


def read_sandbox_records(
    audit_per_query: Path, *, expected_count: int
) -> dict[str, dict[str, float]]:
    """Read frozen sandbox baseline/final values without replaying patched PG."""
    result: dict[str, dict[str, float]] = {}
    with audit_per_query.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            result[row["query_id"]] = {
                "baseline_estimate": int(row["baseline_estimated_rows"]),
                "final_estimate": int(row["final_estimated_rows"]),
                "baseline_qerror": float(row["baseline_qerror"]),
                "final_qerror": float(row["final_qerror"]),
            }
    if len(result) != expected_count:
        raise ValueError(f"source audit does not contain exactly {expected_count} sandbox rows")
    return result


def _validate_deployment(
    deployment: dict[str, Any],
    managed_stats: list[dict[str, Any]],
    expected_order: tuple[str, ...],
) -> None:
    if deployment.get("deployment_policy") != DEPLOYMENT_POLICY:
        raise ValueError("DeploymentResult does not prove the add-only policy")
    if deployment.get("commit_status") != "committed" or not deployment.get("post_commit_verified"):
        raise ValueError("DeploymentResult is not committed and post-commit verified")
    deployed_objects = deployment.get("deployed_objects", [])
    if len(deployed_objects) != len(expected_order):
        raise ValueError("DeploymentResult object count differs from frozen Recommendation")
    if deployment.get("deployment_ordered_candidate_ids") != list(expected_order):
        raise ValueError("DeploymentResult order differs from frozen Recommendation")
    names = [item["name"] for item in deployed_objects]
    if [item["name"] for item in managed_stats] != names:
        raise ValueError("physical OID order differs from Recommendation order")
    deployed_by_name = {item["name"]: item for item in deployed_objects}
    for physical in managed_stats:
        if (
            physical["kind"] != "postgresql.mcv"
            or physical["target"] != 100
            or not physical["payload"]
            or physical["schema"] != "public"
        ):
            raise ValueError("deployed statistics kind, target, or payload is invalid")
        deployed = deployed_by_name[physical["name"]]
        if physical["kind"] != deployed["kind"] or physical["columns"] != " ".join(
            str(value) for value in deployed["column_ordinals"]
        ):
            raise ValueError("physical statistics definition differs from DeploymentResult")


def run_transfer_phases(
    *,
    production_dsn: str,
    source_paths: dict[str, Path],
    workload: list[dict[str, Any]],
    relation: str,
    load: dict[str, Any],
    expected_order: tuple[str, ...],
    output_directory: Path,
    advisor_command: str,
) -> dict[str, Any]:
    """Run fresh load-adjacent P0, committed deployment P1, and rollback P2."""
    import psycopg

    timings: dict[str, float] = {}
    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        pre_stats = _stats(connection, relation)
        if pre_stats:
            raise ValueError(f"fresh {relation} relation has extended statistics: {pre_stats}")
        ordinary_p0 = _ordinary_fingerprint(connection, relation)
        p0 = _explain_rows(connection, workload)
    timings["p0_explain"] = round(time.monotonic() - started, 6)

    deployment_path = output_directory / "deployment-result-v1.json"
    started = time.monotonic()
    _run(_deployment_command(advisor_command, source_paths, production_dsn, deployment_path))
    _run(_validate_deployment_command(advisor_command, source_paths, deployment_path))
    timings["deployment_including_final_analyze"] = round(time.monotonic() - started, 6)
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    reject_credentials(deployment)

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        managed_stats = _stats(connection, relation)
        if len(managed_stats) != len(expected_order):
            raise ValueError("deployment did not create exactly the frozen object count")
        _validate_deployment(deployment, managed_stats, expected_order)
        names = [item["name"] for item in deployment["deployed_objects"]]
        p1 = _explain_rows(connection, workload)
        ordinary_p1 = _ordinary_fingerprint(connection, relation)
    timings["p1_explain"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        connection.execute("BEGIN")
        try:
            _drop_managed(connection, names)
            if _stats(connection, relation):
                raise ValueError("P2 did not drop exactly the advisor-managed objects")
            ordinary_p2 = _ordinary_fingerprint(connection, relation)
            p2 = _explain_rows(connection, workload)
        finally:
            connection.execute("ROLLBACK")
    timings["p2_transaction_evaluation_rollback"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        restored_stats = _stats(connection, relation)
        restored_fingerprint = _ordinary_fingerprint(connection, relation)
    if restored_stats != managed_stats:
        raise ValueError("P2 rollback did not restore exact managed statistics metadata")
    if restored_fingerprint != ordinary_p1 or ordinary_p2 != ordinary_p1:
        raise ValueError("P2 ordinary statistics fingerprint was not identical to P1")
    timings["restoration_verification"] = round(time.monotonic() - started, 6)

    return {
        "load": load,
        "p0": p0,
        "p1": p1,
        "p2": p2,
        "pre_stats": pre_stats,
        "managed_stats": managed_stats,
        "restored_stats": restored_stats,
        "ordinary_stats_fingerprint_p0": ordinary_p0,
        "ordinary_stats_fingerprint_p1": ordinary_p1,
        "ordinary_stats_fingerprint_p2": ordinary_p2,
        "ordinary_stats_p1_p2_equal": ordinary_p1 == ordinary_p2,
        "deployment": deployment,
        "deployment_path": str(deployment_path),
        "timings": timings,
    }
