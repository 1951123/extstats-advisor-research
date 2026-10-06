"""Dataset-generic RQ1 matched comparison orchestration.

This module owns experiment orchestration and artifact assembly.  Advisor
semantics remain in the frozen advisor CLI and the existing canonical runner.
Every arm uses the same audited external observation vector; no arm may fall
back to production exact truth.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extstats_advisor.utility import QErrorLoss

from .arecel_truth import authoritative_truth_spec, observation_records
from .datasets import DATASETS
from .full_data_transfer import _explain_rows, _ordinary_fingerprint, _stats
from .pins import verify_frozen_systems, verify_research_repository
from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
from .postgres_lab import _read_identity, reinit_role, role_spec, stop_role
from .provenance import read_json, sha256_file, write_json
from .rq1_canary import (
    ARM_IDS,
    _arm_common,
    _ensure_planner_catalog,
    _semantic_build_identity,
    _session_settings,
    build_rq1_artifact,
)
from .runner import run_census13, run_dmv11, run_forest10, run_power7
from .system_freeze import DEFAULT_SYSTEM_FREEZE_PATH, load_system_freeze

SEED_IDENTIFIER = 123
SETSEED_SQL = "SELECT setseed(1.0 / 123)"
RQ1_DATASETS = tuple(sorted(DATASETS))


@dataclass(frozen=True)
class _DatasetConfig:
    dataset: Any
    loader: Callable[..., dict[str, Any]]
    advisor_runner: Callable[..., dict[str, Any]]
    search_seconds: float


_CONFIGS = {
    "arecel-census13": _DatasetConfig(
        DATASETS["arecel-census13"],
        load_census13,
        run_census13,
        180.0,
    ),
    "arecel-forest10": _DatasetConfig(
        DATASETS["arecel-forest10"],
        load_forest10,
        run_forest10,
        300.0,
    ),
    "arecel-power7": _DatasetConfig(
        DATASETS["arecel-power7"],
        load_power7,
        run_power7,
        300.0,
    ),
    "arecel-dmv11": _DatasetConfig(
        DATASETS["arecel-dmv11"],
        load_dmv11,
        run_dmv11,
        300.0,
    ),
}


def _relation_name(dataset: Any) -> str:
    return dataset.RELATION.rsplit(".", 1)[-1]


def _advisor_json(command: list[str]) -> dict[str, Any]:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"advisor command failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    if not lines or not lines[-1].startswith("{"):
        raise RuntimeError("advisor command did not return a JSON result")
    return json.loads(lines[-1])


def _advisor_command(command: list[str]) -> None:
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode:
        raise RuntimeError(
            f"advisor command failed ({completed.returncode}): {completed.stderr[-2000:]}"
        )


def _external_truth(dataset_id: str, research_root: Path) -> tuple[dict[str, Any], dict[str, int]]:
    spec = authoritative_truth_spec(dataset_id, research_root)
    observations_path = Path(spec["observations_path"])
    values = observation_records(observations_path)
    audit_path = (
        research_root / "truth" / "arecel" / dataset_id.removeprefix("arecel-") / "audit-v1.json"
    )
    audit = read_json(audit_path)
    if audit.get("observation_sha256") != spec["observations_sha256"]:
        raise ValueError(f"{dataset_id} truth audit does not match observations")
    identity = {
        "source_kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        "authority": spec["authority"],
        "dataset_identity": spec["dataset_identity"],
        "source_revision": spec["source_revision"],
        "source_artifact_sha256": spec["observations_sha256"],
        "observations_sha256": spec["observations_sha256"],
        "observations_semantic_digest": audit["observation_semantic_digest"],
        "policy_status": spec["policy_status"],
        "workload_id": f"arecel_{dataset_id.removeprefix('arecel-')}_test_v1",
        "query_count": len(values),
    }
    return identity, values


def _workload(dataset: Any, runtime: Path, data_root: Path | None) -> tuple[dict[str, Any], Path]:
    path = runtime / "workload.json"
    dataset.extract_workload(path, data_root, "test")
    value = read_json(path)
    if len(value.get("queries", [])) != 10_000:
        raise ValueError(f"{dataset.BENCHMARK_ID} workload must contain 10,000 queries")
    return value, path


def _evaluate_stock_arm(
    *,
    dsn: str,
    dataset: Any,
    workload: list[dict[str, Any]],
    truths: dict[str, int],
    raw_path: Path,
    arm_id: str,
) -> tuple[list[dict[str, Any]], dict[str, str], str, list[dict[str, Any]]]:
    import psycopg

    relation = _relation_name(dataset)
    with psycopg.connect(
        dsn, application_name=f"extstats-research-rq1-{arm_id}", autocommit=True
    ) as connection:
        settings = _session_settings(connection)
        estimates = _explain_rows(connection, workload)
        stats = _stats(connection, relation)
        ordinary = _ordinary_fingerprint(connection, relation)
    loss = QErrorLoss()
    records = []
    for query in workload:
        query_id = query["query_id"]
        if query_id not in truths:
            raise ValueError(f"authoritative observations omit {query_id}")
        truth = int(truths[query_id])
        estimate = int(estimates[query_id])
        records.append(
            {
                "query_id": query_id,
                "estimate": estimate,
                "truth": truth,
                "weight": float(query.get("weight", 1.0)),
                "qerror": float(loss.loss(estimate, truth)),
            }
        )
    if set(truths) != {record["query_id"] for record in records}:
        raise ValueError(f"{dataset.BENCHMARK_ID} workload and truth IDs differ")
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    return records, settings, ordinary, stats


def _workload_identity(workload: dict[str, Any], path: Path) -> dict[str, Any]:
    return {
        "workload_id": workload["workload_id"],
        "sha256": sha256_file(path),
        "query_count": len(workload["queries"]),
        "canonical_source_sha256": workload["provenance"]["canonical_source_sha256"],
    }


def _dataset_identity(dataset: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_id": dataset.BENCHMARK_ID,
        "content_identity": metadata["dataset_content_identity"],
        "csv_sha256": metadata["source_file_sha256"]["csv"],
        "rows": dataset.EXPECTED_ROWS,
        "schema_contract_id": dataset.SCHEMA_CONTRACT_ID,
    }


def _arm(
    *,
    workload_identity: dict[str, Any],
    truth_identity: dict[str, Any],
    stock_build: dict[str, Any],
    load: dict[str, Any],
    records: list[dict[str, Any]],
    raw_path: Path,
    research_root: Path,
    settings: dict[str, str],
    ordinary: str,
    stats: list[dict[str, Any]],
    evaluation_mode: str,
) -> dict[str, Any]:
    return _arm_common(
        workload_identity=workload_identity,
        truth_identity=truth_identity,
        build_identity=stock_build,
        load=load,
        records=records,
        raw_path=raw_path,
        logical_path=str(raw_path.relative_to(research_root)),
        settings=settings,
        ordinary=ordinary,
        stats=stats,
        evaluation_mode=evaluation_mode,
    )


def _deployment(
    *,
    advisor_command: str,
    run_directory: Path,
    manifest: dict[str, Any],
    stock_dsn: str,
    output: Path,
) -> dict[str, Any]:
    def artifact(name: str) -> Path:
        return run_directory / manifest["artifacts"][name]["path"]

    source_names = (
        "snapshot",
        "candidate_universe",
        "native_repository",
        "ground_truth",
        "singleton_profile",
        "optimization_plan",
        "search_result",
        "recommendation",
    )
    source_paths = [artifact(name) for name in source_names]
    _advisor_json(
        [
            advisor_command,
            "deployment",
            "apply",
            "postgres",
            *map(str, source_paths),
            "--dsn",
            stock_dsn,
            "--output",
            str(output),
        ]
    )
    _advisor_command(
        [
            advisor_command,
            "deployment",
            "validate",
            str(output),
            str(artifact("recommendation")),
            "--snapshot",
            str(artifact("snapshot")),
            "--candidate-universe",
            str(artifact("candidate_universe")),
            "--native-repository",
            str(artifact("native_repository")),
            "--ground-truth",
            str(artifact("ground_truth")),
            "--singleton-profile",
            str(artifact("singleton_profile")),
            "--optimization-plan",
            str(artifact("optimization_plan")),
            "--search-result",
            str(artifact("search_result")),
        ]
    )
    deployment = read_json(output)
    return {
        "verified": True,
        "path": str(output),
        "semantic_digest": deployment.get("semantic_digest"),
    }


def run_rq1_matched(
    *,
    dataset_id: str,
    stock_dsn: str,
    patched_dsn: str,
    output: Path,
    data_root: Path | None = None,
    advisor_root: Path = Path("/home/wqts/projects/extstats-advisor"),
    patched_postgres_root: Path = Path("/home/wqts/projects/postgresql-src-pgextadv"),
    advisor_command: str = "extstats-advisor",
) -> dict[str, Any]:
    """Run the three-arm matched comparison for one AreCEL dataset."""

    try:
        config = _CONFIGS[dataset_id]
    except KeyError as exc:
        raise ValueError(f"unsupported RQ1 dataset: {dataset_id}") from exc
    if output.exists():
        raise FileExistsError(output)
    output = output.resolve()
    research_root = Path(__file__).resolve().parents[2]
    research_identity = verify_research_repository(research_root)
    system_freeze = load_system_freeze(DEFAULT_SYSTEM_FREEZE_PATH)
    verify_frozen_systems(advisor_root, patched_postgres_root)
    stock_lab = _read_identity(role_spec("stock"))
    patched_lab = _read_identity(role_spec("patched"))
    stock_build = _semantic_build_identity(stock_lab)
    patched_build = _semantic_build_identity(patched_lab)
    metadata = config.dataset.inspect(data_root)
    truth_identity, truths = _external_truth(dataset_id, research_root)
    runtime = research_root / ".runtime" / f"rq1-{dataset_id.removeprefix('arecel-')}-matched"
    runtime.mkdir(parents=True, exist_ok=True)
    workload, workload_path = _workload(config.dataset, runtime, data_root)
    if workload["workload_id"] != truth_identity["workload_id"]:
        raise ValueError("workload and authoritative truth identities differ")
    workload_identity = _workload_identity(workload, workload_path)
    dataset_identity = _dataset_identity(config.dataset, metadata)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging_directory = runtime / "matched-raw"
    raw_paths = {arm_id: staging_directory / f"{arm_id}-per-query-v1.jsonl" for arm_id in ARM_IDS}
    arms: dict[str, dict[str, Any]] = {}
    advisor_result: dict[str, Any] | None = None
    try:
        for arm_id, target in (("pg16-default", 100), ("pg16-target10000", 10_000)):
            reinit_role("stock")
            load = config.loader(
                stock_dsn,
                data_root=data_root,
                reset_disposable=True,
                statistics_target=target,
                seed_identifier=SEED_IDENTIFIER,
            )
            records, settings, ordinary, stats = _evaluate_stock_arm(
                dsn=stock_dsn,
                dataset=config.dataset,
                workload=workload["queries"],
                truths=truths,
                raw_path=raw_paths[arm_id],
                arm_id=arm_id,
            )
            arms[arm_id] = _arm(
                workload_identity=workload_identity,
                truth_identity=truth_identity,
                stock_build=stock_build,
                load=load,
                records=records,
                raw_path=raw_paths[arm_id],
                research_root=research_root,
                settings=settings,
                ordinary=ordinary,
                stats=stats,
                evaluation_mode="stock-full-data-physical",
            )
        reinit_role("patched")
        reinit_role("stock")
        planner_dsn = patched_dsn
        if dataset_id == "arecel-census13":
            planner_dsn = _ensure_planner_catalog(patched_dsn, "extstats_rq1_census13")
        advisor_result = config.advisor_runner(
            production_dsn=stock_dsn,
            planner_dsn=planner_dsn,
            advisor_root=advisor_root,
            patched_postgres_root=patched_postgres_root,
            output_root=runtime / "advisor-design",
            sample_rows=10_000,
            sample_seed=42,
            statistics_target=100,
            candidate_limit=8,
            search_wall_clock_seconds=config.search_seconds,
            data_root=data_root,
            reset_disposable=True,
            seed_identifier=SEED_IDENTIFIER,
            compact_evidence_directory=output.parent / "advisor-canonical-k8",
            advisor_command=advisor_command,
            **(
                {
                    "truth_source": "authoritative-arecel",
                    "authoritative_observations": Path(
                        authoritative_truth_spec(dataset_id, research_root)["observations_path"]
                    ),
                }
                if dataset_id == "arecel-census13"
                else {}
            ),
        )
        run_directory = Path(advisor_result["run_directory"])
        run_manifest = read_json(run_directory / "manifest.json")
        deployment = _deployment(
            advisor_command=advisor_command,
            run_directory=run_directory,
            manifest=run_manifest,
            stock_dsn=stock_dsn,
            output=output.parent / "deployment-result-v1.json",
        )
        records, settings, ordinary, stats = _evaluate_stock_arm(
            dsn=stock_dsn,
            dataset=config.dataset,
            workload=workload["queries"],
            truths=truths,
            raw_path=raw_paths["pg16-advisor"],
            arm_id="pg16-advisor",
        )
        advisor_load = run_manifest.get("load")
        if not isinstance(advisor_load, dict):
            raise TypeError("advisor canonical run lacks load provenance")
        arms["pg16-advisor"] = _arm(
            workload_identity=workload_identity,
            truth_identity=truth_identity,
            stock_build=stock_build,
            load=advisor_load,
            records=records,
            raw_path=raw_paths["pg16-advisor"],
            research_root=research_root,
            settings=settings,
            ordinary=ordinary,
            stats=stats,
            evaluation_mode="stock-full-data-deployment",
        )
        arms["pg16-advisor"]["design_time_build_identity"] = patched_build
        recommendation = read_json(
            run_directory / run_manifest["artifacts"]["recommendation"]["path"]
        )
        search = read_json(run_directory / run_manifest["artifacts"]["search_result"]["path"])
        arms["pg16-advisor"]["recommendation"] = {
            "semantic_digest": recommendation["semantic_digest"],
            "selected_candidate_ids": recommendation.get("selected_candidate_ids", []),
            "deployment_ordered_candidate_ids": recommendation.get(
                "deployment_ordered_candidate_ids", []
            ),
        }
        arms["pg16-advisor"]["sandbox_optimization_objective"] = {
            "contract": "weighted-workload-mean-v1",
            "baseline": search["baseline_objective"],
            "final": search["final_objective"],
            "termination_reason": search.get("termination_reason"),
            "source": "patched-postgresql-design-time-sandbox",
        }
        arms["pg16-advisor"]["deployment_result"] = deployment
        arms["pg16-advisor"]["bound_ground_truth_set_semantic_digest"] = run_manifest.get(
            "truth_validation", {}
        ).get("ground_truth_digest")
    finally:
        for role in ("stock", "patched"):
            try:
                stop_role(role)
            except (OSError, RuntimeError, ValueError):
                pass
    if advisor_result is None:
        raise RuntimeError("advisor arm did not complete")
    for arm_id in ARM_IDS:
        final_path = output.parent / f"{arm_id}-per-query-v1.jsonl"
        shutil.copyfile(raw_paths[arm_id], final_path)
        arms[arm_id]["per_query_artifact"] = {
            "logical_path": str(final_path.relative_to(research_root)),
            "sha256": sha256_file(final_path),
        }
    artifact = build_rq1_artifact(
        research_commit_sha=research_identity["research_commit_sha"],
        system_freeze=system_freeze,
        dataset=dataset_identity,
        workload=workload_identity,
        truth=truth_identity,
        arms=arms,
        dataset_progress={
            item: ("complete" if item in {"arecel-census13", dataset_id} else "planned")
            for item in RQ1_DATASETS
        },
        experiment_status="dataset-complete",
        cleanup={
            "clusters_stopped": True,
            "fresh_state_protocol": "postgres-lab reinit before every arm",
            "default_no_extstats_verified": True,
            "target10000_no_extstats_verified": True,
            "advisor_stock_full_data_deployment_verified": True,
        },
        provenance={
            "truth_policy": "paper/benchmark-truth-policy-v1.json",
            "truth_policy_status": truth_identity["policy_status"],
            "authoritative_observations_sha256": truth_identity["observations_sha256"],
            "authoritative_observations_semantic_digest": truth_identity[
                "observations_semantic_digest"
            ],
            "bound_ground_truth_set_semantic_digest": arms["pg16-advisor"].get(
                "bound_ground_truth_set_semantic_digest"
            ),
            "advisor_run_directory": str(
                Path(advisor_result["run_directory"]).relative_to(research_root)
            ),
            "parameter_selection_basis": "pre-existing Forest10 canonical K=8 protocol",
            "parameters": {
                "sample_rows": 10_000,
                "sample_seed": 42,
                "statistics_target": 100,
                "candidate_limit": 8,
                "search_wall_clock_seconds": config.search_seconds,
                "experiment_seed_identifier": SEED_IDENTIFIER,
                "postgresql_setseed_sql": SETSEED_SQL,
            },
            "same_authoritative_observation_vector_for_all_arms": True,
            "power7_and_dmv11_run": False,
        },
    )
    write_json(output, artifact)
    return {
        "status": "complete",
        "artifact": str(output),
        "semantic_digest": artifact["semantic_digest"],
        "dataset_id": dataset_id,
        "summary": {arm_id: artifact["per_arm"][arm_id]["summary"] for arm_id in ARM_IDS},
    }


__all__ = ["RQ1_DATASETS", "run_rq1_matched"]
