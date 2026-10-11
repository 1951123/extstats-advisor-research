from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq5_whatif_cost import (
    IncompleteRunError,
    WhatIfCostError,
    read_events,
    run_physical_arm,
)
from extstats_advisor_research.rq5_whatif_cost_attribution import (
    derive_run_attribution,
    summarize_attribution,
)

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads(
    (
        ROOT / "experiments/rq5-whatif-evaluation-cost-v3/manifests/analysis-a-manifest-v1.json"
    ).read_text()
)


class _Physical:
    def prepare_run(self) -> int:
        return 1

    def create_clone(self, configuration: dict) -> int:
        return 1

    def create_statistics(self, configuration: dict) -> int:
        return len(configuration["candidate_ids"])

    def analyze(self, configuration: dict) -> int:
        return 1

    def explain(self, configuration: dict, query_ids: list[str]) -> int:
        return len(query_ids)

    def drop_statistics(self, configuration: dict) -> int:
        return len(configuration["candidate_ids"])

    def destroy_clone(self, configuration: dict) -> int:
        return 1

    def cleanup(self) -> int:
        return 1


def _clock() -> callable:
    value = 0

    def tick() -> int:
        nonlocal value
        value += 10
        return value

    return tick


def _events(tmp_path: Path, run_id: str = "attribution-run") -> list[dict]:
    path = tmp_path / f"{run_id}.jsonl"
    run_physical_arm(
        MANIFEST,
        _Physical(),
        run_id=run_id,
        repetition=1,
        events_path=path,
        clock=_clock(),
    )
    return read_events(path)


def test_clone_exclusion_subtracts_only_clone_stages_and_keeps_cleanup(tmp_path: Path) -> None:
    result = derive_run_attribution(
        MANIFEST,
        _events(tmp_path),
        run_id="attribution-run",
        arm="physical",
        repetition=1,
    )
    first = result["checkpoints"][0]
    final = result["checkpoints"][-1]
    assert first["full_lifecycle_ns"] == 70
    assert first["clone_total_ns"] == 20
    assert first["clone_excluded_lifecycle_ns"] == 50
    assert first["statistics_ddl_ns"] == 10
    assert first["analyze_ns"] == 10
    assert first["explain_ns"] == 10
    assert first["drop_statistics_ns"] == 10
    assert first["final_cleanup_ns"] == 0
    assert final["full_lifecycle_ns"] == 1220
    assert final["final_cleanup_ns"] == 10
    assert final["clone_excluded_lifecycle_ns"] == 820


def test_attribution_aggregates_per_run_before_median(tmp_path: Path) -> None:
    first = derive_run_attribution(
        MANIFEST, _events(tmp_path, "run-one"), run_id="run-one", arm="physical", repetition=1
    )
    second = json.loads(json.dumps(first))
    second["run_id"] = "run-two"
    second["repetition"] = 2
    second["checkpoints"][0]["full_lifecycle_ns"] = 20
    third = json.loads(json.dumps(first))
    third["run_id"] = "run-three"
    third["repetition"] = 3
    third["checkpoints"][0]["full_lifecycle_ns"] = 100
    catalogless = json.loads(json.dumps(first))
    catalogless["run_id"] = "catalogless-one"
    catalogless["arm"] = "catalogless"
    catalogless["repetition"] = 1
    catalogless["checkpoints"][0]["full_lifecycle_ns"] = 1
    catalogless_two = json.loads(json.dumps(catalogless))
    catalogless_two["run_id"] = "catalogless-two"
    catalogless_two["repetition"] = 2
    catalogless_three = json.loads(json.dumps(catalogless))
    catalogless_three["run_id"] = "catalogless-three"
    catalogless_three["repetition"] = 3
    summary = summarize_attribution(
        [first, second, third, catalogless, catalogless_two, catalogless_three]
    )
    assert summary["checkpoints"]["1"]["physical"]["full_lifecycle_ns"]["median_ns"] == 70


def test_missing_event_is_rejected_before_attribution(tmp_path: Path) -> None:
    events = _events(tmp_path, "missing-run")
    events = [event for event in events if event["stage"] != "clone-create"]
    with pytest.raises(IncompleteRunError):
        derive_run_attribution(
            MANIFEST,
            events,
            run_id="missing-run",
            arm="physical",
            repetition=1,
        )


def test_duplicate_event_is_rejected_before_attribution(tmp_path: Path) -> None:
    events = _events(tmp_path, "duplicate-run")
    events.append(dict(events[0]))
    with pytest.raises(WhatIfCostError, match="duplicate event"):
        derive_run_attribution(
            MANIFEST,
            events,
            run_id="duplicate-run",
            arm="physical",
            repetition=1,
        )


def test_failed_event_is_rejected_before_attribution(tmp_path: Path) -> None:
    events = _events(tmp_path, "failed-run")
    events[0] = dict(events[0], status="failed", error={"class": "Test", "message": "failure"})
    with pytest.raises(IncompleteRunError):
        derive_run_attribution(
            MANIFEST,
            events,
            run_id="failed-run",
            arm="physical",
            repetition=1,
        )


def test_attribution_is_deterministic(tmp_path: Path) -> None:
    events = _events(tmp_path, "repeat-run")
    first = derive_run_attribution(
        MANIFEST, events, run_id="repeat-run", arm="physical", repetition=1
    )
    second = derive_run_attribution(
        MANIFEST, events, run_id="repeat-run", arm="physical", repetition=1
    )
    assert first == second
