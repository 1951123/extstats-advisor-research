from __future__ import annotations

import multiprocessing as mp
import time
from pathlib import Path

import pytest

import extstats_advisor_research.rq5_production_exact_truth_cost as truth_cost


def _source() -> dict[str, object]:
    return {
        "dataset_id": truth_cost.DATASET_ID,
        "benchmark_id": truth_cost.DATASET_ID,
        "dataset_content_identity": "a" * 64,
        "relation": "public.census13",
        "schema_contract_id": "arecel-census13-postgres-schema-v1",
        "workload_id": "arecel_census13_test_v1",
        "workload_sha256": "b" * 64,
        "workload_query_count": 10_000,
        "canonical_workload_sha256": "c" * 64,
    }


def _preflight() -> dict[str, object]:
    value: dict[str, object] = {
        "format_version": truth_cost.PREFLIGHT_FORMAT,
        "experiment_id": truth_cost.EXPERIMENT_ID,
        "status": "ready-to-run",
        "formal_execution_started": False,
        "research_commit_sha": "d" * 40,
        "advisor_sha": truth_cost.FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": truth_cost.FROZEN_STOCK_POSTGRES_SHA,
        "stock_postgres_version": truth_cost.STOCK_POSTGRES_VERSION,
        "protocol_path": truth_cost.PROTOCOL_PATH,
        "protocol_semantic_digest": truth_cost.PROTOCOL_DIGEST,
        "dataset_id": truth_cost.DATASET_ID,
        "dataset": _source(),
        "authoritative_truth": {
            "path": truth_cost.TRUTH_SOURCE_PATH,
            "sha256": "f" * 64,
            "kind": truth_cost.TRUTH_SOURCE_KIND,
            "query_count": 10_000,
        },
        "sample_rows": truth_cost.SAMPLE_ROWS,
        "sample_seed": truth_cost.SAMPLE_SEED,
        "repetitions": 1,
        "stage_hard_cap_seconds": truth_cost.STAGE_HARD_CAP_SECONDS,
        "no_planner_work": True,
        "no_patched_postgres": True,
        "no_truth_artifact_tracked": True,
        "preflight_path": "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json",
    }
    value["semantic_digest"] = truth_cost.semantic_digest(truth_cost._without_digest(value))
    return value


def _sleeping_worker(events, payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
    time.sleep(payload["seconds"])


def _fast_worker(events, _payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
    events.put({"signal": "TRUTH_FINISHED"})
    events.put({"signal": "RESULT", "result": {"status": "success"}})


def test_protocol_is_preregistered_and_digest_is_stable():
    root = Path(__file__).resolve().parents[1]
    result = truth_cost.validate_protocol(
        root / truth_cost.PROTOCOL_PATH,
        research_root=root,
    )
    assert result["status"] == "valid"
    assert (
        result["semantic_digest"]
        == "52af2af04363b9642a5720ad2412028228446fc32b22804330399acfba32ecde"
    )


def test_timed_acquirer_delegates_once_and_transparently(monkeypatch):
    calls: list[tuple[object, ...]] = []
    sentinel = (object(), object())

    def frozen_collect(self, *args):
        calls.append(args)
        return sentinel

    monkeypatch.setattr(
        truth_cost._FrozenPostgresSnapshotAcquirer, "_collect_truth", frozen_collect
    )
    acquirer = object.__new__(truth_cost.TimedPostgresSnapshotAcquirer)
    acquirer.truth_elapsed_seconds = None
    acquirer.truth_started = False
    acquirer.truth_finished = False
    signals: list[str] = []
    acquirer._stage_signal = signals.append

    result = acquirer._collect_truth("connection", "workload", "schema", "sql")

    assert result is sentinel
    assert len(calls) == 1
    assert acquirer.truth_started is True
    assert acquirer.truth_finished is True
    assert acquirer.truth_elapsed_seconds is not None
    assert signals == ["TRUTH_STARTED", "TRUTH_FINISHED"]


def test_timing_metadata_is_capture_total_separate_from_truth_timer():
    class FakeAcquirer:
        truth_elapsed_seconds = 0.01
        truth_finished = True

        def capture_with_ground_truth(self, request, workload):
            time.sleep(0.01)
            return "snapshot", "truths"

    snapshot, truths, timing = truth_cost.capture_with_timing(FakeAcquirer(), None, None)
    assert (snapshot, truths) == ("snapshot", "truths")
    assert timing["capture_with_ground_truth_total_elapsed_seconds"] >= 0.01
    assert timing["production_exact_truth_acquisition_elapsed_seconds"] == 0.01
    assert timing["non_truth_capture_component_seconds"] >= 0
    assert "derived difference" in timing["non_truth_capture_component_semantics"]


def test_truth_comparison_reports_counts_without_payload():
    truths = [
        {"query_id": "q1", "cardinality": 4},
        {"query_id": "q2", "cardinality": 0},
    ]
    result = truth_cost.compare_truths_to_authoritative(truths, {"q1": 4, "q2": 0})
    assert result == {
        "compared_count": 2,
        "mismatch_count": 0,
        "missing_count": 0,
        "extra_count": 0,
        "passed": True,
    }


def test_truth_mismatch_and_count_drift_fail_closed():
    result = truth_cost.compare_truths_to_authoritative(
        [{"query_id": "q1", "cardinality": 9}], {"q1": 4, "q2": 0}
    )
    assert result["passed"] is False
    assert result["mismatch_count"] == 1
    assert result["missing_count"] == 1


def test_process_timeout_terminates_worker_and_is_not_publishable():
    result = truth_cost.run_truth_stage_with_timeout(
        _sleeping_worker,
        {"seconds": 2.0},
        timeout_seconds=0.5,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "timeout"
    assert result["completed_truth_query_count"] is None
    assert truth_cost.timeout_result_is_publishable(result) is False


def test_fast_worker_can_publish_result():
    result = truth_cost.run_truth_stage_with_timeout(
        _fast_worker,
        {},
        timeout_seconds=1.0,
        context=mp.get_context("spawn"),
    )
    assert result == {"status": "success"}
    assert truth_cost.timeout_result_is_publishable(result) is True


def test_preflight_source_and_pin_drift_is_rejected_before_live_work():
    preflight = _preflight()
    actual = _source()
    actual["workload_sha256"] = "9" * 64
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError):
        truth_cost.validate_runtime_pins(
            preflight=preflight,
            advisor_sha=truth_cost.FROZEN_ADVISOR_SHA,
            stock_postgres_sha=truth_cost.FROZEN_STOCK_POSTGRES_SHA,
            actual_source=actual,
        )
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError):
        truth_cost.validate_runtime_pins(
            preflight=preflight,
            advisor_sha="0" * 40,
            stock_postgres_sha=truth_cost.FROZEN_STOCK_POSTGRES_SHA,
            actual_source=_source(),
        )


def test_runner_source_drift_happens_before_worker(monkeypatch, tmp_path):
    root = tmp_path
    (root / "paper").mkdir()
    (root / "experiments").mkdir()
    preflight_path = root / "experiments/preflight.json"
    preflight = _preflight()
    preflight["preflight_path"] = "experiments/preflight.json"
    preflight["protocol_semantic_digest"] = truth_cost.PROTOCOL_DIGEST
    preflight["semantic_digest"] = truth_cost.semantic_digest(truth_cost._without_digest(preflight))
    truth_cost.write_json(preflight_path, preflight)
    monkeypatch.setattr(
        truth_cost,
        "_protocol_value",
        lambda _root: {"semantic_digest": truth_cost.PROTOCOL_DIGEST},
    )
    monkeypatch.setattr(truth_cost, "_research_head_sha", lambda _root: "d" * 40)
    monkeypatch.setattr(truth_cost, "verify_git_sha", lambda _path, expected: expected)
    monkeypatch.setattr(
        truth_cost,
        "_dataset_source_spec",
        lambda *_args: {**_source(), "workload_sha256": "9" * 64},
    )
    called = False

    def worker(_events, _payload):
        nonlocal called
        called = True

    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError):
        truth_cost.run_canary(
            research_root=root,
            stock_dsn="not-used",
            preflight=Path("experiments/preflight.json"),
            output=Path("experiments/result.json"),
            advisor_root=Path("advisor"),
            stock_postgres_root=Path("postgres"),
            worker=worker,
        )
    assert called is False


def test_artifact_assembly_rejects_sensitive_payload_and_validates():
    preflight = _preflight()
    source = _source()
    value = truth_cost.assemble_canary_artifact(
        preflight=preflight,
        research_commit_sha=preflight["research_commit_sha"],
        advisor_sha=truth_cost.FROZEN_ADVISOR_SHA,
        stock_postgres_sha=truth_cost.FROZEN_STOCK_POSTGRES_SHA,
        postgres_version=truth_cost.STOCK_POSTGRES_VERSION,
        dataset=source,
        workload={
            key: source[key] for key in ("workload_id", "workload_sha256", "workload_query_count")
        },
        fresh_database_setup_seconds=1.0,
        data_load_seconds=2.0,
        timing={
            "capture_with_ground_truth_total_elapsed_seconds": 3.0,
            "production_exact_truth_acquisition_elapsed_seconds": 2.5,
            "non_truth_capture_component_seconds": 0.5,
            "non_truth_capture_component_semantics": "derived difference; not an independently timed stage",
        },
        eligible_query_count=2,
        truth_record_count=2,
        truth_comparison={"compared_count": 2, "mismatch_count": 0, "passed": True},
        snapshot_semantic_digest="1" * 64,
        ground_truth_semantic_digest="2" * 64,
        source_view_contract="repeatable-read-read-only acquisition transaction",
        source_view_token_present=True,
        snapshot_and_truth_source_view_match=True,
        cleanup_passed=True,
    )
    assert truth_cost.validate_artifact(value, expected_producer_sha="d" * 40)["status"] == "valid"
    assert "truths" not in value
    value["truths"] = []
    value["semantic_digest"] = truth_cost.semantic_digest(truth_cost._without_digest(value))
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError, match="truth payload"):
        truth_cost.validate_artifact(value)


def test_artifact_rejects_failed_comparison():
    preflight = _preflight()
    source = _source()
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError, match="comparison"):
        truth_cost.assemble_canary_artifact(
            preflight=preflight,
            research_commit_sha=preflight["research_commit_sha"],
            advisor_sha=truth_cost.FROZEN_ADVISOR_SHA,
            stock_postgres_sha=truth_cost.FROZEN_STOCK_POSTGRES_SHA,
            postgres_version=truth_cost.STOCK_POSTGRES_VERSION,
            dataset=source,
            workload={
                key: source[key]
                for key in ("workload_id", "workload_sha256", "workload_query_count")
            },
            fresh_database_setup_seconds=1.0,
            data_load_seconds=2.0,
            timing={
                "capture_with_ground_truth_total_elapsed_seconds": 3.0,
                "production_exact_truth_acquisition_elapsed_seconds": 2.5,
                "non_truth_capture_component_seconds": 0.5,
                "non_truth_capture_component_semantics": "derived difference; not an independently timed stage",
            },
            eligible_query_count=2,
            truth_record_count=2,
            truth_comparison={"compared_count": 2, "mismatch_count": 1, "passed": False},
            snapshot_semantic_digest="1" * 64,
            ground_truth_semantic_digest="2" * 64,
            source_view_contract="repeatable-read-read-only acquisition transaction",
            source_view_token_present=True,
            snapshot_and_truth_source_view_match=True,
            cleanup_passed=True,
        )
