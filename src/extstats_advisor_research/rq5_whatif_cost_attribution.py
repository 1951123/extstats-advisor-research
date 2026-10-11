"""Offline clone-attribution analysis for formal RQ5 v3 Analysis A.

This module consumes validated raw event streams.  It never opens a database
connection and does not represent an actually measured no-clone PostgreSQL
workflow; clone-excluded values are explicitly accounting sensitivities.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .rq5_whatif_cost import validate_events

CHECKPOINTS = (1, 5, 10, 20)
PHYSICAL_CLONE_STAGES = ("clone-create", "clone-destroy")
PHYSICAL_ACCOUNTING_STAGES = (
    "baseline-initialization",
    "clone-create",
    "create-alter-statistics",
    "analyze",
    "explain",
    "drop-statistics",
    "clone-destroy",
    "arm-cleanup",
)
CATALOGLESS_PREPARATION_STAGES = (
    "sample-acquisition",
    "snapshot-construction",
    "snapshot-serialization-validation",
    "fixed-sample-preparation",
    "native-payload-materialization",
    "repository-registration-and-sandbox-preparation",
)
CATALOGLESS_ACCOUNTING_STAGES = CATALOGLESS_PREPARATION_STAGES + (
    "activate",
    "explain",
    "deactivate",
    "arm-cleanup",
)


class AttributionError(ValueError):
    """Raised when raw formal evidence cannot support attribution."""


def _stage_total(events: Sequence[Mapping[str, Any]], stage: str) -> int:
    return sum(int(event["elapsed_ns"]) for event in events if event["stage"] == stage)


def _config_stage_total(events: Sequence[Mapping[str, Any]], ordinal: int, stage: str) -> int:
    return sum(
        int(event["elapsed_ns"])
        for event in events
        if event["configuration_ordinal"] == ordinal and event["stage"] == stage
    )


def _summary(values: Sequence[int]) -> dict[str, Any]:
    if not values:
        raise AttributionError("cannot summarize an empty metric")
    return {
        "values_ns": list(values),
        "median_ns": statistics.median(values),
        "minimum_ns": min(values),
        "maximum_ns": max(values),
    }


def derive_run_attribution(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    arm: str,
    repetition: int,
    expected_producer_sha: str | None = None,
    checkpoints: Sequence[int] = CHECKPOINTS,
) -> dict[str, Any]:
    """Derive per-run checkpoint metrics directly from validated events."""

    validate_events(
        manifest,
        events,
        run_id=run_id,
        arm=arm,
        repetition=repetition,
        require_complete=True,
    )
    if expected_producer_sha is not None and any(
        event.get("producer_commit_sha") != expected_producer_sha for event in events
    ):
        raise AttributionError("raw event producer identity does not match the expected producer")
    if tuple(checkpoints) != CHECKPOINTS:
        raise AttributionError("Analysis A attribution requires checkpoints 1, 5, 10, 20")

    configurations = list(manifest["analysis_a"]["configurations"])
    if len(configurations) != 20:
        raise AttributionError("formal Analysis A must contain exactly 20 configurations")
    by_ordinal = {int(item["ordinal"]): item for item in configurations}
    run_stage_names = (
        PHYSICAL_ACCOUNTING_STAGES if arm == "physical" else CATALOGLESS_ACCOUNTING_STAGES
    )
    if arm not in {"physical", "catalogless"}:
        raise AttributionError(f"unsupported arm: {arm}")

    stage_totals = {stage: _stage_total(events, stage) for stage in run_stage_names}
    preparation_stages = (
        ("baseline-initialization",) if arm == "physical" else CATALOGLESS_PREPARATION_STAGES
    )
    preparation_ns = sum(stage_totals[stage] for stage in preparation_stages)
    cleanup_ns = stage_totals["arm-cleanup"]
    per_configuration: list[dict[str, Any]] = []
    for ordinal in range(1, 21):
        config = by_ordinal[ordinal]
        stage_ns = {
            stage: _config_stage_total(events, ordinal, stage)
            for stage in (
                PHYSICAL_ACCOUNTING_STAGES
                if arm == "physical"
                else (
                    "activate",
                    "explain",
                    "deactivate",
                )
            )
            if stage not in {"baseline-initialization", "arm-cleanup"}
        }
        per_configuration.append(
            {
                "ordinal": ordinal,
                "configuration_id": config["configuration_id"],
                "stage_ns": stage_ns,
                "total_ns": sum(stage_ns.values()),
            }
        )

    points: list[dict[str, Any]] = []
    for checkpoint in CHECKPOINTS:
        selected = per_configuration[:checkpoint]
        configuration_ns = sum(item["total_ns"] for item in selected)
        final_cleanup_ns = cleanup_ns if checkpoint == 20 else 0
        full_ns = preparation_ns + configuration_ns + final_cleanup_ns
        point: dict[str, Any] = {
            "N": checkpoint,
            "full_lifecycle_ns": full_ns,
            "preparation_ns": preparation_ns,
            "final_cleanup_ns": final_cleanup_ns,
            "cleanup_included_directly": checkpoint == 20,
            "configuration_evaluation_ns": configuration_ns,
            "warm_start_ns": configuration_ns,
        }
        if arm == "physical":
            clone_create = sum(item["stage_ns"]["clone-create"] for item in selected)
            clone_destroy = sum(item["stage_ns"]["clone-destroy"] for item in selected)
            clone_ns = clone_create + clone_destroy
            point.update(
                {
                    "clone_create_ns": clone_create,
                    "clone_destroy_ns": clone_destroy,
                    "clone_total_ns": clone_ns,
                    "clone_excluded_lifecycle_ns": full_ns - clone_ns,
                    "statistics_ddl_ns": sum(
                        item["stage_ns"]["create-alter-statistics"] for item in selected
                    ),
                    "analyze_ns": sum(item["stage_ns"]["analyze"] for item in selected),
                    "explain_ns": sum(item["stage_ns"]["explain"] for item in selected),
                    "drop_statistics_ns": sum(
                        item["stage_ns"]["drop-statistics"] for item in selected
                    ),
                }
            )
        else:
            point.update(
                {
                    "catalogless_preparation_ns": preparation_ns,
                    "activation_ns": sum(item["stage_ns"]["activate"] for item in selected),
                    "deactivation_ns": sum(item["stage_ns"]["deactivate"] for item in selected),
                    "explain_ns": sum(item["stage_ns"]["explain"] for item in selected),
                }
            )
        points.append(point)

    return {
        "run_id": run_id,
        "arm": arm,
        "repetition": repetition,
        "event_count": len(events),
        "stage_totals_ns": stage_totals,
        "preparation_ns": preparation_ns,
        "cleanup_ns": cleanup_ns,
        "per_configuration": per_configuration,
        "checkpoints": points,
    }


def summarize_attribution(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate already-derived runs; never aggregate raw stages independently."""

    if len(runs) != 6 or {run["arm"] for run in runs} != {"physical", "catalogless"}:
        raise AttributionError("expected three physical and three catalogless runs")
    for arm in ("physical", "catalogless"):
        if sum(run["arm"] == arm for run in runs) != 3:
            raise AttributionError(f"expected three complete {arm} runs")

    by_checkpoint: dict[str, dict[str, Any]] = {}
    for checkpoint in CHECKPOINTS:
        by_checkpoint[str(checkpoint)] = {}
        for arm in ("physical", "catalogless"):
            selected = [run for run in runs if run["arm"] == arm]
            points = [
                next(point for point in run["checkpoints"] if point["N"] == checkpoint)
                for run in selected
            ]
            metric_names = sorted(
                {
                    key
                    for point in points
                    for key, value in point.items()
                    if key.endswith("_ns") and isinstance(value, int)
                }
            )
            by_checkpoint[str(checkpoint)][arm] = {
                metric: _summary([int(point[metric]) for point in points])
                for metric in metric_names
            }
    stage_summary: dict[str, dict[str, Any]] = {}
    for arm in ("physical", "catalogless"):
        selected = [run for run in runs if run["arm"] == arm]
        stages = sorted({stage for run in selected for stage in run["stage_totals_ns"]})
        stage_summary[arm] = {
            stage: _summary([int(run["stage_totals_ns"].get(stage, 0)) for run in selected])
            for stage in stages
        }
    return {
        "checkpoints": by_checkpoint,
        "stage_summary": stage_summary,
        "claim_boundary": "derived accounting sensitivity; no-clone workflow not measured",
    }


def reconcile_published_checkpoints(
    runs: Sequence[Mapping[str, Any]], published: Mapping[str, Any]
) -> dict[str, Any]:
    """Reconcile raw-derived full lifecycle values to the published summary."""

    comparisons = []
    for run in runs:
        arm = str(run["arm"])
        for point in run["checkpoints"]:
            n = str(point["N"])
            values = published["checkpoints"][n][arm]["complete_lifecycle_ns"]["values"]
            expected = int(values[int(run["repetition"]) - 1])
            actual = int(point["full_lifecycle_ns"])
            comparisons.append(
                {
                    "run_id": run["run_id"],
                    "arm": arm,
                    "repetition": run["repetition"],
                    "N": point["N"],
                    "raw_derived_ns": actual,
                    "published_ns": expected,
                    "match": actual == expected,
                }
            )
    mismatches = [item for item in comparisons if not item["match"]]
    return {
        "status": "passed" if not mismatches else "failed",
        "comparison_count": len(comparisons),
        "mismatch_count": len(mismatches),
        "comparisons": comparisons,
    }
