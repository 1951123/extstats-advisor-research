from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq1_summary import (
    _ground_truth_provenance,
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


def test_summary_v2_distinguishes_truth_provenance_roles() -> None:
    summary = json.loads(
        (ROOT / "experiments/rq1-cross-dataset-summary-v2.json").read_text(encoding="utf-8")
    )
    assert summary["format_version"] == "rq1-cross-dataset-summary-v2"
    census = next(row for row in summary["datasets"] if row["dataset_id"] == "arecel-census13")
    truth = census["truth"]
    provenance = truth["ground_truth_provenance"]
    assert (
        truth["observations_sha256"]
        == "de3870a2665bbacbc9f299535222ee64f2bf967a5757345f883cff9e1cf6d99d"
    )
    assert (
        truth["observations_semantic_digest"]
        == "b9cda0042fd5a1d4fd7818ebf3361e46c5146a0a22a8841561d756a1c2b1a811"
    )
    assert (
        provenance["snapshot_bound_ground_truth_set_semantic_digest"]
        == "c59ef631e11f79524475f0345ea54dea35ae976e4b811842da208d63a9d80e26"
    )
    assert (
        provenance["historical_production_exact_ground_truth_semantic_digest"]
        == "2cb7c9e89d020581c8a632c20a3a971cee3fd7e44a6a67e91bcea28d57038509"
    )
    assert (
        provenance["equivalence_bound_external_ground_truth_semantic_digest"]
        == "575c4036422ee7c414ba4f354217415868576fdcfb496e681cf45023151a299c"
    )
    assert (
        provenance["equivalence_artifact"]
        == "experiments/arecel-census13/arecel-truth-equivalence-v1.json"
    )
    assert "bound_ground_truth_set_semantic_digest" not in provenance


def test_summary_requires_snapshot_bound_ground_truth_digest() -> None:
    artifact = json.loads(
        (
            ROOT / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json"
        ).read_text(encoding="utf-8")
    )
    artifact["provenance"].pop("bound_ground_truth_set_semantic_digest")
    with pytest.raises(ValueError, match="snapshot-bound GroundTruthSet digest"):
        _ground_truth_provenance(artifact, ROOT)
