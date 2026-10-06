from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq1_summary import (
    build_cross_dataset_summary,
    default_source_paths,
    validate_cross_dataset_summary,
)

ROOT = Path(__file__).resolve().parents[1]


def test_cross_dataset_summary_is_reproducible(tmp_path: Path) -> None:
    output = tmp_path / "summary.json"
    summary = build_cross_dataset_summary(default_source_paths(ROOT), output)
    assert summary["status"] == "complete"
    assert summary["scope"] == "four-dataset, three-arm, in-workload matched comparison"
    assert summary["held_out_generalization_claim"] is False
    assert summary["rq4_heuristics_included"] is False
    assert [row["dataset_id"] for row in summary["headline_table"]] == [
        "arecel-census13",
        "arecel-forest10",
        "arecel-power7",
        "arecel-dmv11",
    ]
    assert validate_cross_dataset_summary(output)["status"] == "valid"


def test_cross_dataset_summary_rejects_source_digest_or_metric_drift(tmp_path: Path) -> None:
    source = json.loads(
        (
            ROOT / "experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json"
        ).read_text(encoding="utf-8")
    )
    source["per_arm"]["pg16-default"]["summary"]["arithmetic_mean_qerror"] += 1
    broken = tmp_path / "census.json"
    broken.write_text(json.dumps(source), encoding="utf-8")
    paths = list(default_source_paths(ROOT))
    paths[0] = broken
    with pytest.raises(ValueError):
        build_cross_dataset_summary(tuple(paths))


def test_cross_dataset_summary_does_not_mutate_source_artifacts() -> None:
    before = json.loads(
        (
            ROOT / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json"
        ).read_text(encoding="utf-8")
    )
    build_cross_dataset_summary(default_source_paths(ROOT))
    after = json.loads(
        (
            ROOT / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json"
        ).read_text(encoding="utf-8")
    )
    assert after == before
