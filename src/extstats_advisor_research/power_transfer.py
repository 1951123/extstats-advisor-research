"""Power7 full-data deployment-effect transfer experiment."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import (
    FROZEN_ADVISOR_REPOSITORY,
    FROZEN_ADVISOR_SHA,
    FROZEN_RESEARCH_REPOSITORY,
    PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA,
)
from .analysis.audit import contribution_summary
from .datasets import power7
from .full_data_transfer import (
    DEPLOYMENT_POLICY,
    build_transfer_records,
    precedence_check,
    summarize_transfer,
)
from .pins import verify_git_sha, verify_research_repository
from .postgres.loader import load_power7
from .provenance import read_json, reject_credentials, semantic_digest, write_json
from .transfer_engine import (
    read_sandbox_records,
    run_transfer_phases,
    validate_audited_truth_binding,
)

FORMAT_VERSION = "arecel-power7-full-data-transfer-k8-v1"
SOURCE_RUN_ID = "35d6bb57749bf15c84e92300"
SOURCE_RESEARCH_SHA = "6aaeb0ff2819ae705063305c6bd19be4639a6ce7"
SOURCE_ADVISOR_SHA = PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA
EXPECTED_ARTIFACTS = {
    "snapshot": "5690d69b5b7359bf3e7ca41044cc73d3fe8cbe096898424bce4d91fcd724cac8",
    "ground_truth": "f76aac10840580ba8b4864c30961eb165b7f5008838af1c9236f0f9d9ed0020c",
    "candidate_universe": "1d06d4ca8ac6fd843fd174eb180b7cc4872c13294052fe9e52eac85310245c23",
    "native_repository": "5d8272a4bf4701256d1bb13231642934ef7f8548d934a983a3e6d8e9c1da24fe",
    "singleton_profile": "0be5c93e355299172cfb75efee330e60ddf00fac4a32a4c427c8e52223b4489d",
    "optimization_plan": "8e035728668ef862e2e570e8a0ad46b84a758db770e0cfb1eb573eef552569ec",
    "search_result": "e4f25a3b9ca20645dd1490b1528fee58722f1d11eb7c111ea4a24aa7da4422de",
    "recommendation": "9521461ec7775acff1ead96bbc985da6f2c6f40ac301723737b32fa47f634b11",
    "audit": "70970e9a14cef5a076273e6d94efa9ce699faff9f62d660cfba96c473638c141",
    "full_data_target100": "14c180633e5fabdfeb7803d218963d32ed304f7615e01df9d250d0b23953f507",
}
SOURCE_COMPACT_DIGEST = "01fc5e780118e9831547aa8be8d7e068996028f5fe0f78781e97500436877abf"
EXPECTED_MEMBERSHIP = (
    "cand_118e05e5fce1cc567c922f80",
    "cand_5fdb71ebba54bfa84fab9ea1",
    "cand_2015e764087bec455e8ae806",
    "cand_36cd43aecd98a9766ae7c4bc",
    "cand_3bf3dfda2e9adf9eaa8fc28d",
    "cand_6ca1b76d1f3f368ff4604b39",
    "cand_5f9cbe132b4bd75b51704d91",
)
ACCEPTED_MOVE_ORDER = (
    "cand_118e05e5fce1cc567c922f80",
    "cand_5fdb71ebba54bfa84fab9ea1",
    "cand_2015e764087bec455e8ae806",
    "cand_3bf3dfda2e9adf9eaa8fc28d",
    "cand_6ca1b76d1f3f368ff4604b39",
    "cand_36cd43aecd98a9766ae7c4bc",
    "cand_5f9cbe132b4bd75b51704d91",
)
EXPECTED_OBJECT_ORDER = EXPECTED_MEMBERSHIP


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
    rows = {}
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            rows[row["query_id"]] = row
    return rows


def validate_power_sources(
    source_run: Path, *, research_root: Path | None = None
) -> dict[str, Any]:
    """Validate the immutable Power7 source chain and audited labels."""
    source_run = Path(source_run)
    paths = _paths(source_run)
    manifest = read_json(source_run / "manifest.json")
    if source_run.name != SOURCE_RUN_ID or manifest.get("run_id") != SOURCE_RUN_ID:
        raise ValueError("Power7 transfer must use the frozen canonical source RunID")
    if manifest.get("benchmark_id") != power7.BENCHMARK_ID:
        raise ValueError("source run is not the canonical Power7 run")
    if manifest.get("research_commit_sha") != SOURCE_RESEARCH_SHA:
        raise ValueError("source run was not produced by the frozen Power7 research revision")
    if manifest.get("advisor_commit_sha") != SOURCE_ADVISOR_SHA:
        raise ValueError("source run advisor SHA is not the frozen production advisor")
    if manifest.get("dataset_content_identity") != power7.inspect().get("dataset_content_identity"):
        raise ValueError("source run dataset identity does not match audited Power7")
    if manifest.get("row_count") != power7.EXPECTED_ROWS:
        raise ValueError("source run row count does not match audited Power7")

    actual = {}
    for name, expected in EXPECTED_ARTIFACTS.items():
        digest = _artifact_digest(paths[name])
        if digest != expected:
            raise ValueError(f"frozen Power7 {name} digest {digest!r} does not match {expected}")
        actual[name] = digest

    search = read_json(paths["search_result"])
    recommendation = read_json(paths["recommendation"])
    if search.get("final_ordered_candidate_ids") != list(EXPECTED_MEMBERSHIP):
        raise ValueError("Power7 SearchResult membership is not frozen K=8")
    if (
        tuple(item["added_candidate_id"] for item in search["accepted_moves"])
        != ACCEPTED_MOVE_ORDER
    ):
        raise ValueError("Power7 accepted move order is not the frozen K=8 trace")
    if recommendation.get("selected_candidate_ids") != list(EXPECTED_MEMBERSHIP):
        raise ValueError("Power7 Recommendation membership is not frozen K=8")
    if recommendation.get("deployment_ordered_candidate_ids") != list(EXPECTED_OBJECT_ORDER):
        raise ValueError("Power7 Recommendation order is not frozen precedence order")
    if recommendation.get("decision") != "propose-change":
        raise ValueError("Power7 source Recommendation is not a deployment Recommendation")
    if any(
        item.get("kind") != "postgresql.mcv" or item.get("statistics_target") != 100
        for item in recommendation["selected_candidates"]
    ):
        raise ValueError("Power7 Recommendation contains a non-MCV or non-target-100 object")
    precedence = precedence_check(
        search,
        read_json(paths["singleton_profile"]),
        recommendation,
        expected_membership=EXPECTED_MEMBERSHIP,
    )

    from extstats_advisor.ground_truth import load_ground_truth_set
    from extstats_advisor.snapshot import load_snapshot

    snapshot = load_snapshot(paths["snapshot"])
    ground_truth = load_ground_truth_set(paths["ground_truth"], snapshot)
    ground_truth_json = read_json(paths["ground_truth"])
    if ground_truth_json.get("source", {}).get("kind") != "production-exact-execution":
        raise ValueError("Power7 GroundTruthSet source kind is not production-exact-execution")
    if len(ground_truth.truths) != power7.EXPECTED_TEST_QUERIES:
        raise ValueError("Power7 GroundTruthSet does not contain exactly 10,000 queries")
    audit = read_json(paths["audit"])
    if audit.get("semantic_digest") != EXPECTED_ARTIFACTS["audit"]:
        raise ValueError("Power7 source audit semantic digest is not canonical")
    audit_rows = _audit_rows(paths["audit_per_query"])
    truth_rows = {item.query_id: int(item.cardinality) for item in ground_truth.truths}
    truth_binding = validate_audited_truth_binding(
        truth_rows, audit_rows, expected_count=power7.EXPECTED_TEST_QUERIES
    )
    compact_path = (research_root or Path(__file__).resolve().parents[2]) / (
        "experiments/arecel-power7/canonical-k8/canonical-k8-summary-v1.json"
    )
    compact = read_json(compact_path)
    compact_for_digest = {
        key: value
        for key, value in compact.items()
        if key not in {"semantic_digest", "stage_timings"}
    }
    if semantic_digest(compact_for_digest) != SOURCE_COMPACT_DIGEST:
        raise ValueError("Power7 canonical compact source digest is not canonical")
    return {
        "source_run_id": SOURCE_RUN_ID,
        "source_research_commit_sha": SOURCE_RESEARCH_SHA,
        "source_advisor_commit_sha": SOURCE_ADVISOR_SHA,
        "source_artifacts": actual,
        "source_compact_digest": SOURCE_COMPACT_DIGEST,
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
            paths["audit_per_query"], expected_count=power7.EXPECTED_TEST_QUERIES
        ),
        "compact": compact,
    }


def _decision(summary: dict[str, Any]) -> str:
    p0 = summary["p0"]["weighted_objective"]
    p1 = summary["p1"]["weighted_objective"]
    p2 = summary["p2"]["weighted_objective"]
    if p1 < p0 and p1 < p2:
        return "power-k8-transfer-success"
    if p1 == p2:
        return "power-k8-transfer-neutral"
    if p1 > p2:
        return "power-k8-transfer-regression"
    return "power-k8-transfer-neutral"


def _tail_queries(records: list[dict[str, Any]], compact: dict[str, Any]) -> list[str]:
    ids = {
        row["query_id"]
        for row in sorted(records, key=lambda row: row["sandbox_baseline_qerror"], reverse=True)[
            :10
        ]
    }
    ids.update(
        row["query_id"]
        for row in sorted(records, key=lambda row: row["p0_qerror"], reverse=True)[:10]
    )
    ids.update(
        row["query_id"]
        for row in compact["baseline_correspondence"]["top_10_full_data_target100_queries"]
    )
    ids.update(
        row["query_id"]
        for row in compact.get("audit", {})
        .get("tail_queries", {})
        .get("top_10_largest_absolute_improvements", [])
    )
    ids.update(
        {"arecel_power7_test_005495", "arecel_power7_test_001932", "arecel_power7_test_001642"}
    )
    return sorted(ids)


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


def run_power_data_transfer(
    source_run: Path,
    production_dsn: str,
    *,
    output_directory: Path | None = None,
    advisor_command: str = "extstats-advisor",
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Validate the frozen K=8 Recommendation against stock PostgreSQL."""
    if not production_dsn:
        raise ValueError("production DSN is required and is never written to artifacts")
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    execution_advisor_sha = verify_git_sha(advisor_root, FROZEN_ADVISOR_SHA)
    source = validate_power_sources(source_run, research_root=research_root)
    paths = _paths(Path(source_run))
    output = output_directory or research_root / "experiments/arecel-power7/full-data-transfer-k8"
    output.mkdir(parents=True, exist_ok=True)
    started_total = time.monotonic()
    load = load_power7(
        production_dsn, data_root=data_root, reset_disposable=True, statistics_target=100
    )
    if (
        load["rows"] != power7.EXPECTED_ROWS
        or load["statistics_target"] != 100
        or load["analyze_count"] != 1
        or load["physical_extended_statistics_count"] != 0
    ):
        raise ValueError("fresh Power7 load contract failed")

    phases = run_transfer_phases(
        production_dsn=production_dsn,
        source_paths=paths,
        workload=source["workload"],
        relation="power7",
        load=load,
        expected_order=EXPECTED_OBJECT_ORDER,
        output_directory=output,
        advisor_command=advisor_command,
    )
    deployment = phases["deployment"]
    reject_credentials(deployment)
    records = build_transfer_records(
        source["truth"],
        source["sandbox"],
        {
            query_id: {
                "p0": phases["p0"][query_id],
                "p1": phases["p1"][query_id],
                "p2": phases["p2"][query_id],
            }
            for query_id in phases["p0"]
        },
    )
    for row in records:
        row["weight"] = 1.0
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
    summary["transfer_retention_ratio"] = (
        summary["controlled_production_relative_improvement"]
        / summary["sandbox_relative_improvement"]
    )
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
    summary["mean_direction_conflict"] = (
        summary["p0_to_p1_mean_direction"] != summary["p2_to_p1_mean_direction"]
    )
    summary["tail_contributions"] = _tail_contributions(records)
    summary["tail_union_query_ids"] = _tail_queries(records, source["compact"])
    records_by_id = {record["query_id"]: record for record in records}
    summary["tail_query_records"] = [
        records_by_id[query_id] for query_id in summary["tail_union_query_ids"]
    ]
    first_five = read_json(paths["search_result"])["accepted_moves"][:5]
    total_improvement = float(read_json(paths["search_result"])["improvement"])
    summary["first_five_sandbox_improvement_fraction"] = (
        sum(float(item["improvement"]) for item in first_five) / total_improvement
    )
    summary["source_top_10_baseline_qerror_queries"] = summary["top_10_sandbox_baseline"]

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
            "truth_binding": source["truth_binding"],
            "precedence": source["precedence"],
        },
        "source_system": {
            "research_repository": FROZEN_RESEARCH_REPOSITORY,
            "research_commit_sha": SOURCE_RESEARCH_SHA,
            "advisor_repository": FROZEN_ADVISOR_REPOSITORY,
            "advisor_commit_sha": SOURCE_ADVISOR_SHA,
            "patched_postgres_repository": source["compact"].get("patched_postgres_repository"),
            "patched_postgres_commit_sha": source["compact"].get("patched_postgres_commit_sha"),
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
            "benchmark_id": power7.BENCHMARK_ID,
            "content_identity": source["dataset_content_identity"],
            "rows": load["rows"],
            "schema_contract_id": load["schema_contract_id"],
            "source_csv_sha256": load["source_csv_sha256"],
            "source_hashes": {
                "csv": power7.CSV_SHA256,
                "workload_pickle": power7.WORKLOAD_PICKLE_SHA256,
                "label_pickle": power7.LABEL_PICKLE_SHA256,
                "canonical_workload": power7.CANONICAL_WORKLOAD_SHA256,
            },
        },
        "recommendation": {
            "semantic_digest": EXPECTED_ARTIFACTS["recommendation"],
            "selected_candidate_ids": list(EXPECTED_MEMBERSHIP),
            "deployment_order": list(EXPECTED_OBJECT_ORDER),
            "object_count": len(EXPECTED_OBJECT_ORDER),
            "accepted_move_order": list(ACCEPTED_MOVE_ORDER),
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
        "ordinary_stats_p1_p2_equal": phases["ordinary_stats_p1_p2_equal"],
        "p2_control": {
            "dropped_object_count": len(EXPECTED_OBJECT_ORDER),
            "analyze_performed": False,
            "rollback_performed": True,
            "restoration_verified": phases["restored_stats"] == phases["managed_stats"],
        },
        "summary": summary,
        "timings_seconds": {
            "fresh_load_initial_analyze": load["elapsed_seconds"],
            **phases["timings"],
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
