from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from extstats_advisor_research.rq5_whatif_cost import (
    AdapterExecutionError,
    IncompleteRunError,
    WhatIfCostError,
    build_manifest,
    compare_costs,
    integration_preflight_plan,
    read_events,
    run_catalogless_arm,
    run_physical_arm,
    semantic_digest,
    validate_events,
    validate_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _workload(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    queries = [
        {
            "query_id": f"arecel_forest10_test_{index:06d}",
            "sql": f'SELECT * FROM public.forest10 WHERE "elevation" = {index};',
            "weight": 1.0,
        }
        for index in range(10_000)
    ]
    path = tmp_path / "workload.json"
    path.write_text(
        json.dumps(
            {
                "workload_id": "arecel_forest10_test_v1",
                "provenance": {"source": "offline-test-fixture"},
                "queries": queries,
            }
        ),
        encoding="utf-8",
    )
    return path


def _manifests(tmp_path: Path) -> tuple[dict, dict]:
    test_root = tmp_path / "research"
    test_root.mkdir()
    for relative in (
        "docs/protocols/rq5-whatif-evaluation-cost-v2.md",
        "experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json",
        "experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json",
    ):
        destination = test_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    workload = _workload(test_root / "runs")
    kwargs = {
        "research_root": test_root,
        "workload_path": workload,
        "producer_sha": "a" * 40,
        "protocol_path": test_root / "docs/protocols/rq5-whatif-evaluation-cost-v2.md",
    }
    return build_manifest(analysis="A", **kwargs), build_manifest(analysis="B", **kwargs)


class FakePhysical:
    def __init__(self, *, fail: str | None = None, cleanup_fail: bool = False) -> None:
        self.fail = fail
        self.cleanup_fail = cleanup_fail
        self.calls: list[str] = []

    def _run(self, name: str, count: int) -> int:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"failed at {name}")
        return count

    def prepare_run(self) -> int:
        return self._run("baseline-initialization", 1)

    def create_clone(self, configuration: dict) -> int:
        return self._run("clone-create", 1)

    def create_statistics(self, configuration: dict) -> int:
        return self._run("create-alter-statistics", len(configuration["candidate_ids"]))

    def analyze(self, configuration: dict) -> int:
        return self._run("analyze", 1)

    def explain(self, configuration: dict, query_ids: list[str]) -> int:
        return self._run("explain", len(query_ids))

    def drop_statistics(self, configuration: dict) -> int:
        return self._run("drop-statistics", len(configuration["candidate_ids"]))

    def destroy_clone(self, configuration: dict) -> int:
        return self._run("clone-destroy", 1)

    def cleanup(self) -> int:
        if self.cleanup_fail:
            self.calls.append("arm-cleanup")
            raise RuntimeError("cleanup failed")
        return self._run("arm-cleanup", 1)


class FakeCatalogless:
    def __init__(self, *, fail: str | None = None, cleanup_fail: bool = False) -> None:
        self.fail = fail
        self.cleanup_fail = cleanup_fail
        self.calls: list[str] = []

    def _run(self, name: str, count: int) -> int:
        self.calls.append(name)
        if self.fail == name:
            raise RuntimeError(f"failed at {name}")
        return count

    def prepare_sample(self) -> int:
        return self._run("fixed-sample-preparation", 1)

    def materialize_payloads(self) -> int:
        return self._run("native-payload-materialization", 57)

    def register_repository(self) -> int:
        return self._run("repository-registration", 57)

    def activate(self, configuration: dict) -> int:
        return self._run("activate", len(configuration["candidate_ids"]))

    def explain(self, configuration: dict, query_ids: list[str]) -> int:
        return self._run("explain", len(query_ids))

    def deactivate(self, configuration: dict) -> int:
        return self._run("deactivate", len(configuration["candidate_ids"]))

    def cleanup(self) -> int:
        if self.cleanup_fail:
            self.calls.append("arm-cleanup")
            raise RuntimeError("cleanup failed")
        return self._run("arm-cleanup", 1)


def _clock() -> callable:
    value = 0

    def tick() -> int:
        nonlocal value
        value += 10
        return value

    return tick


def test_manifest_separates_fixed_k_and_evaluation_count(tmp_path: Path) -> None:
    analysis_a, analysis_b = _manifests(tmp_path)

    assert validate_manifest(analysis_a)["status"] == "valid"
    assert validate_manifest(analysis_b)["status"] == "valid"
    assert len(analysis_a["analysis_a"]["configurations"]) == 20
    assert [item["configuration_size"] for item in analysis_a["analysis_a"]["configurations"]] == [
        1
    ] * 20
    assert [item["configuration_size"] for item in analysis_b["analysis_b"]["configurations"]] == [
        0,
        1,
        5,
        10,
        20,
        50,
    ]
    assert analysis_a["binding_digest"] == analysis_b["binding_digest"]
    assert analysis_a["query_subset_size"] == 128
    assert analysis_a["query_subset"][0]["source_index"] == 0
    assert analysis_a["query_subset"][-1]["source_index"] == 9999


def test_manifest_tampering_and_duplicate_candidate_are_rejected(tmp_path: Path) -> None:
    manifest, _ = _manifests(tmp_path)
    tampered = json.loads(json.dumps(manifest))
    tampered["analysis_a"]["configurations"][0]["candidate_ids"] = []
    with pytest.raises(WhatIfCostError, match="semantic digest"):
        validate_manifest(tampered)

    duplicate = json.loads(json.dumps(manifest))
    duplicate["candidate_universe"]["definitions"].append(
        duplicate["candidate_universe"]["definitions"][0]
    )
    duplicate["semantic_digest"] = semantic_digest(
        {key: value for key, value in duplicate.items() if key != "semantic_digest"}
    )
    with pytest.raises(WhatIfCostError, match="candidate definitions"):
        validate_manifest(duplicate)


def test_physical_and_catalogless_runs_account_for_all_stages(tmp_path: Path) -> None:
    manifest, _ = _manifests(tmp_path)
    physical_events = tmp_path / "physical.events.jsonl"
    catalogless_events = tmp_path / "catalogless.events.jsonl"
    physical = run_physical_arm(
        manifest,
        FakePhysical(),
        run_id="offline-run",
        repetition=1,
        events_path=physical_events,
        clock=_clock(),
    )
    catalogless = run_catalogless_arm(
        manifest,
        FakeCatalogless(),
        run_id="offline-run",
        repetition=1,
        events_path=catalogless_events,
        clock=_clock(),
    )

    assert physical["status"] == catalogless["status"] == "complete"
    assert physical["configuration_count"] == catalogless["configuration_count"] == 20
    assert physical["explain_calls"] == catalogless["explain_calls"] == 20 * 128
    assert physical["analyze_calls"] == 20
    assert catalogless["analyze_calls"] == 0
    comparison = compare_costs(physical, catalogless)
    assert len(comparison["checkpoints"]) == 20
    assert read_events(physical_events)
    assert read_events(catalogless_events)


def test_partial_runs_are_not_summarized(tmp_path: Path) -> None:
    manifest, _ = _manifests(tmp_path)
    events_path = tmp_path / "partial.events.jsonl"
    with pytest.raises(AdapterExecutionError):
        run_catalogless_arm(
            manifest,
            FakeCatalogless(fail="explain"),
            run_id="partial-run",
            repetition=1,
            events_path=events_path,
            failure_output=tmp_path / "failure",
            clock=_clock(),
        )
    events = read_events(events_path)
    assert any(event["status"] == "failed" for event in events)
    with pytest.raises(IncompleteRunError):
        validate_events(
            manifest,
            events,
            run_id="partial-run",
            arm="catalogless",
            repetition=1,
        )
    failure = next((tmp_path / "failure").glob("failures/*.json"))
    value = json.loads(failure.read_text(encoding="utf-8"))
    assert value["status"] == "failed"
    assert value["completed_explain_calls"] == 0


def test_cleanup_failure_is_separate_from_primary_failure(tmp_path: Path) -> None:
    manifest, _ = _manifests(tmp_path)
    with pytest.raises(AdapterExecutionError) as raised:
        run_physical_arm(
            manifest,
            FakePhysical(fail="analyze", cleanup_fail=True),
            run_id="cleanup-run",
            repetition=1,
            events_path=tmp_path / "cleanup.events.jsonl",
            failure_output=tmp_path / "failure",
            clock=_clock(),
        )
    assert "failed at analyze" in str(raised.value.primary)
    assert raised.value.cleanup is not None
    failure = next((tmp_path / "failure").glob("failures/*.json"))
    value = json.loads(failure.read_text(encoding="utf-8"))
    assert value["primary_exception"]["message"] == "failed at analyze"
    assert value["cleanup_exception"]["message"] == "cleanup failed"


def test_integration_preflight_plan_is_nonexecuting(tmp_path: Path) -> None:
    manifest, _ = _manifests(tmp_path)
    plan = integration_preflight_plan(manifest)
    assert plan["status"] == "not-executed"
    assert plan["live_database_connections"] == 0
    assert plan["authorization_required"] is True
    assert plan["fixture_scope"]["candidate_count"] == 2
    assert plan["fixture_scope"]["query_count"] == 3
