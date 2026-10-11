from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq5_whatif_cost import (
    WhatIfCostError,
    semantic_digest,
    validate_manifest,
)
from extstats_advisor_research.rq5_whatif_cost_v3 import (
    V3_MANIFEST_FORMAT,
    V3_SAMPLE_ROWS,
    derive_sample_indices,
    sample_recipe,
    validate_v3_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = ROOT / "experiments/rq5-whatif-evaluation-cost-v3/manifests"


def _manifest(analysis: str) -> dict:
    return json.loads((MANIFESTS / f"analysis-{analysis.lower()}-manifest-v1.json").read_text())


def test_v3_manifests_preserve_frozen_dimensions_without_live_access() -> None:
    analysis_a = _manifest("A")
    analysis_b = _manifest("B")

    assert validate_manifest(analysis_a)["format_version"] == V3_MANIFEST_FORMAT
    assert validate_manifest(analysis_b)["format_version"] == V3_MANIFEST_FORMAT
    assert validate_v3_manifest(analysis_a)["sample_ready"] is True
    assert len(analysis_a["candidate_universe"]["definitions"]) == 57
    assert len(analysis_a["query_subset"]) == 128
    assert len(analysis_a["analysis_a"]["configurations"]) == 20
    assert {item["configuration_size"] for item in analysis_a["analysis_a"]["configurations"]} == {
        1
    }
    assert [item["configuration_size"] for item in analysis_b["analysis_b"]["configurations"]] == [
        0,
        1,
        5,
        10,
        20,
        50,
    ]
    assert analysis_a["binding_digest"] != analysis_b["binding_digest"]
    assert analysis_a["dataset"]["full_population_rows"] == 581_012
    assert analysis_a["dataset"]["catalogless_sample_rows"] == V3_SAMPLE_ROWS


def test_v3_sample_recipe_and_selection_are_deterministic() -> None:
    first = derive_sample_indices(population_rows=100, sample_rows=10, seed=42)
    second = derive_sample_indices(population_rows=100, sample_rows=10, seed=42)
    assert first == second
    assert len(first) == 10
    assert first == sorted(first)
    assert sample_recipe(population_rows=100, sample_rows=10, seed=42)["algorithm"] == (
        "sha256-row-rank-v1"
    )


def test_v3_manifest_rejects_missing_sample_in_strict_readiness_mode(tmp_path: Path) -> None:
    value = _manifest("A")
    value["sample_contract"]["snapshot_path"] = str(tmp_path / "missing-snapshot")
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    with pytest.raises(WhatIfCostError, match="sealed snapshot is missing"):
        validate_v3_manifest(value, require_sample=True)


def test_v3_manifest_rejects_sample_size_drift() -> None:
    value = _manifest("A")
    value["dataset"]["catalogless_sample_rows"] = 10_000
    with pytest.raises(WhatIfCostError, match="semantic digest mismatch"):
        validate_v3_manifest(value)
