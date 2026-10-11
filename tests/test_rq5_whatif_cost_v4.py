from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq5_whatif_cost import (
    WhatIfCostError,
    read_events,
    run_physical_arm,
    validate_events,
    validate_manifest,
)
from extstats_advisor_research.rq5_whatif_cost_postgres import (
    LiveAdapterConfig,
    LiveAdapterError,
    validate_inplace_database_name,
    validate_live_config,
)

ROOT = Path(__file__).resolve().parents[1]
V4_MANIFEST = (
    ROOT / "experiments/rq5-whatif-evaluation-cost-v4/manifests/analysis-a-manifest-v1.json"
)


def _manifest() -> dict:
    return json.loads(V4_MANIFEST.read_text(encoding="utf-8"))


class FakeInPlacePhysical:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _run(self, name: str, count: int) -> int:
        self.calls.append(name)
        return count

    def prepare_run(self) -> int:
        return self._run("baseline-initialization", 1)

    def create_statistics(self, configuration: dict) -> int:
        return self._run("create-alter-statistics", len(configuration["candidate_ids"]))

    def analyze(self, configuration: dict) -> int:
        return self._run("analyze", 1)

    def explain(self, configuration: dict, query_ids: list[str]) -> int:
        return self._run("explain", len(query_ids))

    def drop_statistics(self, configuration: dict) -> int:
        return self._run("drop-statistics", len(configuration["candidate_ids"]))

    def verify_post_drop(self, configuration: dict) -> int:
        return self._run("post-drop-verification", 1)

    def cleanup(self) -> int:
        return self._run("arm-cleanup", 1)


def _clock():
    value = 0

    def tick() -> int:
        nonlocal value
        value += 10
        return value

    return tick


def test_v4_manifest_is_independent_and_valid() -> None:
    manifest = _manifest()
    result = validate_manifest(manifest)
    assert result["format_version"] == "rq5-whatif-evaluation-cost-manifest-v4"
    assert manifest["physical_reset_strategy"]["clone_per_configuration"] is False
    assert manifest["analysis_a"]["configurations"][0]["configuration_id"] == "A-singleton-01"


def test_v4_physical_runner_reuses_one_lifecycle_without_clone_stages(tmp_path: Path) -> None:
    manifest = _manifest()
    adapter = FakeInPlacePhysical()
    result = run_physical_arm(
        manifest,
        adapter,
        run_id="v4-offline",
        repetition=1,
        events_path=tmp_path / "events.jsonl",
        clock=_clock(),
    )
    assert result["status"] == "complete"
    assert adapter.calls.count("baseline-initialization") == 1
    assert adapter.calls.count("analyze") == 20
    assert "clone-create" not in adapter.calls
    assert "clone-destroy" not in adapter.calls
    events = read_events(tmp_path / "events.jsonl")
    assert all(event["protocol_format"] == "rq5-whatif-evaluation-cost-v4" for event in events)
    assert all(event["stage"] != "clone-create" for event in events)
    assert (
        sum(event["operation_count"] for event in events if event["stage"] == "explain") == 20 * 128
    )


def test_v4_missing_post_drop_event_is_rejected() -> None:
    manifest = _manifest()
    events = []
    # A v3 event stream cannot satisfy the v4 protocol identity, even when its
    # operation names otherwise look similar.
    with pytest.raises(WhatIfCostError):
        validate_events(
            manifest,
            events,
            run_id="v4-offline",
            arm="physical",
            repetition=1,
        )


def test_inplace_database_namespace_is_strict() -> None:
    assert validate_inplace_database_name("rq5wc_v4_preflight") == "rq5wc_v4_preflight"
    for value in ("rq5wc_old", "extstats_stock", "rq5wc_v4_bad-name"):
        with pytest.raises(LiveAdapterError):
            validate_inplace_database_name(value)


def test_inplace_live_config_accepts_only_explicit_v4_target(tmp_path: Path) -> None:
    stock_sha = "0d1c00c624fa7367d4a895f44381887757289682"
    patched_sha = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
    stock_identity = tmp_path / "stock.json"
    patched_identity = tmp_path / "patched.json"
    stock_identity.write_text(
        json.dumps({"source_commit_sha": stock_sha, "source_dirty": False}), encoding="utf-8"
    )
    patched_identity.write_text(
        json.dumps({"source_commit_sha": patched_sha, "source_dirty": False}), encoding="utf-8"
    )
    common = {
        "stock_admin_dsn": "host=127.0.0.1 port=55432 dbname=postgres",
        "patched_dsn": "host=127.0.0.1 port=55433 dbname=extstats_lab",
        "advisor_root": tmp_path,
        "snapshot_path": tmp_path,
        "candidate_universe_path": tmp_path / "candidates.json",
        "workload_path": tmp_path / "workload.json",
        "stock_identity_path": stock_identity,
        "patched_identity_path": patched_identity,
        "output_dir": tmp_path / "output",
        "run_id": "v4-config",
    }
    config = LiveAdapterConfig(
        stock_dsn="host=127.0.0.1 port=55432 dbname=rq5wc_v4_r1",
        **common,
    )
    result = validate_live_config(config, inplace_physical=True)
    assert result["inplace_physical_database"] is True
    unsafe = LiveAdapterConfig(
        stock_dsn="host=127.0.0.1 port=55432 dbname=extstats_stock",
        **common,
    )
    with pytest.raises(LiveAdapterError):
        validate_live_config(unsafe, inplace_physical=True)
