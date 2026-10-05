"""Full-table transfer validation for one frozen Recommendation.

The production advisor remains the owner of Recommendation construction and
deployment.  This module only binds immutable source artifacts, measures
stock PostgreSQL, and writes compact transfer evidence.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .analysis.audit import distribution
from .datasets import census13
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_census13
from .provenance import read_json, reject_credentials, write_json

FORMAT_VERSION = "arecel-full-data-transfer-v1"
DEPLOYMENT_POLICY = "postgresql-add-only-deployment-v1"
EXPECTED_SOURCE = {
    "snapshot": "7843ccf2e1ae0b017fda3c5a8e97797118b8534bdd97d854ed1ef1ca9631568d",
    "candidate_universe": "8912122ddc7cff7b4047b470de13edf89ecc8dc5237107c25aec7c5d8ac86b74",
    "native_repository": "787483dabec6816123566574b1a4ab8d68d930cc2f1c4e4cd543241fb764ed62",
    "singleton_profile": "2665d5ff54331bc768bb4661554b3ed763cf0f445545ac2ed5e5169657c0aa78",
    "optimization_plan": "dc0ffdcab66942888be063dea9540ce3a87a9e431c8f0830da3e6ff1f4a15963",
    "search_result": "ad77fafbd3b9f1c1ffae62dfa20d987c02a4636dda7ae6865d35921d4172bd6b",
    "ground_truth": "b70410c12af2b7e52d3d3b9972ab086a4270c2743c4363f58ed400a3bdb9d491",
}
EXPECTED_CALIBRATION = "260db38389ed260bcf4889c0c144b91ba0d48a035150823bc7a99911992e1bee"
EXPECTED_MEMBERSHIP = (
    "cand_cb55f02882e2d0ef75203787",
    "cand_ffc5c63805d1be782d2b4a02",
    "cand_8a4f08da78a4aa33ad1a459b",
    "cand_ae97bada52eb7042e5fc00b8",
    "cand_d339256e88c14460f89f50b8",
    "cand_7ac24ff8a73a964272444efd",
    "cand_384c338346aee8994f53b6e6",
)
ACCEPTED_MOVE_ORDER = (
    "cand_cb55f02882e2d0ef75203787",
    "cand_ffc5c63805d1be782d2b4a02",
    "cand_8a4f08da78a4aa33ad1a459b",
    "cand_7ac24ff8a73a964272444efd",
    "cand_d339256e88c14460f89f50b8",
    "cand_ae97bada52eb7042e5fc00b8",
    "cand_384c338346aee8994f53b6e6",
)


def _source_paths(source_run: Path, budget_directory: Path) -> dict[str, Path]:
    return {
        "snapshot": source_run / "advisor-snapshot",
        "candidate_universe": source_run / "candidate-universe.json",
        "native_repository": source_run / "native-stats-repository",
        "ground_truth": source_run / "ground-truth-v1.json",
        "singleton_profile": source_run / "singleton-profile.json",
        "optimization_plan": budget_directory / "optimization-plan.json",
        "search_result": budget_directory / "search-result.json",
        "workload": source_run / "workload.json",
    }


def validate_frozen_sources(source_run: Path, budget_directory: Path) -> dict[str, Any]:
    """Check the exact immutable source chain used by the transfer experiment."""
    paths = _source_paths(source_run, budget_directory)
    manifest = read_json(source_run / "manifest.json")
    actual: dict[str, str] = {}
    for name, expected in EXPECTED_SOURCE.items():
        if name == "ground_truth":
            path = paths[name]
        else:
            path = paths[name]
        if path.is_dir():
            value = read_json(path / "manifest.json")
            digest = value.get("semantic_digest") or value.get("semantic_digest")
        else:
            value = read_json(path)
            digest = value.get("semantic_digest")
        if digest != expected:
            raise ValueError(f"frozen {name} digest {digest!r} does not match {expected}")
        actual[name] = digest
    if manifest.get("run_id") != source_run.name:
        raise ValueError("source run directory does not match its RunManifest")
    if manifest.get("research_commit_sha") != "9de794eaa62389c48ee6c5cebb192a467b9696cc":
        raise ValueError("source run was not produced by the frozen canonical research revision")
    if manifest.get("dataset_content_identity") != census13.inspect().get(
        "dataset_content_identity"
    ):
        raise ValueError("source run dataset identity does not match the audited Census13 data")
    if read_json(paths["search_result"])["final_ordered_candidate_ids"] != list(
        EXPECTED_MEMBERSHIP
    ):
        raise ValueError("frozen search membership is not the expected converged K=8 membership")
    calibration = budget_directory.parent / "calibration-v1.json"
    calibration_digest = read_json(calibration).get("semantic_digest")
    if calibration_digest != EXPECTED_CALIBRATION:
        raise ValueError("search-budget calibration digest is not the frozen converged calibration")
    return {
        "source_run_id": source_run.name,
        "source_artifacts": actual,
        "dataset_content_identity": manifest["dataset_content_identity"],
        "workload_sha256": manifest["workload_sha256"],
        "calibration_digest": calibration_digest,
        "advisor_repository": manifest["advisor_repository"],
        "advisor_commit_sha": manifest["advisor_commit_sha"],
        "patched_postgres_repository": manifest["patched_postgres_repository"],
        "patched_postgres_commit_sha": manifest["patched_postgres_commit_sha"],
    }


def precedence_check(
    search: dict[str, Any], singleton: dict[str, Any], recommendation: dict[str, Any]
) -> dict[str, Any]:
    """Prove D=F restricted to M*, independently of accepted move order."""
    profiles = {item["candidate_id"]: item for item in singleton["candidate_profiles"]}
    membership = tuple(search["final_ordered_candidate_ids"])
    expected = tuple(sorted(membership, key=lambda item: profiles[item]["frozen_precedence_rank"]))
    actual = tuple(recommendation["deployment_ordered_candidate_ids"])
    if set(actual) != set(membership):
        raise ValueError("Recommendation membership differs from the frozen SearchResult")
    if actual != expected:
        raise ValueError(f"Recommendation order {actual!r} is not precedence order {expected!r}")
    moves = tuple(item["added_candidate_id"] for item in search["accepted_moves"])
    return {
        "membership": list(membership),
        "accepted_move_order": list(moves),
        "precedence_order": list(expected),
        "accepted_move_order_differs": moves != expected,
        "policy": "D=F|M*; accepted move order is not deployment order",
    }


def classify_change(before: float, after: float) -> str:
    if after < before:
        return "improved"
    if after > before:
        return "worsened"
    return "unchanged"


def build_transfer_records(
    truth: Iterable[dict[str, Any]],
    sandbox: dict[str, dict[str, float]],
    estimates: dict[str, dict[str, int]],
) -> list[dict[str, Any]]:
    records = []
    for item in sorted(truth, key=lambda value: value["query_id"]):
        query_id = item["query_id"]
        truth_rows = int(item["cardinality"])
        phases = estimates[query_id]
        qerrors = {
            phase: max(float(rows), 1.0) / max(float(truth_rows), 1.0)
            if rows >= truth_rows
            else max(float(truth_rows), 1.0) / max(float(rows), 1.0)
            for phase, rows in phases.items()
        }
        records.append(
            {
                "query_id": query_id,
                "true_rows": truth_rows,
                "sandbox_baseline_estimate": sandbox[query_id]["baseline_estimate"],
                "sandbox_final_estimate": sandbox[query_id]["final_estimate"],
                "sandbox_baseline_qerror": sandbox[query_id]["baseline_qerror"],
                "sandbox_final_qerror": sandbox[query_id]["final_qerror"],
                "p0_estimated_rows": phases["p0"],
                "p1_estimated_rows": phases["p1"],
                "p2_estimated_rows": phases["p2"],
                "p0_qerror": qerrors["p0"],
                "p1_qerror": qerrors["p1"],
                "p2_qerror": qerrors["p2"],
                "p2_to_p1_classification": classify_change(qerrors["p2"], qerrors["p1"]),
                "p0_to_p1_classification": classify_change(qerrors["p0"], qerrors["p1"]),
                "sandbox_classification": classify_change(
                    sandbox[query_id]["baseline_qerror"], sandbox[query_id]["final_qerror"]
                ),
            }
        )
    return records


def summarize_transfer(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {"query_count": len(records)}
    for phase in ("p0", "p2", "p1"):
        result[phase] = distribution(records, f"{phase}_qerror", _mean(records, f"{phase}_qerror"))
    for label in ("p2_to_p1_classification", "p0_to_p1_classification"):
        result[label] = {
            value: sum(row[label] == value for row in records)
            for value in ("improved", "unchanged", "worsened")
        }
    result["top_10_p2_to_p1_improvements"] = _ranked(records, "p2_qerror", "p1_qerror", False)
    result["top_10_p2_to_p1_regressions"] = _ranked(records, "p2_qerror", "p1_qerror", True)
    result["sandbox_production_correspondence"] = _correspondence(records)
    return result


def _mean(records: list[dict[str, Any]], field: str) -> float:
    return sum(float(row[field]) for row in records) / len(records)


def _ranked(
    records: list[dict[str, Any]], before: str, after: str, reverse: bool
) -> list[dict[str, Any]]:
    rows = sorted(records, key=lambda row: row[before] - row[after], reverse=not reverse)
    return [
        {
            "query_id": row["query_id"],
            "before_qerror": row[before],
            "after_qerror": row[after],
            "delta": row[before] - row[after],
        }
        for row in rows[:10]
    ]


def _correspondence(records: list[dict[str, Any]]) -> dict[str, int]:
    result = {
        "same_direction": 0,
        "opposite_direction": 0,
        "sandbox_improved_production_improved": 0,
        "sandbox_improved_production_worsened": 0,
    }
    for row in records:
        sandbox = row["sandbox_classification"]
        production = row["p2_to_p1_classification"]
        if sandbox == production:
            result["same_direction"] += 1
        elif {sandbox, production} == {"improved", "worsened"}:
            result["opposite_direction"] += 1
        if sandbox == "improved" and production == "improved":
            result["sandbox_improved_production_improved"] += 1
        if sandbox == "improved" and production == "worsened":
            result["sandbox_improved_production_worsened"] += 1
    return result


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed: {command[0]} ({completed.stderr[-1000:]})")


def _run_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed: {command[0]} ({completed.stderr[-1000:]})")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    return json.loads(lines[-1])


def _explain_rows(connection: Any, workload: list[dict[str, Any]]) -> dict[str, int]:
    result = {}
    for query in workload:
        plan = connection.execute(f"EXPLAIN (FORMAT JSON) {query['sql']}").fetchone()[0]
        if isinstance(plan, str):
            plan = json.loads(plan)
        result[query["query_id"]] = int(plan[0]["Plan"]["Plan Rows"])
    return result


def _wrapped_count_sql(query_sql: str) -> str:
    """Turn a workload SELECT into a representative-count subquery safely."""
    return f"SELECT count(*) FROM ({query_sql.rstrip().rstrip(';').rstrip()}) AS q"


def _stats(connection: Any) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT s.oid, n.nspname, s.stxname, s.stxkind::text, s.stxkeys::text,
               s.stxstattarget, d.stxdmcv IS NOT NULL
          FROM pg_catalog.pg_statistic_ext s
          JOIN pg_catalog.pg_namespace n ON n.oid=s.stxnamespace
          JOIN pg_catalog.pg_class c ON c.oid=s.stxrelid
          LEFT JOIN pg_catalog.pg_statistic_ext_data d ON d.stxoid=s.oid
         WHERE n.nspname='public' AND c.relname='census13'
         ORDER BY s.oid
        """
    ).fetchall()
    return [
        {
            "oid": int(r[0]),
            "schema": str(r[1]),
            "name": str(r[2]),
            "kind": {"{m}": "postgresql.mcv", "{d}": "postgresql.dependencies"}.get(
                str(r[3]), str(r[3])
            ),
            "columns": str(r[4]),
            "target": int(r[5]),
            "payload": bool(r[6]),
        }
        for r in rows
    ]


def _drop_managed(connection: Any, names: list[str]) -> None:
    from psycopg import sql

    for name in names:
        connection.execute(
            sql.SQL("DROP STATISTICS {}.{}").format(sql.Identifier("public"), sql.Identifier(name))
        )


def _qerror(rows: int, truth: int) -> float:
    return (
        max(float(rows), 1.0) / max(float(truth), 1.0)
        if rows >= truth
        else max(float(truth), 1.0) / max(float(rows), 1.0)
    )


def run_full_data_transfer(
    source_run: Path,
    budget_directory: Path,
    production_dsn: str,
    *,
    planner_dsn: str | None = None,
    output_directory: Path | None = None,
    advisor_command: str = "extstats-advisor",
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Build, deploy, measure, and write one Census13 transfer experiment."""
    if not production_dsn:
        raise ValueError("production DSN is required and is never written to artifacts")
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    system_identity = verify_frozen_systems(
        Path("/home/wqts/projects/extstats-advisor"),
        Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    paths = _source_paths(source_run, budget_directory)
    source = validate_frozen_sources(source_run, budget_directory)
    output = output_directory or budget_directory.parent.parent / "full-data-transfer-k8"
    output.mkdir(parents=True, exist_ok=True)
    recommendation_path = output / "recommendation.json"
    _run(
        [
            advisor_command,
            "recommendation",
            "build",
            "postgres",
            *(
                str(paths[k])
                for k in (
                    "snapshot",
                    "candidate_universe",
                    "native_repository",
                    "ground_truth",
                    "singleton_profile",
                    "optimization_plan",
                    "search_result",
                )
            ),
            "--output",
            str(recommendation_path),
        ]
    )
    _run(
        [
            advisor_command,
            "recommendation",
            "validate",
            str(recommendation_path),
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
    )
    recommendation = read_json(recommendation_path)
    precedence = precedence_check(
        read_json(paths["search_result"]), read_json(paths["singleton_profile"]), recommendation
    )
    reject_credentials(recommendation)

    load = load_census13(
        production_dsn, data_root=data_root, reset_disposable=True, statistics_target=100
    )
    if load["statistics_target"] != 100:
        raise ValueError("ordinary statistics target is not the required 100")
    import psycopg

    truth = read_json(paths["ground_truth"])["truths"]
    workload = read_json(paths["workload"])["queries"]
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        pre_stats = _stats(connection)
        if pre_stats:
            raise ValueError(f"fresh production relation has extended statistics: {pre_stats}")
        ordinary_before = _ordinary_fingerprint(connection)
        p0 = _explain_rows(connection, workload)
        sanity_ids = {
            "arecel_census13_test_000000",
            "arecel_census13_test_005803",
            "arecel_census13_test_007775",
            "arecel_census13_test_008202",
        }
        sanity = {
            row["query_id"]: int(connection.execute(_wrapped_count_sql(row["sql"])).fetchone()[0])
            for row in workload
            if row["query_id"] in sanity_ids
        }
        expected = {
            row["query_id"]: int(row["cardinality"])
            for row in truth
            if row["query_id"] in sanity_ids
        }
        if sanity != expected:
            raise ValueError(f"representative frozen truth mismatch: {sanity!r} != {expected!r}")

    deployment_path = output / "deployment-result-v1.json"
    _run(
        [
            advisor_command,
            "deployment",
            "apply",
            "postgres",
            *(
                str(paths[k])
                for k in (
                    "snapshot",
                    "candidate_universe",
                    "native_repository",
                    "ground_truth",
                    "singleton_profile",
                    "optimization_plan",
                    "search_result",
                )
            ),
            str(recommendation_path),
            "--dsn",
            production_dsn,
            "--output",
            str(deployment_path),
        ]
    )
    deployment = read_json(deployment_path)
    if deployment.get("deployment_policy") != DEPLOYMENT_POLICY or not deployment.get(
        "post_commit_verified"
    ):
        raise ValueError("deployment result does not prove add-only committed post-verification")
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        managed_stats = _stats(connection)
        if len(managed_stats) != len(EXPECTED_MEMBERSHIP):
            raise ValueError("deployment did not create exactly seven managed statistics")
        deployed_names = [item["name"] for item in deployment["deployed_objects"]]
        if [item["name"] for item in managed_stats] != deployed_names:
            raise ValueError("physical OID order does not match the Recommendation order")
        if any(not item["payload"] or item["target"] != 100 for item in managed_stats):
            raise ValueError("managed statistics payload or target verification failed")
        deployed_by_name = {item["name"]: item for item in deployment["deployed_objects"]}
        for physical in managed_stats:
            deployed = deployed_by_name[physical["name"]]
            if physical["kind"] != deployed["kind"] or physical["columns"] != " ".join(
                str(value) for value in deployed["column_ordinals"]
            ):
                raise ValueError(
                    "physical managed statistics definition differs from DeploymentResult"
                )
        p1 = _explain_rows(connection, workload)
        ordinary_after_deploy = _ordinary_fingerprint(connection)
        names = deployed_names

    with psycopg.connect(production_dsn, autocommit=False) as connection:
        connection.execute("BEGIN")
        try:
            _drop_managed(connection, names)
            ordinary_p2 = _ordinary_fingerprint(connection)
            p2 = _explain_rows(connection, workload)
        finally:
            connection.rollback()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        restored_stats = _stats(connection)
        if restored_stats != managed_stats:
            raise ValueError("paired-control rollback did not restore managed statistics exactly")
        if _ordinary_fingerprint(connection) != ordinary_after_deploy:
            raise ValueError("paired-control rollback changed ordinary statistics")

    if not planner_dsn:
        raise ValueError("--planner-dsn is required to bind per-query frozen sandbox estimates")
    sandbox = _sandbox_records(paths, planner_dsn, advisor_command)
    estimates = {
        query_id: {"p0": p0[query_id], "p1": p1[query_id], "p2": p2[query_id]} for query_id in p0
    }
    records = build_transfer_records(truth, sandbox, estimates)
    for row in records:
        row["weight"] = 1.0
    write_json(
        output / "full-data-transfer-v1.json",
        {
            "format_version": FORMAT_VERSION,
            "source": source,
            "system_identity": {**research_identity, **system_identity},
            "recommendation_semantic_digest": recommendation["semantic_digest"],
            "deployment_result_semantic_digest": deployment["semantic_digest"],
            "dataset": {
                "content_identity": source["dataset_content_identity"],
                "csv_sha256": load["source_csv_sha256"],
                "rows": load["rows"],
                "schema_contract_id": load["schema_contract_id"],
                "server_version": load["server_version"],
            },
            "pre_deployment_extended_statistics": pre_stats,
            "managed_statistics": managed_stats,
            "restored_statistics": restored_stats,
            "precedence": precedence,
            "ordinary_stats_fingerprint_p0": ordinary_before,
            "ordinary_stats_fingerprint_p2": ordinary_p2,
            "ordinary_stats_fingerprint_p1": ordinary_after_deploy,
            "ordinary_stats_p0_p2_equal": ordinary_before == ordinary_p2,
            "summary": summarize_transfer(records),
            "credentials_recorded": False,
        },
    )
    with (output / "per-query-transfer-v1.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return {
        "output_directory": str(output),
        "recommendation": recommendation["semantic_digest"],
        "deployment": deployment["semantic_digest"],
        "query_count": len(records),
    }


def _ordinary_fingerprint(connection: Any) -> str:
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    oid = int(connection.execute("SELECT 'public.census13'::regclass::oid").fetchone()[0])
    return ordinary_stats_fingerprint(connection, oid)


def _sandbox_records(
    paths: dict[str, Path], planner_dsn: str, advisor_command: str
) -> dict[str, dict[str, float]]:
    """Evaluate frozen baseline/final membership in the existing planner sandbox."""
    baseline = _run_json(
        [
            advisor_command,
            "utility",
            "evaluate",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            "--dsn",
            planner_dsn,
        ]
    )
    candidate_args: list[str] = []
    for value in EXPECTED_MEMBERSHIP:
        candidate_args.extend(("--candidate", value))
    final = _run_json(
        [
            advisor_command,
            "utility",
            "evaluate",
            "postgres",
            str(paths["snapshot"]),
            str(paths["candidate_universe"]),
            str(paths["native_repository"]),
            str(paths["ground_truth"]),
            "--dsn",
            planner_dsn,
            *candidate_args,
        ]
    )
    baseline_rows = {row["query_id"]: row for row in baseline["per_query"]}
    final_rows = {row["query_id"]: row for row in final["per_query"]}
    if set(baseline_rows) != set(final_rows):
        raise ValueError("frozen sandbox baseline/final utility IDs differ")
    return {
        row["query_id"]: {
            "baseline_estimate": int(baseline_rows[row["query_id"]]["estimate"]),
            "final_estimate": int(final_rows[row["query_id"]]["estimate"]),
            "baseline_qerror": float(baseline_rows[row["query_id"]]["loss"]),
            "final_qerror": float(final_rows[row["query_id"]]["loss"]),
        }
        for row in baseline["per_query"]
    }
