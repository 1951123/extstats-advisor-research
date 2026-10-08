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


def _post_truth_slow_worker(events, payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
    events.put({"signal": "TRUTH_FINISHED"})
    time.sleep(payload["seconds"])
    events.put({"signal": "RESULT", "result": {"status": "success"}})


def _post_truth_hanging_worker(events, _payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
    events.put({"signal": "TRUTH_FINISHED"})
    time.sleep(2.0)


def _database_truth_timeout_worker(events, _payload):
    events.put({"signal": "DATABASE_CREATED"})
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
    time.sleep(2.0)


def _finish_before_start_worker(events, _payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_FINISHED"})


def _result_before_finish_worker(events, _payload):
    events.put({"signal": "READY_FOR_TRUTH"})
    events.put({"signal": "TRUTH_STARTED"})
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


def test_truth_finished_stops_primary_deadline_before_slow_post_truth_work():
    result = truth_cost.run_truth_stage_with_timeout(
        _post_truth_slow_worker,
        {"seconds": 0.25},
        timeout_seconds=0.5,
        post_truth_timeout_seconds=1.0,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "success"
    assert result["phase"] == "finished"


def test_post_truth_hang_has_distinct_watchdog_status():
    result = truth_cost.run_truth_stage_with_timeout(
        _post_truth_hanging_worker,
        {},
        timeout_seconds=0.5,
        post_truth_timeout_seconds=0.2,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "timeout-after-truth-stage"
    assert result["status"] != "timeout"


def test_truth_timeout_terminates_worker_and_invokes_parent_cleanup():
    cleaned: list[str] = []

    def cleanup(database_name: str) -> bool:
        cleaned.append(database_name)
        return True

    result = truth_cost.run_truth_stage_with_timeout(
        _database_truth_timeout_worker,
        {"database_name": "ignored-by-worker"},
        timeout_seconds=0.3,
        database_name="rq5_truth_timeout_test",
        cleanup=cleanup,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "timeout"
    assert cleaned == ["rq5_truth_timeout_test"]
    assert result["cleanup_passed"] is True


def test_timeout_cleanup_failure_is_not_swallowed():
    result = truth_cost.run_truth_stage_with_timeout(
        _database_truth_timeout_worker,
        {},
        timeout_seconds=0.3,
        database_name="rq5_truth_cleanup_failure",
        cleanup=lambda _database_name: False,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "timeout-cleanup-failed"
    assert result["cleanup_passed"] is False


def test_timeout_before_database_exists_does_not_require_cleanup():
    cleaned: list[str] = []

    def cleanup(database_name: str) -> bool:
        cleaned.append(database_name)
        return True

    result = truth_cost.run_truth_stage_with_timeout(
        _sleeping_worker,
        {"seconds": 2.0},
        timeout_seconds=0.2,
        database_name="rq5_truth_not_created",
        cleanup=cleanup,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "timeout"
    assert result["cleanup_passed"] is True
    assert cleaned == ["rq5_truth_not_created"]


@pytest.mark.parametrize("worker", [_finish_before_start_worker, _result_before_finish_worker])
def test_invalid_truth_signal_order_fails_closed(worker):
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError, match="out of order"):
        truth_cost.run_truth_stage_with_timeout(
            worker,
            {},
            timeout_seconds=1.0,
            context=mp.get_context("spawn"),
        )


def test_fast_worker_can_publish_result():
    result = truth_cost.run_truth_stage_with_timeout(
        _fast_worker,
        {},
        timeout_seconds=1.0,
        context=mp.get_context("spawn"),
    )
    assert result["status"] == "success"
    assert result["ready_seen"] is True
    assert result["phase"] == "finished"
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
    preflight_path = root / (
        "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
    )
    preflight = _preflight()
    preflight["preflight_path"] = (
        "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
    )
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
    monkeypatch.setattr(
        truth_cost,
        "_git_status_entries",
        lambda _root: [
            ("??", "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json")
        ],
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


def test_canary_tree_gate_accepts_only_canonical_preflight(monkeypatch, tmp_path):
    monkeypatch.setattr(
        truth_cost,
        "_git_status_entries",
        lambda _root: [
            ("??", "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json")
        ],
    )
    monkeypatch.setattr(truth_cost, "_research_head_sha", lambda _root: "d" * 40)
    assert (
        truth_cost._verify_canary_formal_tree(
            tmp_path,
            preflight_relative=Path(
                "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
            ),
            output_relative=Path("experiments/rq5-production-exact-truth-cost-census13-v1.json"),
        )
        == "d" * 40
    )


@pytest.mark.parametrize(
    "entries",
    [
        [(" M", "src/changed.py")],
        [("??", "debug.json")],
        [("A ", "staged.json")],
    ],
)
def test_canary_tree_gate_rejects_non_campaign_dirtiness(monkeypatch, tmp_path, entries):
    monkeypatch.setattr(truth_cost, "_git_status_entries", lambda _root: entries)
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError):
        truth_cost._verify_canary_formal_tree(
            tmp_path,
            preflight_relative=Path(
                "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
            ),
            output_relative=Path("experiments/rq5-production-exact-truth-cost-census13-v1.json"),
        )


def test_build_preflight_rejects_existing_formal_output_before_source_work(monkeypatch, tmp_path):
    (tmp_path / "experiments").mkdir()
    (tmp_path / "paper").mkdir()
    output = tmp_path / "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
    (tmp_path / "experiments/rq5-production-exact-truth-cost-census13-v1.json").write_text(
        "{}", encoding="utf-8"
    )
    monkeypatch.setattr(truth_cost, "_protocol_value", lambda _root: {})
    with pytest.raises(truth_cost.RQ5ProductionExactTruthValidationError, match="formal artifact"):
        truth_cost.build_preflight(
            tmp_path,
            advisor_root=tmp_path / "advisor",
            stock_postgres_root=tmp_path / "postgres",
            output=output,
        )


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
