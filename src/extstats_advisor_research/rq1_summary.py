"""Build and validate the four-dataset RQ1 matched-comparison summary."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, write_json
from .rq1_canary import ARM_IDS, validate_rq1_artifact

SUMMARY_FORMAT = "rq1-cross-dataset-summary-v1"
RQ1_DATASETS = (
    "arecel-census13",
    "arecel-forest10",
    "arecel-power7",
    "arecel-dmv11",
)


def _repository_relative_path(path: Path) -> str:
    repository_root = Path(__file__).resolve().parents[2]
    try:
        return str(path.resolve().relative_to(repository_root))
    except ValueError:
        return str(path)


def default_source_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        root / f"experiments/{dataset_id}/rq1-confirmatory/rq1-matched-comparison-v1.json"
        for dataset_id in RQ1_DATASETS
    )


def _direction(before: float, after: float) -> str:
    if after < before:
        return "lower"
    if after > before:
        return "higher"
    return "equal"


def _tail_concentration(records: list[dict[str, Any]]) -> dict[str, Any]:
    values = sorted((float(row["qerror"]) for row in records), reverse=True)
    total = sum(values)
    if not values or total <= 0:
        raise ValueError("q-error records must have positive aggregate mass")
    result: dict[str, Any] = {"query_count": len(values)}
    for label, fraction in (("top_1_percent", 0.01), ("top_10_percent", 0.10)):
        count = max(1, math.ceil(len(values) * fraction))
        result[label] = {
            "query_count": count,
            "objective_fraction": sum(values[:count]) / total,
        }
    return result


def _top_regressions(
    reference: list[dict[str, Any]], advisor: list[dict[str, Any]], limit: int = 10
) -> dict[str, Any]:
    by_query = {row["query_id"]: row for row in advisor}
    deltas = []
    for row in reference:
        other = by_query[row["query_id"]]
        delta = float(other["qerror"]) - float(row["qerror"])
        if delta > 0:
            deltas.append(
                {
                    "query_id": row["query_id"],
                    "reference_qerror": row["qerror"],
                    "advisor_qerror": other["qerror"],
                    "delta": delta,
                }
            )
    deltas.sort(key=lambda row: (-row["delta"], row["query_id"]))
    return {"count": len(deltas), "top": deltas[:limit]}


def _dataset_summary(artifact: dict[str, Any], path: Path) -> dict[str, Any]:
    dataset_id = artifact["dataset"]["dataset_id"]
    per_arm = artifact["per_arm"]
    default = per_arm["pg16-default"]
    target = per_arm["pg16-target10000"]
    advisor = per_arm["pg16-advisor"]

    def comparison(reference: dict[str, Any]) -> dict[str, Any]:
        return {
            "mean": _direction(
                reference["summary"]["arithmetic_mean_qerror"],
                advisor["summary"]["arithmetic_mean_qerror"],
            ),
            "p50": _direction(reference["summary"]["p50_qerror"], advisor["summary"]["p50_qerror"]),
            "mean_p50_direction_conflict": (
                _direction(
                    reference["summary"]["arithmetic_mean_qerror"],
                    advisor["summary"]["arithmetic_mean_qerror"],
                )
                != _direction(reference["summary"]["p50_qerror"], advisor["summary"]["p50_qerror"])
            ),
        }

    return {
        "dataset_id": dataset_id,
        "workload_id": artifact["workload"]["workload_id"],
        "query_count": artifact["workload"]["query_count"],
        "source_artifact": {
            "path": _repository_relative_path(path),
            "semantic_digest": artifact["semantic_digest"],
            "research_commit_sha": artifact["research_commit_sha"],
        },
        "truth": {
            "source_kind": artifact["truth"]["source_kind"],
            "policy_status": artifact["truth"]["policy_status"],
            "observations_sha256": artifact["truth"]["observations_sha256"],
            "observations_semantic_digest": artifact["truth"]["observations_semantic_digest"],
            "bound_ground_truth_set_semantic_digest": artifact["provenance"].get(
                "bound_ground_truth_set_semantic_digest",
                artifact["truth"]["observations_semantic_digest"],
            ),
        },
        "arms": {
            arm_id: {
                "metrics": per_arm[arm_id]["summary"],
                "evaluation_mode": per_arm[arm_id]["evaluation_mode"],
                "physical_extended_statistics_count": per_arm[arm_id][
                    "physical_extended_statistics_count"
                ],
            }
            for arm_id in ARM_IDS
        },
        "headline": {
            "default_mean": default["summary"]["arithmetic_mean_qerror"],
            "target10000_mean": target["summary"]["arithmetic_mean_qerror"],
            "advisor_mean": advisor["summary"]["arithmetic_mean_qerror"],
            "advisor_p95": advisor["summary"]["p95_qerror"],
            "advisor_p99": advisor["summary"]["p99_qerror"],
            "advisor_max": advisor["summary"]["max_qerror"],
        },
        "paired_classifications": artifact["paired_comparison"],
        "recommendation": per_arm["pg16-advisor"].get("recommendation"),
        "sandbox_objective": per_arm["pg16-advisor"].get("sandbox_optimization_objective"),
        "deployment": per_arm["pg16-advisor"].get("deployment_result"),
        "per_query_regressions": {
            "default_to_advisor": _top_regressions(default["per_query"], advisor["per_query"]),
            "target10000_to_advisor": _top_regressions(target["per_query"], advisor["per_query"]),
        },
        "tail_error_concentration": {
            arm_id: _tail_concentration(per_arm[arm_id]["per_query"]) for arm_id in ARM_IDS
        },
        "mean_p50_trend": {
            "default_to_advisor": comparison(default),
            "target10000_to_advisor": comparison(target),
        },
    }


def _assemble(paths: tuple[Path, ...]) -> dict[str, Any]:
    if tuple(path.stem for path in paths) != ("rq1-matched-comparison-v1",) * len(paths):
        raise ValueError("RQ1 summary sources must use the matched-comparison-v1 artifact")
    if len(paths) != len(RQ1_DATASETS):
        raise ValueError("RQ1 cross-dataset summary requires exactly four datasets")

    artifacts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for path, expected_dataset in zip(paths, RQ1_DATASETS, strict=True):
        artifact = read_json(path)
        validate_rq1_artifact(artifact)
        if artifact["dataset"]["dataset_id"] != expected_dataset:
            raise ValueError(f"summary source order/dataset mismatch for {path}")
        if artifact["experiment_status"] != "dataset-complete":
            raise ValueError(f"{expected_dataset} is not dataset-complete")
        if artifact["truth"]["source_kind"] != "authoritative-external-exact":
            raise ValueError(f"{expected_dataset} does not use authoritative external truth")
        artifacts.append(artifact)
        rows.append(_dataset_summary(artifact, path))

    freeze_digests = {artifact["system_freeze_semantic_digest"] for artifact in artifacts}
    if len(freeze_digests) != 1:
        raise ValueError("RQ1 datasets do not share one frozen SUT identity")
    freeze = artifacts[0]["system_freeze"]
    return {
        "format_version": SUMMARY_FORMAT,
        "summary_id": SUMMARY_FORMAT,
        "status": "complete",
        "scope": "four-dataset, three-arm, in-workload matched comparison",
        "held_out_generalization_claim": False,
        "rq4_heuristics_included": False,
        "source_artifacts": [row["source_artifact"] for row in rows],
        "system_freeze": {
            "semantic_digest": next(iter(freeze_digests)),
            "advisor_commit_sha": freeze["advisor"]["commit_sha"],
            "patched_postgres_commit_sha": freeze["patched_postgresql"]["source_commit_sha"],
            "stock_postgres_commit_sha": freeze["stock_postgresql"]["source_commit_sha"],
            "postgres_version": freeze["patched_postgresql"]["postgres_version"],
        },
        "datasets": rows,
        "headline_table": [{"dataset_id": row["dataset_id"], **row["headline"]} for row in rows],
    }


def build_cross_dataset_summary(
    source_paths: tuple[Path, ...], output: Path | None = None
) -> dict[str, Any]:
    summary = _assemble(source_paths)
    summary["semantic_digest"] = semantic_digest(summary)
    if output is not None:
        write_json(output, summary)
    return summary


def validate_cross_dataset_summary(path: Path) -> dict[str, Any]:
    summary = read_json(path)
    repository_root = Path(__file__).resolve().parents[2]
    source_paths = tuple(
        Path(item["path"]) if Path(item["path"]).is_absolute() else repository_root / item["path"]
        for item in summary.get("source_artifacts", [])
    )
    expected = build_cross_dataset_summary(source_paths)
    if summary != expected:
        raise ValueError("cross-dataset summary is not reproducible from source artifacts")
    return {
        "status": "valid",
        "format_version": SUMMARY_FORMAT,
        "dataset_count": len(expected["datasets"]),
        "semantic_digest": expected["semantic_digest"],
    }


__all__ = [
    "SUMMARY_FORMAT",
    "build_cross_dataset_summary",
    "default_source_paths",
    "validate_cross_dataset_summary",
]
