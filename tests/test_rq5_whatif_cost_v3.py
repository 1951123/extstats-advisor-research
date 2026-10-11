from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq5_whatif_cost import (
    IncompleteRunError,
    WhatIfCostError,
    read_events,
    run_catalogless_arm,
    semantic_digest,
    validate_events,
    validate_manifest,
)
from extstats_advisor_research.rq5_whatif_cost_v3 import (
    V3_MANIFEST_FORMAT,
    V3_SAMPLE_INDEX_DIGEST,
    V3_SAMPLE_ROWS,
    derive_sample_indices,
    sample_index_digest,
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


def test_v3_frozen_sample_membership_digest_is_preserved() -> None:
    indices = derive_sample_indices()
    assert len(indices) == V3_SAMPLE_ROWS
    assert sample_index_digest(indices) == V3_SAMPLE_INDEX_DIGEST


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


class _FakeV3Catalogless:
    def _count(self, count: int) -> int:
        return count

    def acquire_sample(self) -> int:
        return self._count(1)

    def construct_snapshot(self) -> int:
        return self._count(1)

    def validate_snapshot(self) -> int:
        return self._count(1)

    def prepare_sample(self) -> int:
        return self._count(1)

    def materialize_payloads(self) -> int:
        return self._count(57)

    def register_repository(self) -> int:
        return self._count(57)

    def activate(self, configuration: dict) -> int:
        return self._count(len(configuration["candidate_ids"]))

    def explain(self, configuration: dict, query_ids: list[str]) -> int:
        return self._count(len(query_ids))

    def deactivate(self, configuration: dict) -> int:
        return self._count(len(configuration["candidate_ids"]))

    def cleanup(self) -> int:
        return self._count(1)


def _fixed_clock() -> callable:
    value = 0

    def tick() -> int:
        nonlocal value
        value += 10
        return value

    return tick


def test_v3_catalogless_preparation_is_explicitly_timed_and_ordered(tmp_path: Path) -> None:
    manifest = _manifest("A")
    events_path = tmp_path / "v3.events.jsonl"
    summary = run_catalogless_arm(
        manifest,
        _FakeV3Catalogless(),
        run_id="v3-offline-run",
        repetition=1,
        events_path=events_path,
        clock=_fixed_clock(),
    )
    events = read_events(events_path)
    stages = [event["stage"] for event in events]
    assert stages[:6] == [
        "sample-acquisition",
        "snapshot-construction",
        "snapshot-serialization-validation",
        "fixed-sample-preparation",
        "native-payload-materialization",
        "repository-registration-and-sandbox-preparation",
    ]
    assert stages[-1] == "arm-cleanup"
    assert summary["preparation_ns"] == 60
    assert summary["stage_totals_ns"]["snapshot-construction"] == 10
    assert summary["warm_start_excludes"] == [
        "sample-acquisition",
        "snapshot-construction",
        "snapshot-serialization-validation",
        "fixed-sample-preparation",
        "native-payload-materialization",
        "repository-registration-and-sandbox-preparation",
    ]
    assert summary["analyze_calls"] == 0


def test_v3_missing_preparation_event_rejects_complete_summary(tmp_path: Path) -> None:
    manifest = _manifest("A")
    events_path = tmp_path / "v3-missing.events.jsonl"
    run_catalogless_arm(
        manifest,
        _FakeV3Catalogless(),
        run_id="v3-missing-run",
        repetition=1,
        events_path=events_path,
        clock=_fixed_clock(),
    )
    events = read_events(events_path)
    events = [event for event in events if event["stage"] != "snapshot-construction"]
    with pytest.raises(IncompleteRunError, match="snapshot-construction"):
        validate_events(
            manifest,
            events,
            run_id="v3-missing-run",
            arm="catalogless",
            repetition=1,
        )


def test_v3_out_of_order_preparation_is_rejected(tmp_path: Path) -> None:
    manifest = _manifest("A")
    events_path = tmp_path / "v3-order.events.jsonl"
    run_catalogless_arm(
        manifest,
        _FakeV3Catalogless(),
        run_id="v3-order-run",
        repetition=1,
        events_path=events_path,
        clock=_fixed_clock(),
    )
    events = read_events(events_path)
    events[0], events[1] = events[1], events[0]
    with pytest.raises(WhatIfCostError, match="event order drifted"):
        validate_events(
            manifest,
            events,
            run_id="v3-order-run",
            arm="catalogless",
            repetition=1,
        )
