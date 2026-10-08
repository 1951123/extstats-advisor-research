"""Census13 production-exact truth-cost canary.

This module preregisters and hardens a future, stock-only canary.  The Phase A
surface is deliberately useful without PostgreSQL: protocol/preflight
validation, truth comparison, timing-boundary instrumentation, worker timeout
handling, and artifact validation are all testable offline.  The live runner
is not invoked by this module's tests.
"""

from __future__ import annotations

import multiprocessing as mp
import shutil
import subprocess
import time
import traceback
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from queue import Empty
from typing import Any

from .arecel_truth import authoritative_truth_spec, observation_records
from .datasets import census13
from .pins import verify_git_sha, verify_research_repository
from .postgres.loader import load_census13
from .provenance import read_json, reject_credentials, semantic_digest, sha256_file, write_json
from .rq5_snapshot_footprint import (
    _canonical_repo_path,
    _database_cleanup_verified,
    _dataset_source_spec,
    _git_status_entries,
)
from .rq5_static_deployment_cost import (
    _create_database,
    _drop_database,
    _psycopg,
)
from .system_freeze_v2 import FROZEN_ADVISOR_SHA, FROZEN_STOCK_POSTGRES_SHA

try:  # Keep offline validation importable when the optional Advisor package is absent.
    from extstats_advisor.dbms.postgres.acquisition import (
        PostgresSnapshotAcquirer as _FrozenPostgresSnapshotAcquirer,
    )
except ImportError:  # pragma: no cover - exercised by environments without the optional package.

    class _FrozenPostgresSnapshotAcquirer:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RQ5ProductionExactTruthValidationError(
                "frozen Advisor package is required for the live truth canary"
            )

        def _collect_truth(self, *_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
            raise RQ5ProductionExactTruthValidationError(
                "frozen Advisor package is required for the live truth canary"
            )


FORMAT_VERSION = "rq5-production-exact-truth-cost-census13-v1"
PROTOCOL_FORMAT = "rq5-production-exact-truth-cost-census13-protocol-v1"
PREFLIGHT_FORMAT = "rq5-production-exact-truth-cost-census13-preflight-v1"
PROTOCOL_PATH = "paper/rq5-production-exact-truth-cost-census13-protocol-v1.json"
PROTOCOL_DIGEST = "52af2af04363b9642a5720ad2412028228446fc32b22804330399acfba32ecde"
EXPERIMENT_ID = "rq5-production-exact-truth-cost-census13-v1"
DATASET_ID = "arecel-census13"
REPETITION_COUNT = 1
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STAGE_HARD_CAP_SECONDS = 300.0
STOCK_POSTGRES_VERSION = "16.14"
TRUTH_SOURCE_PATH = "truth/arecel/census13/authoritative-cardinality-observations-v1.json"
TRUTH_SOURCE_KIND = "authoritative-external-exact"
TRUTH_QUERY_COUNT = 10_000


class RQ5ProductionExactTruthValidationError(ValueError):
    """Raised when canary provenance, timing, or artifact semantics drift."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ5ProductionExactTruthValidationError(message)


def _without_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _research_head_sha(root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    _require(completed.returncode == 0, "could not resolve truth-cost research HEAD")
    head = completed.stdout.strip()
    _require(len(head) == 40, "truth-cost research HEAD is not a full SHA")
    return head


def _sha(value: Any, label: str) -> str:
    _require(isinstance(value, str) and len(value) == 64, f"{label} must be a SHA256 digest")
    return value


def _protocol_value(root: Path) -> dict[str, Any]:
    value = read_json(root / PROTOCOL_PATH)
    validate_protocol_value(value)
    return value


def validate_protocol_value(value: Mapping[str, Any]) -> dict[str, Any]:
    _require(value.get("format_version") == PROTOCOL_FORMAT, "unsupported truth-cost protocol")
    _require(value.get("experiment_id") == EXPERIMENT_ID, "truth-cost experiment ID drifted")
    _require(value.get("status") == "preregistered", "truth-cost protocol is not preregistered")
    _require(
        value.get("dataset_id") == DATASET_ID and value.get("datasets") == [DATASET_ID],
        "truth-cost protocol must cover Census13 only",
    )
    _require(value.get("repetitions") == REPETITION_COUNT, "truth-cost repetition count drifted")
    systems = value.get("systems")
    _require(isinstance(systems, Mapping), "truth-cost system identity is missing")
    _require(
        systems.get("advisor_sha") == FROZEN_ADVISOR_SHA, "truth-cost Advisor identity drifted"
    )
    _require(
        systems.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA,
        "truth-cost stock PostgreSQL identity drifted",
    )
    _require(
        systems.get("postgres_version") == STOCK_POSTGRES_VERSION,
        "truth-cost PostgreSQL version drifted",
    )
    _require(
        value.get("sample_rows") == SAMPLE_ROWS and value.get("sample_seed") == SAMPLE_SEED,
        "truth-cost sample contract drifted",
    )
    _require(
        value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS,
        "truth-cost hard cap drifted",
    )
    _require(
        value.get("primary_metric") == "production_exact_truth_acquisition_elapsed_seconds",
        "truth-cost primary metric drifted",
    )
    timing = value.get("timing_boundary")
    _require(isinstance(timing, Mapping), "truth-cost timing boundary is missing")
    _require(
        timing.get("method") == "PostgresSnapshotAcquirer._collect_truth",
        "truth-cost timer must bind _collect_truth",
    )
    _require(
        timing.get("clock") == "client-monotonic-wall-clock",
        "truth-cost timer must use a client monotonic clock",
    )
    _require(
        value.get("capture_path") == "TimedPostgresSnapshotAcquirer.capture_with_ground_truth",
        "truth-cost capture path drifted",
    )
    _require(
        value.get("truth_source") == "frozen production-exact count path",
        "truth-cost source semantics drifted",
    )
    timeout = value.get("timeout")
    _require(isinstance(timeout, Mapping), "truth-cost timeout contract is missing")
    _require(
        timeout.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS
        and timeout.get("retry") is False,
        "truth-cost timeout contract drifted",
    )
    _require(value.get("no_planner_work") is True, "truth-cost protocol permits planner work")
    _require(
        value.get("no_patched_postgres") is True, "truth-cost protocol permits patched PostgreSQL"
    )
    return dict(value)


def validate_protocol(path: Path, *, research_root: Path | None = None) -> dict[str, Any]:
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    value = read_json(path)
    expected_path = (root / PROTOCOL_PATH).resolve()
    _require(path.resolve() == expected_path, "truth-cost protocol path is not canonical")
    validate_protocol_value(value)
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "truth-cost protocol digest mismatch",
    )
    _require(
        value.get("semantic_digest") == PROTOCOL_DIGEST,
        "truth-cost protocol semantic identity drifted",
    )
    return {
        "status": "valid",
        "format_version": PROTOCOL_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def _validate_source_spec(spec: Mapping[str, Any]) -> None:
    required = {
        "dataset_id",
        "benchmark_id",
        "dataset_content_identity",
        "relation",
        "schema_contract_id",
        "workload_id",
        "workload_sha256",
        "workload_query_count",
        "canonical_workload_sha256",
    }
    _require(required <= set(spec), "truth-cost source specification is incomplete")
    _require(spec.get("dataset_id") == DATASET_ID, "truth-cost dataset identity drifted")
    _require(spec.get("benchmark_id") == DATASET_ID, "truth-cost benchmark identity drifted")
    _sha(spec.get("dataset_content_identity"), "dataset_content_identity")
    _sha(spec.get("workload_sha256"), "workload_sha256")
    canonical = spec.get("canonical_workload_sha256")
    _require(
        canonical is None or (isinstance(canonical, str) and len(canonical) == 64),
        "canonical workload SHA is invalid",
    )
    _require(isinstance(spec.get("relation"), str) and spec["relation"], "relation is missing")
    _require(
        isinstance(spec.get("schema_contract_id"), str) and spec["schema_contract_id"],
        "schema contract is missing",
    )
    _require(
        isinstance(spec.get("workload_id"), str) and spec["workload_id"], "workload ID is missing"
    )
    _require(
        spec.get("workload_query_count") == TRUTH_QUERY_COUNT, "truth workload query count drifted"
    )


def _truth_source_binding(root: Path) -> dict[str, Any]:
    spec = authoritative_truth_spec(DATASET_ID, root)
    path = Path(spec["observations_path"]).resolve()
    relative = path.relative_to(root.resolve()).as_posix()
    return {
        "path": relative,
        "sha256": spec["observations_sha256"],
        "kind": TRUTH_SOURCE_KIND,
        "query_count": spec["query_count"],
    }


def build_preflight(
    research_root: Path,
    *,
    advisor_root: Path,
    stock_postgres_root: Path,
    output: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Build the future canary preflight without starting PostgreSQL."""
    root = research_root.resolve()
    output_path, output_relative = _canonical_repo_path(
        root,
        output or root / "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json",
        label="truth-cost preflight output",
    )
    _require(not output_path.exists(), f"truth-cost preflight already exists: {output_path}")
    _require(
        not default_artifact_path(root).exists(),
        "truth-cost formal artifact already exists; refusing to start a new campaign",
    )
    protocol = _protocol_value(root)
    research = verify_research_repository(root)
    advisor_sha = verify_git_sha(advisor_root.resolve(), FROZEN_ADVISOR_SHA)
    stock_sha = verify_git_sha(stock_postgres_root.resolve(), FROZEN_STOCK_POSTGRES_SHA)
    source_check = root / ".runtime" / f"rq5-truth-source-{uuid.uuid4().hex}"
    source_check.mkdir(parents=True, exist_ok=False)
    try:
        source = _dataset_source_spec(DATASET_ID, data_root, source_check)
    finally:
        shutil.rmtree(source_check, ignore_errors=True)
    _validate_source_spec(source)
    truth = _truth_source_binding(root)
    _require(truth["query_count"] == TRUTH_QUERY_COUNT, "authoritative truth query count drifted")
    value = {
        "format_version": PREFLIGHT_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "ready-to-run",
        "formal_execution_started": False,
        "research_commit_sha": research["research_commit_sha"],
        "advisor_sha": advisor_sha,
        "stock_postgres_sha": stock_sha,
        "stock_postgres_version": STOCK_POSTGRES_VERSION,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": protocol["semantic_digest"],
        "dataset_id": DATASET_ID,
        "dataset": source,
        "authoritative_truth": truth,
        "sample_rows": SAMPLE_ROWS,
        "sample_seed": SAMPLE_SEED,
        "repetitions": REPETITION_COUNT,
        "stage_hard_cap_seconds": STAGE_HARD_CAP_SECONDS,
        "no_planner_work": True,
        "no_patched_postgres": True,
        "no_truth_artifact_tracked": True,
        "preflight_path": output_relative.as_posix(),
    }
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    write_json(output_path, value)
    return value


def validate_preflight(
    value: Mapping[str, Any], *, protocol: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    _require(value.get("format_version") == PREFLIGHT_FORMAT, "unsupported truth-cost preflight")
    _require(value.get("status") == "ready-to-run", "truth-cost preflight is not ready-to-run")
    _require(
        value.get("formal_execution_started") is False,
        "truth-cost preflight claims execution started",
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "truth-cost preflight digest mismatch",
    )
    _require(
        value.get("protocol_path") == PROTOCOL_PATH,
        "truth-cost preflight protocol path drifted",
    )
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        "truth-cost preflight protocol digest drifted",
    )
    _require(
        value.get("advisor_sha") == FROZEN_ADVISOR_SHA, "truth-cost preflight Advisor SHA drifted"
    )
    _require(
        value.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA,
        "truth-cost preflight stock SHA drifted",
    )
    _require(
        value.get("stock_postgres_version") == STOCK_POSTGRES_VERSION,
        "truth-cost PostgreSQL version drifted",
    )
    _require(value.get("dataset_id") == DATASET_ID, "truth-cost preflight dataset drifted")
    _require(
        isinstance(value.get("research_commit_sha"), str)
        and len(value["research_commit_sha"]) == 40,
        "truth-cost preflight producer SHA is missing",
    )
    _require(
        value.get("sample_rows") == SAMPLE_ROWS and value.get("sample_seed") == SAMPLE_SEED,
        "truth-cost sampling contract drifted",
    )
    _require(
        value.get("repetitions") == REPETITION_COUNT,
        "truth-cost preflight repetition count drifted",
    )
    _require(
        value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS,
        "truth-cost preflight cap drifted",
    )
    _require(
        value.get("no_planner_work") is True and value.get("no_patched_postgres") is True,
        "truth-cost preflight permits forbidden systems",
    )
    _require(
        value.get("no_truth_artifact_tracked") is True,
        "truth-cost preflight permits tracked truth payload",
    )
    _validate_source_spec(value.get("dataset", {}))
    truth = value.get("authoritative_truth", {})
    _require(truth.get("kind") == TRUTH_SOURCE_KIND, "truth source kind drifted")
    _require(truth.get("query_count") == TRUTH_QUERY_COUNT, "truth source query count drifted")
    _sha(truth.get("sha256"), "authoritative truth source")
    if protocol is not None:
        _require(
            value.get("protocol_semantic_digest") == protocol.get("semantic_digest"),
            "preflight protocol binding drifted",
        )
    return {
        "status": "valid",
        "format_version": PREFLIGHT_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def validate_preflight_file(path: Path, research_root: Path) -> dict[str, Any]:
    root = research_root.resolve()
    canonical, relative = _canonical_repo_path(root, path, label="truth-cost preflight")
    value = read_json(canonical)
    result = validate_preflight(value, protocol=_protocol_value(root))
    _require(
        value.get("preflight_path") == relative.as_posix(),
        "truth-cost preflight path does not match its repository path",
    )
    return result


def validate_runtime_pins(
    *,
    preflight: Mapping[str, Any],
    advisor_sha: str,
    stock_postgres_sha: str,
    actual_source: Mapping[str, Any],
) -> None:
    """Fail closed before database creation when live inputs drift."""
    _require(
        advisor_sha == FROZEN_ADVISOR_SHA == preflight.get("advisor_sha"),
        "Advisor source SHA drifted before truth canary",
    )
    _require(
        stock_postgres_sha == FROZEN_STOCK_POSTGRES_SHA == preflight.get("stock_postgres_sha"),
        "stock PostgreSQL SHA drifted before truth canary",
    )
    expected = preflight.get("dataset", {})
    _validate_source_spec(expected)
    _validate_source_spec(actual_source)
    _require(
        dict(actual_source) == dict(expected),
        "Census13 dataset/workload source drifted before database creation",
    )


class TimedPostgresSnapshotAcquirer(_FrozenPostgresSnapshotAcquirer):
    """Time only the frozen ``_collect_truth`` method and delegate its body."""

    def __init__(
        self, *args: Any, stage_signal: Callable[[str], None] | None = None, **kwargs: Any
    ) -> None:
        super().__init__(*args, **kwargs)
        self.truth_elapsed_seconds: float | None = None
        self.truth_started = False
        self.truth_finished = False
        self._stage_signal = stage_signal

    def _signal(self, name: str) -> None:
        if self._stage_signal is not None:
            self._stage_signal(name)

    def _collect_truth(self, connection: Any, workload: Any, schema: Any, sql: Any) -> Any:
        self.truth_started = True
        self._signal("TRUTH_STARTED")
        started = time.monotonic()
        try:
            return super()._collect_truth(connection, workload, schema, sql)
        finally:
            self.truth_elapsed_seconds = time.monotonic() - started
            self.truth_finished = True
            self._signal("TRUTH_FINISHED")


def capture_with_timing(
    acquirer: TimedPostgresSnapshotAcquirer, request: Any, workload: Any
) -> tuple[Any, Any, dict[str, Any]]:
    """Run the real capture API and return timing metadata only."""
    started = time.monotonic()
    snapshot, truths = acquirer.capture_with_ground_truth(request, workload)
    total = time.monotonic() - started
    _require(acquirer.truth_elapsed_seconds is not None, "truth timer did not run")
    _require(acquirer.truth_finished is True, "truth timer did not finish")
    _require(total >= acquirer.truth_elapsed_seconds, "truth timing exceeds total capture timing")
    return (
        snapshot,
        truths,
        {
            "capture_with_ground_truth_total_elapsed_seconds": total,
            "production_exact_truth_acquisition_elapsed_seconds": acquirer.truth_elapsed_seconds,
            "non_truth_capture_component_seconds": total - acquirer.truth_elapsed_seconds,
            "non_truth_capture_component_semantics": "derived difference; not an independently timed stage",
        },
    )


def compare_truths_to_authoritative(truths: Any, expected: Mapping[str, int]) -> dict[str, Any]:
    """Compare IDs/values without returning or serializing the truth payload."""
    records = getattr(truths, "truths", truths)
    actual = {}
    for item in records:
        if hasattr(item, "query_id"):
            query_id = item.query_id
            cardinality = item.cardinality
        else:
            query_id = item.get("query_id")
            cardinality = item.get("cardinality")
        _require(query_id not in actual, "production exact truth contains duplicate query IDs")
        actual[query_id] = cardinality
    mismatches = sum(1 for query_id, value in actual.items() if expected.get(query_id) != value)
    missing = len(set(expected) - set(actual))
    extra = len(set(actual) - set(expected))
    compared = len(set(expected) & set(actual))
    return {
        "compared_count": compared,
        "mismatch_count": mismatches,
        "missing_count": missing,
        "extra_count": extra,
        "passed": compared == len(expected) and mismatches == 0 and missing == 0 and extra == 0,
    }


def _worker_entry(
    target: Callable[[Any, Mapping[str, Any]], None], payload: Mapping[str, Any], events: Any
) -> None:
    try:
        target(events, payload)
    except Exception as exc:  # noqa: BLE001 - propagate arbitrary worker failures to the parent
        events.put({"signal": "ERROR", "error": repr(exc), "traceback": traceback.format_exc()})


def run_truth_stage_with_timeout(
    target: Callable[[Any, Mapping[str, Any]], None],
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float = STAGE_HARD_CAP_SECONDS,
    context: Any | None = None,
    stock_dsn: str | None = None,
    database_name: str | None = None,
    cleanup: Callable[[str], bool] | None = None,
    post_truth_timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """Run a truth worker with explicit phases and parent-owned cleanup."""
    _require(timeout_seconds > 0, "truth timeout must be positive")
    post_timeout = (
        timeout_seconds if post_truth_timeout_seconds is None else post_truth_timeout_seconds
    )
    _require(post_timeout > 0, "post-truth timeout must be positive")
    ctx = context or mp.get_context("spawn")
    events = ctx.Queue()
    process = ctx.Process(target=_worker_entry, args=(target, payload, events))
    process.start()
    phase = "pre_truth"
    ready_seen = False
    database_created = False
    truth_started_at: float | None = None
    post_truth_started_at: float | None = None
    ready_deadline = time.monotonic() + timeout_seconds
    result: dict[str, Any] | None = None

    def terminate_and_cleanup(status: str) -> dict[str, Any]:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        cleanup_passed = True
        if database_name is not None:
            if cleanup is not None:
                cleanup_passed = bool(cleanup(database_name))
            else:
                try:
                    psycopg = _psycopg()
                    if not _database_cleanup_verified(stock_dsn or "", database_name, psycopg):
                        _drop_database(stock_dsn or "", database_name, psycopg)
                        cleanup_passed = _database_cleanup_verified(
                            stock_dsn or "", database_name, psycopg
                        )
                except Exception:  # noqa: BLE001 - timeout cleanup is fail-closed below
                    cleanup_passed = False
        if not cleanup_passed:
            status = "timeout-cleanup-failed"
        return {
            "status": status,
            "completed_truth_query_count": None,
            "timeout_seconds": timeout_seconds,
            "ready_seen": ready_seen,
            "cleanup_passed": cleanup_passed,
        }

    try:
        while True:
            now = time.monotonic()
            if phase == "pre_truth" and now >= ready_deadline:
                return terminate_and_cleanup("timeout-before-truth-stage")
            if (
                phase == "truth"
                and truth_started_at is not None
                and now - truth_started_at >= timeout_seconds
            ):
                return terminate_and_cleanup("timeout")
            if (
                phase == "post_truth"
                and post_truth_started_at is not None
                and now - post_truth_started_at >= post_timeout
            ):
                return terminate_and_cleanup("timeout-after-truth-stage")
            try:
                event = events.get(timeout=0.05)
            except Empty:
                if not process.is_alive():
                    break
                continue
            signal = event.get("signal")
            if signal == "DATABASE_CREATED":
                _require(
                    phase == "pre_truth" and not database_created,
                    "truth worker database-created signal is out of order",
                )
                database_created = True
            elif signal == "READY_FOR_TRUTH":
                _require(
                    phase == "pre_truth" and not ready_seen,
                    "truth worker READY_FOR_TRUTH signal is out of order",
                )
                ready_seen = True
            elif signal == "TRUTH_STARTED":
                _require(
                    phase == "pre_truth" and ready_seen,
                    "truth worker TRUTH_STARTED signal is out of order",
                )
                phase = "truth"
                truth_started_at = time.monotonic()
            elif signal == "TRUTH_FINISHED":
                _require(
                    phase == "truth" and truth_started_at is not None,
                    "truth worker TRUTH_FINISHED signal is out of order",
                )
                phase = "post_truth"
                truth_started_at = None
                post_truth_started_at = time.monotonic()
            elif signal == "RESULT":
                _require(phase == "post_truth", "truth worker RESULT signal is out of order")
                result = dict(event["result"])
                _require(result.get("status") == "success", "truth worker result is not successful")
                phase = "finished"
                break
            elif signal == "ERROR":
                raise RQ5ProductionExactTruthValidationError(
                    f"truth worker failed: {event.get('error')}"
                )
            else:
                raise RQ5ProductionExactTruthValidationError(
                    f"unknown truth worker signal: {signal!r}"
                )
        _require(result is not None, "truth worker exited without a result")
        result["ready_seen"] = ready_seen
        result["phase"] = phase
        return result
    except RQ5ProductionExactTruthValidationError:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        if database_name is not None:
            cleanup_result = terminate_and_cleanup("worker-error")
            if not cleanup_result["cleanup_passed"]:
                raise RQ5ProductionExactTruthValidationError(
                    "truth worker failed and cleanup could not be verified"
                )
        raise
    finally:
        if process.is_alive():
            process.terminate()
        process.join(timeout=5)
        events.close()
        events.join_thread()


def assemble_canary_artifact(
    *,
    preflight: Mapping[str, Any],
    research_commit_sha: str,
    advisor_sha: str,
    stock_postgres_sha: str,
    postgres_version: str,
    dataset: Mapping[str, Any],
    workload: Mapping[str, Any],
    fresh_database_setup_seconds: float,
    data_load_seconds: float,
    timing: Mapping[str, Any],
    eligible_query_count: int,
    truth_record_count: int,
    truth_comparison: Mapping[str, Any],
    snapshot_semantic_digest: str,
    ground_truth_semantic_digest: str,
    source_view_contract: str,
    source_view_token_present: bool,
    snapshot_and_truth_source_view_match: bool,
    cleanup_passed: bool,
) -> dict[str, Any]:
    """Build the compact future formal artifact; reject raw truth payloads."""
    validate_preflight(preflight)
    _require(
        research_commit_sha == preflight.get("research_commit_sha"), "canary producer SHA drifted"
    )
    validate_runtime_pins(
        preflight=preflight,
        advisor_sha=advisor_sha,
        stock_postgres_sha=stock_postgres_sha,
        actual_source=dataset,
    )
    _require(postgres_version == STOCK_POSTGRES_VERSION, "canary PostgreSQL version drifted")
    _require(
        fresh_database_setup_seconds <= STAGE_HARD_CAP_SECONDS,
        "fresh database setup exceeded the hard cap",
    )
    _require(
        data_load_seconds <= STAGE_HARD_CAP_SECONDS, "Census13 data load exceeded the hard cap"
    )
    _require(dataset == preflight["dataset"], "canary dataset provenance drifted")
    for field in ("workload_id", "workload_sha256", "workload_query_count"):
        _require(workload.get(field) == dataset.get(field), f"canary {field} drifted")
    _require(
        eligible_query_count == truth_record_count,
        "truth record count differs from eligible query count",
    )
    _require(truth_comparison.get("passed") is True, "production exact truth comparison failed")
    _require(
        truth_comparison.get("compared_count") == eligible_query_count,
        "truth comparison count drifted",
    )
    _require(
        source_view_token_present and snapshot_and_truth_source_view_match,
        "snapshot/truth source-view binding failed",
    )
    _require(cleanup_passed is True, "canary cleanup did not pass")
    for forbidden in ("truths", "ground_truth_records", "snapshot_payload", "workload_sql", "dsn"):
        _require(forbidden not in timing, f"sensitive canary field is not permitted: {forbidden}")
    _require(
        isinstance(snapshot_semantic_digest, str) and len(snapshot_semantic_digest) == 64,
        "snapshot semantic digest is invalid",
    )
    _require(
        isinstance(ground_truth_semantic_digest, str) and len(ground_truth_semantic_digest) == 64,
        "ground-truth semantic digest is invalid",
    )
    value = {
        "format_version": FORMAT_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "status": "complete",
        "research_commit_sha": research_commit_sha,
        "advisor_sha": advisor_sha,
        "stock_postgres_sha": stock_postgres_sha,
        "postgres_version": postgres_version,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": preflight["protocol_semantic_digest"],
        "preflight_path": preflight.get("preflight_path"),
        "preflight_semantic_digest": preflight["semantic_digest"],
        "dataset": dict(dataset),
        "workload": dict(workload),
        "repetition_count": REPETITION_COUNT,
        "fresh_database_setup_seconds": fresh_database_setup_seconds,
        "data_load_seconds": data_load_seconds,
        **dict(timing),
        "eligible_query_count": eligible_query_count,
        "truth_record_count": truth_record_count,
        "truth_comparison": dict(truth_comparison),
        "source_view_contract": source_view_contract,
        "source_view_token_present": source_view_token_present,
        "snapshot_and_truth_source_view_match": snapshot_and_truth_source_view_match,
        "snapshot_semantic_digest": snapshot_semantic_digest,
        "ground_truth_semantic_digest": ground_truth_semantic_digest,
        "cleanup_passed": cleanup_passed,
        "no_planner_work": True,
        "no_patched_postgres": True,
        "no_truth_payload_tracked": True,
        "not_refresh": True,
    }
    reject_credentials(value)
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    validate_artifact(value, expected_producer_sha=research_commit_sha)
    return value


def validate_artifact(
    value: Mapping[str, Any], *, expected_producer_sha: str | None = None
) -> dict[str, Any]:
    _require(value.get("format_version") == FORMAT_VERSION, "unsupported truth-cost artifact")
    _require(value.get("status") == "complete", "truth-cost artifact is not complete")
    _require(value.get("experiment_id") == EXPERIMENT_ID, "truth-cost artifact ID drifted")
    producer = value.get("research_commit_sha")
    _require(
        isinstance(producer, str) and len(producer) == 40, "truth-cost producer SHA is missing"
    )
    _require(
        expected_producer_sha is None or producer == expected_producer_sha,
        "truth-cost producer SHA drifted",
    )
    _require(value.get("advisor_sha") == FROZEN_ADVISOR_SHA, "truth-cost Advisor SHA drifted")
    _require(
        value.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA, "truth-cost stock SHA drifted"
    )
    _require(
        value.get("postgres_version") == STOCK_POSTGRES_VERSION,
        "truth-cost PostgreSQL version drifted",
    )
    _require(value.get("protocol_path") == PROTOCOL_PATH, "truth-cost protocol path drifted")
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        "truth-cost protocol digest drifted",
    )
    _require(
        value.get("repetition_count") == REPETITION_COUNT, "truth-cost repetition count drifted"
    )
    _require(value.get("cleanup_passed") is True, "truth-cost cleanup did not pass")
    _require(
        value.get("no_planner_work") is True and value.get("no_patched_postgres") is True,
        "truth-cost artifact permits forbidden work",
    )
    _require(
        value.get("no_truth_payload_tracked") is True, "truth-cost artifact tracks truth payload"
    )
    _require(value.get("not_refresh") is True, "truth-cost artifact is mislabeled as refresh")
    primary = value.get("production_exact_truth_acquisition_elapsed_seconds")
    total = value.get("capture_with_ground_truth_total_elapsed_seconds")
    _require(
        isinstance(primary, (int, float))
        and not isinstance(primary, bool)
        and primary >= 0
        and primary <= STAGE_HARD_CAP_SECONDS,
        "truth-cost primary timing is outside the hard cap",
    )
    _require(
        isinstance(total, (int, float)) and not isinstance(total, bool) and total >= primary,
        "truth-cost total capture timing is invalid",
    )
    _require(
        value.get("truth_comparison", {}).get("mismatch_count") == 0,
        "truth comparison has mismatches",
    )
    _require(
        value.get("truth_comparison", {}).get("passed") is True, "truth comparison did not pass"
    )
    _require(
        value.get("truth_record_count") == value.get("eligible_query_count"), "truth counts drifted"
    )
    _require(
        "truths" not in value and "ground_truth_records" not in value,
        "truth payload leaked into artifact",
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "truth-cost artifact digest mismatch",
    )
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION,
        "semantic_digest": value["semantic_digest"],
    }


def timeout_result_is_publishable(value: Mapping[str, Any]) -> bool:
    """Timeouts are terminal canary outcomes and never complete artifacts."""
    return value.get("status") == "success"


def default_preflight_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"


def default_artifact_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-production-exact-truth-cost-census13-v1.json"


def _verify_canary_formal_tree(
    root: Path, *, preflight_relative: Path, output_relative: Path
) -> str:
    """Allow only the campaign's untracked preflight before live work."""
    expected_preflight = Path(
        "experiments/rq5-production-exact-truth-cost-census13-preflight-v1.json"
    )
    expected_output = Path("experiments/rq5-production-exact-truth-cost-census13-v1.json")
    _require(
        preflight_relative == expected_preflight,
        "truth-cost preflight path is not the canonical campaign preflight",
    )
    _require(
        output_relative == expected_output,
        "truth-cost output path is not the canonical campaign artifact",
    )
    entries = _git_status_entries(root)
    _require(
        all(status == "??" for status, _path in entries),
        "truth-cost formal run requires no tracked or staged working-tree changes",
    )
    _require(
        [path for _status, path in entries] == [expected_preflight.as_posix()],
        "truth-cost formal run permits only the canonical untracked preflight",
    )
    _require(
        not (root / expected_output).exists(),
        "truth-cost formal artifact already exists; refusing to rerun campaign",
    )
    return _research_head_sha(root)


def _census_truth_worker(events: Any, payload: Mapping[str, Any]) -> None:
    """Future live worker; never called by Phase A tests."""
    from extstats_advisor.dbms.base import AcquisitionRequest, SamplePolicy
    from extstats_advisor.snapshot.model import Workload

    dataset_module = census13
    psycopg = _psycopg()
    database_name = str(payload["database_name"])
    created = False
    cleaned = False
    data_root = Path(payload["data_root"]) if payload.get("data_root") else None
    temporary = Path(payload["temporary_root"])
    temporary.mkdir(parents=True, exist_ok=False)
    try:
        workload_path = temporary / "workload.json"
        source_probe = temporary / "source-probe"
        source_probe.mkdir()
        actual_source = _dataset_source_spec(DATASET_ID, data_root, source_probe)
        _require(actual_source == payload["source_spec"], "worker source identity drifted")
        workload = dataset_module.extract_workload(workload_path, data_root, "test")
        workload_identity = {
            "workload_id": workload["workload_id"],
            "workload_sha256": workload["sha256"],
            "workload_query_count": workload["query_count"],
        }
        _require(
            all(workload_identity[key] == payload["source_spec"][key] for key in workload_identity),
            "worker workload identity drifted",
        )
        setup_started = time.monotonic()
        target_dsn = _create_database(str(payload["stock_dsn"]), database_name, psycopg)
        created = True
        events.put({"signal": "DATABASE_CREATED"})
        setup_elapsed = time.monotonic() - setup_started
        _require(
            setup_elapsed <= STAGE_HARD_CAP_SECONDS, "fresh database setup exceeded the hard cap"
        )
        load_started = time.monotonic()
        load = payload["loader"](
            target_dsn,
            data_root=data_root,
            reset_disposable=False,
            statistics_target=100,
            seed_identifier=123,
        )
        load_elapsed = time.monotonic() - load_started
        _require(load_elapsed <= STAGE_HARD_CAP_SECONDS, "Census13 data load exceeded the hard cap")
        _require(load.get("validated") is True, "Census13 loader did not validate")
        _require(
            load.get("physical_extended_statistics_count", 0) == 0,
            "loader left extended statistics",
        )
        events.put({"signal": "READY_FOR_TRUTH"})
        frozen_workload = Workload.from_dict(read_json(workload_path))
        request = AcquisitionRequest(
            dataset_module.RELATION,
            SamplePolicy(SAMPLE_ROWS, seed=SAMPLE_SEED),
        )
        acquirer = TimedPostgresSnapshotAcquirer(
            target_dsn,
            stage_signal=lambda signal: events.put({"signal": signal}),
        )
        snapshot, ground_truth, timing = capture_with_timing(acquirer, request, frozen_workload)
        expected = observation_records(Path(payload["truth_path"]))
        comparison = compare_truths_to_authoritative(ground_truth, expected)
        _require(
            comparison["passed"] is True, "production exact truth did not match Census13 labels"
        )
        source_view_token = ground_truth.source.source_view_token
        _require(
            isinstance(source_view_token, str) and source_view_token, "source view token is missing"
        )
        snapshot_token = snapshot.semantic_provenance.get("source_view_token")
        _require(snapshot_token == source_view_token, "snapshot/truth source-view token mismatch")
        source_view_contract = snapshot.semantic_provenance.get("source_view_contract")
        _require(
            source_view_contract == "postgresql-pg-current-snapshot-v1",
            "source-view contract drifted",
        )
        _drop_database(str(payload["stock_dsn"]), database_name, psycopg)
        cleaned = True
        _require(
            _database_cleanup_verified(str(payload["stock_dsn"]), database_name, psycopg),
            "Census13 canary database cleanup could not be verified",
        )
        events.put(
            {
                "signal": "RESULT",
                "result": {
                    "status": "success",
                    "setup_seconds": setup_elapsed,
                    "data_load_seconds": load_elapsed,
                    "timing": timing,
                    "eligible_query_count": comparison["compared_count"],
                    "truth_record_count": len(ground_truth.truths),
                    "truth_comparison": comparison,
                    "snapshot_semantic_digest": snapshot.semantic_digest,
                    "ground_truth_semantic_digest": ground_truth.computed_semantic_digest,
                    "source_view_contract": source_view_contract,
                    "source_view_token_present": True,
                    "snapshot_and_truth_source_view_match": True,
                    "postgres_version": ground_truth.source.server_version,
                    "cleanup_passed": True,
                },
            }
        )
    finally:
        if created and not cleaned:
            _drop_database(str(payload["stock_dsn"]), database_name, psycopg)
            cleaned = True
        shutil.rmtree(temporary, ignore_errors=True)


def run_canary(
    *,
    research_root: Path,
    stock_dsn: str,
    preflight: Path,
    output: Path,
    advisor_root: Path,
    stock_postgres_root: Path,
    data_root: Path | None = None,
    worker: Callable[[Any, Mapping[str, Any]], None] = _census_truth_worker,
) -> dict[str, Any]:
    """Execute a future one-repetition canary with pre-DB provenance gates."""
    root = research_root.resolve()
    preflight_path, preflight_relative = _canonical_repo_path(
        root, preflight, label="truth-cost preflight"
    )
    output_path, output_relative = _canonical_repo_path(root, output, label="truth-cost output")
    research_head = _verify_canary_formal_tree(
        root,
        preflight_relative=preflight_relative,
        output_relative=output_relative,
    )
    validate_preflight_file(preflight_path, root)
    preflight_value = read_json(preflight_path)
    _require(
        research_head == preflight_value["research_commit_sha"], "truth-cost producer SHA drifted"
    )
    advisor_sha = verify_git_sha(advisor_root.resolve(), FROZEN_ADVISOR_SHA)
    stock_sha = verify_git_sha(stock_postgres_root.resolve(), FROZEN_STOCK_POSTGRES_SHA)
    probe = root / ".runtime" / f"rq5-truth-runtime-source-{uuid.uuid4().hex}"
    probe.mkdir(parents=True, exist_ok=False)
    try:
        actual_source = _dataset_source_spec(DATASET_ID, data_root, probe)
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    validate_runtime_pins(
        preflight=preflight_value,
        advisor_sha=advisor_sha,
        stock_postgres_sha=stock_sha,
        actual_source=actual_source,
    )
    truth_path = root / preflight_value["authoritative_truth"]["path"]
    _require(
        sha256_file(truth_path) == preflight_value["authoritative_truth"]["sha256"],
        "authoritative truth source drifted",
    )
    temporary_root = root / ".runtime" / f"rq5-truth-worker-{uuid.uuid4().hex}"
    database_name = f"rq5_truth_census13_{uuid.uuid4().hex[:12]}"
    result = run_truth_stage_with_timeout(
        worker,
        {
            "stock_dsn": stock_dsn,
            "database_name": database_name,
            "data_root": str(data_root.resolve()) if data_root else None,
            "temporary_root": str(temporary_root),
            "source_spec": actual_source,
            "truth_path": str(truth_path),
            "loader": load_census13,
        },
        stock_dsn=stock_dsn,
        database_name=database_name,
    )
    _require(timeout_result_is_publishable(result), "truth canary did not complete successfully")
    value = assemble_canary_artifact(
        preflight=preflight_value,
        research_commit_sha=research_head,
        advisor_sha=advisor_sha,
        stock_postgres_sha=stock_sha,
        postgres_version=result["postgres_version"],
        dataset=actual_source,
        workload={
            key: actual_source[key]
            for key in ("workload_id", "workload_sha256", "workload_query_count")
        },
        fresh_database_setup_seconds=result["setup_seconds"],
        data_load_seconds=result["data_load_seconds"],
        timing=result["timing"],
        eligible_query_count=result["eligible_query_count"],
        truth_record_count=result["truth_record_count"],
        truth_comparison=result["truth_comparison"],
        snapshot_semantic_digest=result["snapshot_semantic_digest"],
        ground_truth_semantic_digest=result["ground_truth_semantic_digest"],
        source_view_contract=result["source_view_contract"],
        source_view_token_present=result["source_view_token_present"],
        snapshot_and_truth_source_view_match=result["snapshot_and_truth_source_view_match"],
        cleanup_passed=result["cleanup_passed"],
    )
    write_json(output_path, value)
    return value
