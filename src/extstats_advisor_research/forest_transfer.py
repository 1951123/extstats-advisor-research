"""Forest10 full-data deployment-effect transfer experiment.

This path consumes the already validated Forest10 K=8 Recommendation and the
canonical source audit.  It does not rerun search, recreate DDL, or start the
patched planner.  Production deployment remains owned by the frozen advisor
CLI.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path
from typing import Any

from . import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_RESEARCH_REPOSITORY,
)
from .datasets import forest10
from .full_data_transfer import (
    DEPLOYMENT_POLICY,
    _drop_managed,
    _explain_rows,
    _ordinary_fingerprint,
    _stats,
    build_transfer_records,
    precedence_check,
    summarize_transfer,
)
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_forest10
from .provenance import read_json, reject_credentials, semantic_digest, write_json

FORMAT_VERSION = "arecel-forest10-full-data-transfer-k8-v1"
SOURCE_RUN_ID = "3a8737b6c3184ae2037100df"
SOURCE_RESEARCH_SHA = "88bfe76fefc7b3b1a39957a6191bab5a4da4fc1a"
SOURCE_ADVISOR_SHA = FROZEN_ADVISOR_SHA
EXPECTED_ARTIFACTS = {
    "snapshot": "45772e41d6b20c6e36d8704d550f824aa0623897bc0bb038678a5ca39e4dace4",
    "candidate_universe": "953be518707b25af35b80060b600d25bb513f58cb79b14cd4d8f7cb739587d3f",
    "native_repository": "a7769df7f7e88effc6dc416ef6277fc40006561c0dac24ee55202c011c93a06b",
    "singleton_profile": "5d2266a3c5b73ba1356d41ad23c1b0df7ed9cab08b822b64f1bb5009225d3544",
    "optimization_plan": "bd2898cf02692dd42aae078ab5423171763994e7e5e21438ed7dfc09e81ee9c9",
    "search_result": "cb60781ef04b3c779502faab4cbea21d79010fbad0ed83652a7d50b726272692",
    "ground_truth": "7004bc427c9d53c3e9bb4abaab0e8c27190b8fb8eebbd3914a6c99137250f6db",
    "recommendation": "62d7b66a42038b75f69a6041229c5860ccfd2e35475d0b15faece8683c015282",
    "audit": "9e02d23773ab4787970341ef15d9e1d49654d44210dc8d3e7d705b4d9f42b9f2",
}
EXPECTED_MEMBERSHIP = (
    "cand_c56ca74c0278286db475d565",
    "cand_ec7d07197c8eb8b1bd089107",
    "cand_85f3ee3b1e0ad076ce4d1737",
    "cand_18522b1af86d3973bc4242f9",
    "cand_c3bd2cc22c4c2bc964d3b17f",
    "cand_97531ff8f920d6eca79b7c71",
    "cand_a8b6bfd9f15c558452812c8c",
    "cand_f2aa6ed41c837b7ad0690814",
)


def _paths(source_run: Path) -> dict[str, Path]:
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
        "audit": source_run / "analysis" / "audit-v1.json",
        "audit_per_query": source_run / "analysis" / "per-query-v1.jsonl",
    }


def _artifact_digest(path: Path) -> str:
    value = read_json(path / "manifest.json") if path.is_dir() else read_json(path)
    return str(value.get("semantic_digest"))


def validate_forest_sources(source_run: Path) -> dict[str, Any]:
    """Validate the immutable source chain and all 10,000 audited labels."""
    source_run = Path(source_run)
    paths = _paths(source_run)
    manifest = read_json(source_run / "manifest.json")
    if source_run.name != SOURCE_RUN_ID or manifest.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("Forest transfer must use the frozen canonical source RunID")
    if manifest.get("benchmark_id") != forest10.BENCHMARK_ID:
        raise ValueError("source run is not the canonical Forest10 run")
    if manifest.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("source run was not produced by the frozen Forest10 research revision")
    if manifest.get("advisor_commit_sha") != SOURCE_ADVISOR_SHA:
        raise ValueError("source run advisor SHA is not the frozen production advisor")
    if manifest.get("dataset_content_identity") != forest10.inspect().get(
        "dataset_content_identity"
    ):
        raise ValueError("source run dataset identity does not match audited Forest10")
    actual = {}
    for name, expected in EXPECTED_ARTIFACTS.items():
        digest = _artifact_digest(paths[name])
        if digest != expected:
            raise ValueError(f"frozen Forest10 {name} digest {digest!r} does not match {expected}")
        actual[name] = digest
    if read_json(paths["search_result"])["final_ordered_candidate_ids"] != list(
        EXPECTED_MEMBERSHIP
    ):
        raise ValueError("Forest10 source SearchResult membership is not canonical K=8")
    recommendation = read_json(paths["recommendation"])
    if recommendation.get("selected_candidate_ids") != list(EXPECTED_MEMBERSHIP):
        raise ValueError("Forest10 Recommendation membership is not canonical K=8")
    if recommendation.get("decision") != "propose-change":
        raise ValueError("Forest10 source Recommendation is not a deployment Recommendation")
    precedence = precedence_check(
        read_json(paths["search_result"]),
        read_json(paths["singleton_profile"]),
        recommendation,
        expected_membership=EXPECTED_MEMBERSHIP,
    )

    # Use the production artifact APIs for snapshot/truth compatibility.  The
    # source-view token remains historical provenance and is not compared with
    # the new production relation.
    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.snapshot import load_snapshot

    snapshot = load_snapshot(paths["snapshot"])
    ground_truth = load_ground_truth_set(paths["ground_truth"], snapshot)
    audit = read_json(paths["audit"])
    if audit.get("semantic_digest") != EXPECTED_ARTIFACTS["audit"]:
        raise ValueError("Forest10 source audit semantic digest is not canonical")
    audit_rows = {}
    with paths["audit_per_query"].open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            audit_rows[row["query_id"]] = row
    truth_rows = {item.query_id: int(item.cardinality) for item in ground_truth.truths}
    truth_binding = validate_audited_truth_binding(truth_rows, audit_rows)
    return {
        "source_run_id": SOURCE_RUN_ID,
        "source_research_commit_sha": SOURCE_RESEARCH_SHA,
        "source_advisor_commit_sha": SOURCE_ADVISOR_SHA,
        "source_artifacts": actual,
        "dataset_content_identity": manifest["dataset_content_identity"],
        "workload_id": manifest["workload_id"],
        "workload_sha256": manifest["workload_sha256"],
        "truth_binding": {
            "snapshot_semantic_digest": ground_truth.source_snapshot_semantic_digest,
            "ground_truth_semantic_digest": ground_truth.semantic_digest,
            **truth_binding,
            "source_view_token_not_reused": True,
        },
        "precedence": precedence,
    }


def _audit_sandbox_records(paths: dict[str, Path]) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    with paths["audit_per_query"].open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            result[row["query_id"]] = {
                "baseline_estimate": int(row["baseline_estimated_rows"]),
                "final_estimate": int(row["final_estimated_rows"]),
                "baseline_qerror": float(row["baseline_qerror"]),
                "final_qerror": float(row["final_qerror"]),
            }
    if len(result) != forest10.EXPECTED_TEST_QUERIES:
        raise ValueError("Forest10 source audit does not contain exactly 10,000 sandbox rows")
    return result


def validate_audited_truth_binding(
    truth_rows: dict[str, int], audit_rows: dict[str, dict[str, Any]]
) -> dict[str, int]:
    """Require every immutable GroundTruthSet label to match the source audit."""
    if len(truth_rows) != forest10.EXPECTED_TEST_QUERIES:
        raise ValueError("Forest10 source GroundTruthSet does not contain 10,000 truths")
    if set(truth_rows) != set(audit_rows):
        raise ValueError("Forest10 source truth/audit query IDs do not bind exactly")
    mismatched = [
        query_id
        for query_id, cardinality in truth_rows.items()
        if cardinality != int(audit_rows[query_id]["true_rows"])
    ]
    if mismatched:
        raise ValueError(f"Forest10 source truth/audit labels differ: {mismatched[:3]}")
    return {"query_count": len(truth_rows), "matched": len(truth_rows), "mismatched": 0}


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


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RuntimeError(f"command failed: {command[0]} ({completed.stderr[-2000:]})")


def _decision(summary: dict[str, Any]) -> str:
    p0 = summary["p0"]["weighted_objective"]
    p1 = summary["p1"]["weighted_objective"]
    p2 = summary["p2"]["weighted_objective"]
    if p1 < p0 and p1 < p2:
        return "forest-k8-transfer-success"
    if p1 == p2:
        return "forest-k8-transfer-neutral"
    if p1 > p2:
        return "forest-k8-transfer-regression"
    return "forest-k8-transfer-neutral"


def run_forest_data_transfer(
    source_run: Path,
    production_dsn: str,
    *,
    output_directory: Path | None = None,
    advisor_command: str = "extstats-advisor",
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run the Forest10 P0/P1/P2 transfer on fresh stock PostgreSQL."""
    if not production_dsn:
        raise ValueError("production DSN is required and is never written to artifacts")
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    systems = verify_frozen_systems(
        Path("/home/wqts/projects/extstats-advisor"),
        Path("/home/wqts/projects/postgresql-src-pgextadv"),
    )
    if systems["advisor_commit_sha"] != FROZEN_ADVISOR_SHA:
        raise ValueError("execution advisor is not the frozen production advisor")
    if systems["patched_postgres_commit_sha"] != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("historical patched PostgreSQL identity drifted")
    source = validate_forest_sources(source_run)
    paths = _paths(Path(source_run))
    output = output_directory or research_root / "experiments/arecel-forest10/full-data-transfer-k8"
    output.mkdir(parents=True, exist_ok=True)
    truth = read_json(paths["ground_truth"])["truths"]
    workload = read_json(paths["workload"])["queries"]
    timings: dict[str, float] = {}

    started = time.monotonic()
    load = load_forest10(
        production_dsn, data_root=data_root, reset_disposable=True, statistics_target=100
    )
    timings["fresh_load_initial_analyze"] = round(time.monotonic() - started, 6)
    if load["rows"] != forest10.EXPECTED_ROWS or load["statistics_target"] != 100:
        raise ValueError("fresh Forest10 load contract failed")

    import psycopg

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        pre_stats = _stats(connection, "forest10")
        if pre_stats:
            raise ValueError(f"fresh Forest10 relation has extended statistics: {pre_stats}")
        ordinary_p0 = _ordinary_fingerprint(connection, "forest10")
        p0 = _explain_rows(connection, workload)
    timings["p0_explain"] = round(time.monotonic() - started, 6)

    deployment_path = output / "deployment-result-v1.json"
    started = time.monotonic()
    _run(_deployment_command(advisor_command, paths, production_dsn, deployment_path))
    _run(_validate_deployment_command(advisor_command, paths, deployment_path))
    timings["deployment"] = round(time.monotonic() - started, 6)
    deployment = read_json(deployment_path)
    reject_credentials(deployment)
    if deployment.get("deployment_policy") != DEPLOYMENT_POLICY:
        raise ValueError("DeploymentResult does not prove the add-only policy")
    if deployment.get("commit_status") != "committed" or not deployment.get("post_commit_verified"):
        raise ValueError("DeploymentResult is not committed and post-commit verified")
    deployed_objects = deployment.get("deployed_objects", [])
    if len(deployed_objects) != len(EXPECTED_MEMBERSHIP):
        raise ValueError("Forest10 deployment did not commit exactly eight objects")
    if deployment.get("deployment_ordered_candidate_ids") != list(EXPECTED_MEMBERSHIP):
        raise ValueError("DeploymentResult order differs from canonical Recommendation")

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        managed_stats = _stats(connection, "forest10")
        if len(managed_stats) != 8:
            raise ValueError("Forest10 deployment did not leave exactly eight statistics")
        names = [item["name"] for item in deployed_objects]
        if [item["name"] for item in managed_stats] != names:
            raise ValueError("physical Forest10 OID order differs from Recommendation order")
        if any(
            item["kind"] != "postgresql.mcv"
            or item["target"] != 100
            or not item["payload"]
            or item["schema"] != "public"
            for item in managed_stats
        ):
            raise ValueError("Forest10 deployed statistics kind, target, or payload is invalid")
        deployed_by_name = {item["name"]: item for item in deployed_objects}
        for physical in managed_stats:
            deployed = deployed_by_name[physical["name"]]
            if physical["kind"] != deployed["kind"] or physical["columns"] != " ".join(
                str(value) for value in deployed["column_ordinals"]
            ):
                raise ValueError("physical Forest10 statistics definition differs from result")
        p1 = _explain_rows(connection, workload)
        ordinary_p1 = _ordinary_fingerprint(connection, "forest10")
    timings["p1_explain"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        connection.execute("BEGIN")
        try:
            _drop_managed(connection, names)
            if _stats(connection, "forest10"):
                raise ValueError("P2 did not drop exactly the eight advisor-managed objects")
            ordinary_p2 = _ordinary_fingerprint(connection, "forest10")
            p2 = _explain_rows(connection, workload)
        finally:
            connection.execute("ROLLBACK")
    timings["p2_control_and_rollback"] = round(time.monotonic() - started, 6)

    started = time.monotonic()
    with psycopg.connect(production_dsn, autocommit=True) as connection:
        restored_stats = _stats(connection, "forest10")
        restored_fingerprint = _ordinary_fingerprint(connection, "forest10")
    if restored_stats != managed_stats:
        raise ValueError("P2 rollback did not restore exact managed statistics metadata")
    if restored_fingerprint != ordinary_p1 or ordinary_p2 != ordinary_p1:
        raise ValueError("P2 ordinary statistics fingerprint was not identical to P1")
    timings["rollback_restoration_verification"] = round(time.monotonic() - started, 6)

    sandbox = _audit_sandbox_records(paths)
    estimates = {
        query_id: {"p0": p0[query_id], "p1": p1[query_id], "p2": p2[query_id]} for query_id in p0
    }
    records = build_transfer_records(truth, sandbox, estimates)
    for row in records:
        row["weight"] = 1.0
    summary = summarize_transfer(records)
    summary["primary_research_decision"] = _decision(summary)
    summary["p0_to_p1_mean_direction"] = (
        "improved"
        if summary["p1"]["weighted_objective"] < summary["p0"]["weighted_objective"]
        else "worsened"
        if summary["p1"]["weighted_objective"] > summary["p0"]["weighted_objective"]
        else "unchanged"
    )
    summary["p2_to_p1_mean_direction"] = (
        "improved"
        if summary["p1"]["weighted_objective"] < summary["p2"]["weighted_objective"]
        else "worsened"
        if summary["p1"]["weighted_objective"] > summary["p2"]["weighted_objective"]
        else "unchanged"
    )
    summary["source_audit_tail_query"] = "arecel_forest10_test_003110"
    timings["artifact_analysis_write"] = 0.0
    artifact = {
        "format_version": FORMAT_VERSION,
        "source": source,
        "source_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "research_commit_sha": SOURCE_RESEARCH_SHA,
            "advisor_repository": "1951123/extstats-advisor",
            "advisor_commit_sha": SOURCE_ADVISOR_SHA,
            "patched_postgres_repository": "1951123/postgresql-pgextadv",
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        },
        "transfer_implementation_research_commit_sha": research_identity["research_commit_sha"],
        "execution_system": {
            **research_identity,
            **systems,
            "stock_postgres_version": load["server_version"],
        },
        "dataset": {
            "benchmark_id": forest10.BENCHMARK_ID,
            "content_identity": source["dataset_content_identity"],
            "rows": load["rows"],
            "schema_contract_id": load["schema_contract_id"],
            "source_csv_sha256": load["source_csv_sha256"],
            "server_version": load["server_version"],
        },
        "recommendation_semantic_digest": EXPECTED_ARTIFACTS["recommendation"],
        "deployment_result_semantic_digest": deployment["semantic_digest"],
        "deployment_policy": DEPLOYMENT_POLICY,
        "deployment_result": {
            "path": "deployment-result-v1.json",
            "semantic_digest": deployment["semantic_digest"],
            "object_count": len(deployed_objects),
            "commit_status": deployment["commit_status"],
            "post_commit_verified": deployment["post_commit_verified"],
        },
        "pre_deployment_extended_statistics": pre_stats,
        "managed_statistics": managed_stats,
        "restored_statistics": restored_stats,
        "ordinary_stats_fingerprint_p0": ordinary_p0,
        "ordinary_stats_fingerprint_p1": ordinary_p1,
        "ordinary_stats_fingerprint_p2": ordinary_p2,
        "ordinary_stats_p1_p2_equal": ordinary_p1 == ordinary_p2,
        "summary": summary,
        "timings_seconds": timings,
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "credentials_recorded": False,
    }
    started = time.monotonic()
    artifact["semantic_digest"] = semantic_digest(artifact)
    reject_credentials(artifact)
    write_json(output / "full-data-transfer-v1.json", artifact)
    with (output / "per-query-transfer-v1.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    timings["artifact_analysis_write"] = round(time.monotonic() - started, 6)
    artifact.pop("semantic_digest", None)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output / "full-data-transfer-v1.json", artifact)
    return {
        "output_directory": str(output),
        "recommendation": EXPECTED_ARTIFACTS["recommendation"],
        "deployment": deployment["semantic_digest"],
        "query_count": len(records),
        "decision": summary["primary_research_decision"],
    }
