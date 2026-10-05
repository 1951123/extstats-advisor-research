"""DMV11 full-data transfer validation for one frozen K=8 Recommendation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import FROZEN_ADVISOR_REPOSITORY, FROZEN_ADVISOR_SHA, FROZEN_RESEARCH_REPOSITORY
from .analysis.audit import contribution_summary
from .datasets import dmv11
from .full_data_transfer import (
    DEPLOYMENT_POLICY,
    build_transfer_records,
    precedence_check,
    summarize_transfer,
)
from .pins import verify_git_sha, verify_research_repository
from .postgres.loader import load_dmv11
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .transfer_engine import (
    read_sandbox_records,
    run_transfer_phases,
    validate_audited_truth_binding,
)

FORMAT_VERSION = "arecel-dmv11-full-data-transfer-k8-v1"
SOURCE_RUN_ID = "82f277385bf5381a43b0c268"
SOURCE_RESEARCH_SHA = "dd613f7357d436c69412a9bd7461a4afdc46eca3"
SOURCE_ADVISOR_SHA = FROZEN_ADVISOR_SHA
SOURCE_COMPACT_DIGEST = "3b49b2b4262a438ce32b1835c57a0117fe3b39caed6d07532d8bb68acdfcadda"
SOURCE_TRUTH_SHA256 = "aaedaf54313926fb03efb1051ba58c86b17729cad372a69e9c6faf5b192eb37a"
EXPECTED_ACCEPTED_MOVE_ORDER = (
    "cand_57f08ef654098d51d97f0400",
    "cand_7f3ef384db817c3691b4ca88",
    "cand_df363eb816815cdff7fdd419",
    "cand_67e6aa143586118096224da8",
    "cand_3c437e0f5567c912c876e9ea",
)
EXPECTED_OBJECT_ORDER = (
    "cand_57f08ef654098d51d97f0400",
    "cand_7f3ef384db817c3691b4ca88",
    "cand_df363eb816815cdff7fdd419",
    "cand_3c437e0f5567c912c876e9ea",
    "cand_67e6aa143586118096224da8",
)
EXPECTED_ARTIFACTS = {
    "snapshot": "9687d42c311226678eeddb653a80fb769604656f82820a76e2d844eb2310247d",
    "ground_truth": "6096a1e8435a7a0a471a214955ded71629555be87652e0d26d67c443971eea44",
    "candidate_universe": "deba616def6389e1086f7180ebbe0af8532016ace543daba0454f611eacec2d4",
    "native_repository": "4c90d36d58bfdc2b24476a64d8be0db43b2be0d35d6f806a3b89d31f6cb65ad0",
    "singleton_profile": "f7e949a25e3ae2d8281d3021388a9ba3a236d5be62694661211401396c74a3e3",
    "optimization_plan": "dcac1413c834bb9c4a243ed20e376eac07f6f587c8e847343b14f84bff076253",
    "search_result": "fe604d0e7103158dfe1a0308545ac811634e22bec48521729e14a81600521eab",
    "recommendation": "6bd0eb2a3c89bdc4fb0bca7319c1e40fc86cb323028eb1ff6e7a13e18372c8e2",
    "audit": "1fc7dbd2d574584d1479641a77d05830911721c854931c6e5c478ada4482b5f6",
    "full_data_target100": "19fefaff941d257593efbac16b0f08c7e3851c00fd087688f0f62c0b9785019d",
}


def resolve_dmv_source_run(source_run: Path) -> Path:
    """Resolve the canonical run from either ``runs/<id>`` or its nested path."""
    source_run = Path(source_run)
    if (source_run / "manifest.json").is_file():
        return source_run
    candidates = [
        item
        for item in source_run.parent.glob(f"*/{source_run.name}")
        if (item / "manifest.json").is_file()
    ]
    if len(candidates) == 1:
        return candidates[0]
    raise FileNotFoundError(
        f"could not resolve DMV11 source RunID {source_run.name!r} below {source_run.parent}"
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
        "full_data_target100": source_run / "full-data-target100-v1.json",
    }


def _artifact_digest(path: Path) -> str:
    value = read_json(path / "manifest.json") if path.is_dir() else read_json(path)
    return str(value.get("semantic_digest"))


def _audit_rows(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8") as stream:
        return {row["query_id"]: row for row in map(json.loads, stream)}


def _validate_external_truth_source(source: dict[str, Any]) -> dict[str, Any]:
    """Require the exact DMV11 external truth provenance and no DB identity."""
    expected = {
        "kind": "authoritative-external-exact",
        "authority": dmv11.AUTHORITATIVE_TRUTH_AUTHORITY,
        "dataset_identity": dmv11.AUTHORITATIVE_TRUTH_DATASET_IDENTITY,
        "source_revision": dmv11.AUTHORITATIVE_TRUTH_SOURCE_REVISION,
        "source_artifact_sha256": SOURCE_TRUTH_SHA256,
    }
    if any(source.get(key) != value for key, value in expected.items()):
        raise ValueError(f"DMV11 GroundTruthSet source provenance is not frozen: {source!r}")
    for key in ("dbms", "server_version", "server_version_num", "source_view_token"):
        if source.get(key) is not None:
            raise ValueError(f"DMV11 external GroundTruthSet unexpectedly contains {key}")
    return expected


def validate_dmv_sources(source_run: Path, *, research_root: Path | None = None) -> dict[str, Any]:
    """Validate the immutable DMV11 source chain without executing exact counts."""
    source_run = resolve_dmv_source_run(source_run)
    paths = _paths(source_run)
    manifest = read_json(source_run / "manifest.json")
    if source_run.name != SOURCE_RUN_ID or manifest.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("DMV11 transfer must use the frozen canonical source RunID")
    if manifest.get("benchmark_id") != dmv11.BENCHMARK_ID:
        raise ValueError("source run is not the canonical DMV11 run")
    if manifest.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("source run was not produced by the frozen DMV11 research revision")
    if manifest.get("advisor_commit_sha") != SOURCE_ADVISOR_SHA:
        raise ValueError("source run advisor SHA is not the frozen production advisor")
    if manifest.get("dataset_content_identity") != dmv11.AUTHORITATIVE_TRUTH_DATASET_IDENTITY:
        raise ValueError("source run dataset identity does not match audited DMV11")
    if manifest.get("row_count") != dmv11.EXPECTED_ROWS:
        raise ValueError("source run row count does not match audited DMV11")

    actual = {}
    for name, expected in EXPECTED_ARTIFACTS.items():
        digest = _artifact_digest(paths[name])
        if digest != expected:
            raise ValueError(f"frozen DMV11 {name} digest {digest!r} does not match {expected}")
        actual[name] = digest

    search = read_json(paths["search_result"])
    recommendation = read_json(paths["recommendation"])
    if search.get("final_ordered_candidate_ids") != list(EXPECTED_OBJECT_ORDER):
        raise ValueError("DMV11 SearchResult membership is not frozen K=8")
    if (
        tuple(item["added_candidate_id"] for item in search["accepted_moves"])
        != EXPECTED_ACCEPTED_MOVE_ORDER
    ):
        raise ValueError("DMV11 accepted move order is not the frozen K=8 trace")
    if recommendation.get("selected_candidate_ids") != list(EXPECTED_OBJECT_ORDER):
        raise ValueError("DMV11 Recommendation membership is not frozen K=8")
    if recommendation.get("deployment_ordered_candidate_ids") != list(EXPECTED_OBJECT_ORDER):
        raise ValueError("DMV11 Recommendation precedence is not frozen")
    if recommendation.get("decision") != "propose-change":
        raise ValueError("DMV11 source Recommendation is not a deployment Recommendation")
    if any(
        item.get("kind") != "postgresql.mcv" or item.get("statistics_target") != 100
        for item in recommendation["selected_candidates"]
    ):
        raise ValueError("DMV11 Recommendation contains a non-MCV or non-target-100 object")
    precedence = precedence_check(
        search,
        read_json(paths["singleton_profile"]),
        recommendation,
        expected_membership=EXPECTED_OBJECT_ORDER,
    )

    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.snapshot import load_snapshot

    snapshot = load_snapshot(paths["snapshot"])
    ground_truth = load_ground_truth_set(paths["ground_truth"], snapshot)
    ground_truth_json = read_json(paths["ground_truth"])
    source = ground_truth_json.get("source", {})
    expected_source = _validate_external_truth_source(source)
    for key in ("dbms", "server_version", "server_version_num", "source_view_token"):
        if source.get(key) is not None or ground_truth_json.get(key) is not None:
            raise ValueError(f"DMV11 external GroundTruthSet unexpectedly contains {key}")
    if len(ground_truth.truths) != dmv11.EXPECTED_TEST_QUERIES:
        raise ValueError("DMV11 GroundTruthSet does not contain exactly 10,000 queries")

    audit = read_json(paths["audit"])
    if audit.get("semantic_digest") != EXPECTED_ARTIFACTS["audit"]:
        raise ValueError("DMV11 source audit semantic digest is not canonical")
    truth_rows = {item.query_id: int(item.cardinality) for item in ground_truth.truths}
    truth_binding = validate_audited_truth_binding(
        truth_rows,
        _audit_rows(paths["audit_per_query"]),
        expected_count=dmv11.EXPECTED_TEST_QUERIES,
    )

    compact_path = (research_root or Path(__file__).resolve().parents[2]) / (
        "experiments/arecel-dmv11/canonical-k8/canonical-k8-summary-v1.json"
    )
    compact = read_json(compact_path)
    compact_for_digest = {
        key: value
        for key, value in compact.items()
        if key not in {"semantic_digest", "stage_timings"}
    }
    if semantic_digest(compact_for_digest) != SOURCE_COMPACT_DIGEST:
        raise ValueError("DMV11 canonical compact source digest is not canonical")
    return {
        "source_run_id": SOURCE_RUN_ID,
        "source_research_commit_sha": SOURCE_RESEARCH_SHA,
        "source_advisor_commit_sha": SOURCE_ADVISOR_SHA,
        "source_artifacts": actual,
        "source_compact_digest": SOURCE_COMPACT_DIGEST,
        "source_truth_kind": source["kind"],
        "truth_provenance": expected_source,
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
        "truth": read_json(paths["ground_truth"])["truths"],
        "workload": read_json(paths["workload"])["queries"],
        "sandbox": read_sandbox_records(
            paths["audit_per_query"], expected_count=dmv11.EXPECTED_TEST_QUERIES
        ),
        "compact": compact,
        "full_data_target100": read_json(paths["full_data_target100"]),
    }


def _decision(summary: dict[str, Any]) -> str:
    p0 = summary["p0"]["weighted_objective"]
    p1 = summary["p1"]["weighted_objective"]
    p2 = summary["p2"]["weighted_objective"]
    if p1 < p0 and p1 < p2:
        return "dmv-k8-transfer-success"
    if p1 == p2:
        return "dmv-k8-transfer-neutral"
    if p1 > p2:
        return "dmv-k8-transfer-regression"
    return "dmv-k8-transfer-neutral"


def _tail_contributions(records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        state: contribution_summary(records, key)
        for state, key in (
            ("sandbox_baseline", "sandbox_baseline_qerror"),
            ("sandbox_final", "sandbox_final_qerror"),
            ("p0", "p0_qerror"),
            ("p2", "p2_qerror"),
            ("p1", "p1_qerror"),
        )
    }


def _tail_union(records: list[dict[str, Any]], source: dict[str, Any]) -> list[str]:
    ids = set()
    for field in ("sandbox_baseline_qerror", "sandbox_final_qerror", "p0_qerror"):
        ids.update(
            row["query_id"]
            for row in sorted(records, key=lambda item: item[field], reverse=True)[:10]
        )
    source_full = _source_full_tail_map(source)
    ids.update(source_full)
    ids.add("arecel_dmv11_test_009189")
    return sorted(ids)


def _source_full_tail_map(source: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = source["compact"]["baseline_correspondence"]["top_10_full_data_target100_queries"]
    return {
        row["query_id"]: {
            "estimate": int(row["full_data_target100"]["estimate"]),
            "qerror": float(row["full_data_target100"]["qerror"]),
        }
        for row in rows
    }


def _source_tail_values(source: dict[str, Any], query_id: str) -> dict[str, Any] | None:
    return _source_full_tail_map(source).get(query_id)


def run_dmv_data_transfer(
    source_run: Path,
    production_dsn: str,
    *,
    output_directory: Path | None = None,
    advisor_command: str = "extstats-advisor",
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run exactly one DMV11 stock PostgreSQL P0/P1/P2 transfer."""
    if not production_dsn:
        raise ValueError("production DSN is required and is never written to an artifact")
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    execution_advisor_sha = verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA)
    source = validate_dmv_sources(source_run, research_root=research_root)
    paths = _paths(resolve_dmv_source_run(source_run))
    output = output_directory or research_root / "experiments/arecel-dmv11/full-data-transfer-k8"
    output.mkdir(parents=True, exist_ok=True)
    started_total = time.monotonic()

    load_started = time.monotonic()
    load = load_dmv11(
        production_dsn, data_root=data_root, reset_disposable=True, statistics_target=100
    )
    load_timing = round(time.monotonic() - load_started, 6)
    if (
        load["rows"] != dmv11.EXPECTED_ROWS
        or load["statistics_target"] != 100
        or load["analyze_count"] != 1
        or load["physical_extended_statistics_count"] != 0
    ):
        raise ValueError("fresh DMV11 load contract failed")

    phases = run_transfer_phases(
        production_dsn=production_dsn,
        source_paths=paths,
        workload=source["workload"],
        relation="dmv11",
        load=load,
        expected_order=EXPECTED_OBJECT_ORDER,
        output_directory=output,
        advisor_command=advisor_command,
    )
    deployment = phases["deployment"]
    reject_credentials(deployment)
    estimates = {
        query_id: {
            "p0": phases["p0"][query_id],
            "p1": phases["p1"][query_id],
            "p2": phases["p2"][query_id],
        }
        for query_id in phases["p0"]
    }
    records = build_transfer_records(source["truth"], source["sandbox"], estimates)
    for row in records:
        row["weight"] = 1.0
        row["p0_estimate"] = row["p0_estimated_rows"]
        row["p1_estimate"] = row["p1_estimated_rows"]
        row["p2_estimate"] = row["p2_estimated_rows"]
    analysis_started = time.monotonic()
    summary = summarize_transfer(records)
    summary["decision"] = _decision(summary)
    summary["sandbox_relative_improvement"] = (
        summary["sandbox_baseline"]["weighted_objective"]
        - summary["sandbox_final"]["weighted_objective"]
    ) / summary["sandbox_baseline"]["weighted_objective"]
    summary["controlled_production_relative_improvement"] = (
        summary["p2"]["weighted_objective"] - summary["p1"]["weighted_objective"]
    ) / summary["p2"]["weighted_objective"]
    summary["operational_production_relative_improvement"] = (
        summary["p0"]["weighted_objective"] - summary["p1"]["weighted_objective"]
    ) / summary["p0"]["weighted_objective"]
    summary["analyze_drift_relative_change"] = (
        summary["p0"]["weighted_objective"] - summary["p2"]["weighted_objective"]
    ) / summary["p0"]["weighted_objective"]
    summary["transfer_retention_ratio"] = (
        summary["controlled_production_relative_improvement"]
        / summary["sandbox_relative_improvement"]
    )
    summary["tail_contributions"] = _tail_contributions(records)
    summary["tail_union_query_ids"] = _tail_union(records, source)
    records_by_id = {record["query_id"]: record for record in records}
    summary["tail_query_records"] = []
    for query_id in summary["tail_union_query_ids"]:
        record = dict(records_by_id[query_id])
        record["full_data_target100"] = _source_tail_values(source, query_id)
        summary["tail_query_records"].append(record)
    search = read_json(paths["search_result"])
    summary["first_selected_object_share"] = float(
        search["accepted_moves"][0]["improvement"]
    ) / float(search["improvement"])
    summary["first_three_selected_objects_share"] = sum(
        float(item["improvement"]) for item in search["accepted_moves"][:3]
    ) / float(search["improvement"])
    summary["explicit_tail_query"] = records_by_id["arecel_dmv11_test_009189"]
    summary["source_top_10_baseline_qerror_queries"] = summary["top_10_sandbox_baseline"]
    summary["analysis_elapsed_seconds"] = round(time.monotonic() - analysis_started, 6)

    artifact = {
        "format_version": FORMAT_VERSION,
        "source_run_id": SOURCE_RUN_ID,
        "source_provenance": {
            "run_id": source["source_run_id"],
            "research_commit_sha": source["source_research_commit_sha"],
            "advisor_commit_sha": source["source_advisor_commit_sha"],
            "dataset_content_identity": source["dataset_content_identity"],
            "workload_id": source["workload_id"],
            "workload_sha256": source["workload_sha256"],
            "artifacts": source["source_artifacts"],
            "compact_digest": source["source_compact_digest"],
            "truth_kind": source["source_truth_kind"],
            "truth_provenance": source["truth_provenance"],
            "truth_binding": source["truth_binding"],
            "precedence": source["precedence"],
            "exact_truth_recapture": False,
        },
        "source_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "research_commit_sha": SOURCE_RESEARCH_SHA,
            "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
            "advisor_commit_sha": SOURCE_ADVISOR_SHA,
            "patched_postgres_repository": "1951123/postgresql-pgextadv",
            "patched_postgres_commit_sha": "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6",
        },
        "execution_system": {
            **research_identity,
            "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
            "advisor_commit_sha": execution_advisor_sha,
            "stock_postgres_version": load["server_version"],
            "stock_postgres_version_num": load["server_version_num"],
        },
        "transfer_implementation_research_sha": research_identity["research_commit_sha"],
        "dataset": {
            "benchmark_id": dmv11.BENCHMARK_ID,
            "content_identity": source["dataset_content_identity"],
            "rows": load["rows"],
            "schema_contract_id": load["schema_contract_id"],
            "source_csv_sha256": load["source_csv_sha256"],
            "source_hashes": {
                "csv": dmv11.CSV_SHA256,
                "workload_pickle": dmv11.WORKLOAD_PICKLE_SHA256,
                "label_pickle": dmv11.LABEL_PICKLE_SHA256,
                "canonical_workload": dmv11.CANONICAL_WORKLOAD_SHA256,
            },
        },
        "recommendation": {
            "semantic_digest": EXPECTED_ARTIFACTS["recommendation"],
            "selected_candidate_ids": list(EXPECTED_OBJECT_ORDER),
            "deployment_order": list(EXPECTED_OBJECT_ORDER),
            "accepted_move_order": list(EXPECTED_ACCEPTED_MOVE_ORDER),
            "object_count": len(EXPECTED_OBJECT_ORDER),
        },
        "deployment_policy": DEPLOYMENT_POLICY,
        "deployment_result": {
            "path": "deployment-result-v1.json",
            "semantic_digest": deployment["semantic_digest"],
            "commit_status": deployment["commit_status"],
            "post_commit_verified": deployment["post_commit_verified"],
            "object_count": len(deployment["deployed_objects"]),
        },
        "pre_deployment_extended_statistics": phases["pre_stats"],
        "managed_statistics": phases["managed_stats"],
        "restored_statistics": phases["restored_stats"],
        "ordinary_stats_fingerprint_p0": phases["ordinary_stats_fingerprint_p0"],
        "ordinary_stats_fingerprint_p1": phases["ordinary_stats_fingerprint_p1"],
        "ordinary_stats_fingerprint_p2": phases["ordinary_stats_fingerprint_p2"],
        "ordinary_stats_p0_p2_equal": phases["ordinary_stats_fingerprint_p0"]
        == phases["ordinary_stats_fingerprint_p2"],
        "ordinary_stats_p1_p2_equal": phases["ordinary_stats_p1_p2_equal"],
        "p2_control": {
            "dropped_object_count": len(EXPECTED_OBJECT_ORDER),
            "analyze_performed": False,
            "rollback_performed": True,
            "restoration_verified": phases["restored_stats"] == phases["managed_stats"],
            "external_statistics_touched": False,
        },
        "summary": summary,
        "timings_seconds": {
            "fresh_dmv_load_initial_analyze": load_timing,
            **phases["timings"],
            "transfer_analysis_artifact_write": summary["analysis_elapsed_seconds"],
        },
        "live_experiment_elapsed_seconds": round(time.monotonic() - started_total, 6),
        "search_performed": False,
        "patched_postgres_started": False,
        "credentials_recorded": False,
        "qerror_contract": "qerror-cardinality-floor-1-v1",
    }
    reject_credentials(artifact)
    artifact["semantic_digest"] = semantic_digest(artifact)
    write_json(output / "full-data-transfer-v1.json", artifact)
    with (output / "per-query-transfer-v1.jsonl").open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    write_json(output / "full-data-transfer-v1.json", artifact)
    return {
        "output_directory": str(output),
        "deployment": deployment["semantic_digest"],
        "transfer": artifact["semantic_digest"],
        "query_count": len(records),
        "decision": summary["decision"],
    }
