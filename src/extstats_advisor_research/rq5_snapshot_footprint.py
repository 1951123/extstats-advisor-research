"""RQ5 sealed AdvisorSnapshot footprint protocol and harness.

Phase A is entirely offline: it validates the preregistered protocol and
provides mockable helpers for the future stock-only run.  Phase B captures a
validated AdvisorSnapshot in a temporary directory, records only file hashes
and byte counts, and removes the snapshot before producing tracked evidence.
"""

from __future__ import annotations

import json
import math
import stat
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from statistics import median
from typing import Any

from .datasets import census13, dmv11, forest10, power7
from .pins import verify_git_sha, verify_research_repository
from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
from .provenance import read_json, reject_credentials, semantic_digest, sha256_file, write_json
from .rq5_static_deployment_cost import _create_database, _drop_database, _psycopg
from .system_freeze_v2 import FROZEN_ADVISOR_SHA, FROZEN_STOCK_POSTGRES_SHA

FORMAT_VERSION = "rq5-snapshot-footprint-v1"
PREFLIGHT_FORMAT = "rq5-snapshot-footprint-preflight-v1"
PROTOCOL_PATH = "paper/rq5-snapshot-footprint-protocol-v1.json"
PROTOCOL_FORMAT = "rq5-snapshot-footprint-protocol-v1"
PROTOCOL_DIGEST = "e339986dc8960b4632da504d563de9d32a8e08e22762c54033d41a5ce3eb12c9"
EXPERIMENT_ID = "rq5-snapshot-footprint"
ADVISOR_SNAPSHOT_FORMAT = "advisor-snapshot-v1"
STOCK_POSTGRES_VERSION = "16.14"
DATASETS = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
REPETITIONS = (1, 2, 3)
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
LOADER_SEED_IDENTIFIER = 123
STAGE_HARD_CAP_SECONDS = 300.0
KNOWN_FILES = ("manifest.json", "schema.json", "population.json", "workload.json")

DATASET_SPECS = {
    "arecel-census13": (census13, load_census13),
    "arecel-forest10": (forest10, load_forest10),
    "arecel-power7": (power7, load_power7),
    "arecel-dmv11": (dmv11, load_dmv11),
}


class RQ5SnapshotFootprintValidationError(ValueError):
    """Raised when the snapshot footprint protocol or evidence is invalid."""


def default_preflight_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-snapshot-footprint-preflight-v1.json"


def default_formal_path(research_root: Path) -> Path:
    return research_root / "experiments/rq5-snapshot-footprint-v1.json"


def default_raw_path(research_root: Path, dataset_id: str) -> Path:
    return research_root / f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ5SnapshotFootprintValidationError(message)


def _finite(value: Any, label: str) -> float | int:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{label} must be a finite number",
    )
    return value


def _without_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _canonical_repo_path(root: Path, path: Path, *, label: str) -> tuple[Path, Path]:
    canonical_root = root.resolve()
    candidate = path.resolve() if path.is_absolute() else (canonical_root / path).resolve()
    try:
        relative = candidate.relative_to(canonical_root)
    except ValueError as exc:
        raise RQ5SnapshotFootprintValidationError(
            f"snapshot footprint {label} must reside inside the research repository"
        ) from exc
    return candidate, relative


def _protocol(root: Path) -> dict[str, Any]:
    value = read_json(root / PROTOCOL_PATH)
    _require(
        value.get("format_version") == PROTOCOL_FORMAT, "snapshot footprint protocol format drifted"
    )
    _require(
        value.get("status") == "preregistered", "snapshot footprint protocol is not preregistered"
    )
    _require(
        value.get("semantic_digest") == PROTOCOL_DIGEST,
        "snapshot footprint protocol digest drifted",
    )
    _require(
        semantic_digest(_without_digest(value)) == PROTOCOL_DIGEST,
        "snapshot footprint protocol semantic digest mismatch",
    )
    _require(
        value.get("dataset_order") == list(DATASETS), "snapshot footprint dataset order drifted"
    )
    _require(
        value.get("repetitions") == len(REPETITIONS), "snapshot footprint repetition count drifted"
    )
    _require(
        value.get("snapshot_contract", {}).get("sample_rows") == SAMPLE_ROWS,
        "snapshot sample rows drifted",
    )
    _require(
        value.get("snapshot_contract", {}).get("sample_seed") == SAMPLE_SEED,
        "snapshot sample seed drifted",
    )
    _require(
        value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS, "snapshot stage cap drifted"
    )
    return value


def validate_protocol(path: Path) -> dict[str, Any]:
    """Validate the preregistered snapshot-footprint protocol offline."""
    value = read_json(path)
    _require(
        value.get("format_version") == PROTOCOL_FORMAT, "snapshot footprint protocol format drifted"
    )
    _require(
        value.get("status") == "preregistered", "snapshot footprint protocol is not preregistered"
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "snapshot footprint protocol digest mismatch",
    )
    _require(
        value.get("semantic_digest") == PROTOCOL_DIGEST,
        "snapshot footprint protocol semantic digest drifted",
    )
    _require(
        value.get("dataset_order") == list(DATASETS), "snapshot footprint dataset order drifted"
    )
    _require(
        value.get("repetitions") == len(REPETITIONS), "snapshot footprint repetition count drifted"
    )
    _require(
        value.get("snapshot_contract", {}).get("sample_rows") == SAMPLE_ROWS,
        "snapshot sample rows drifted",
    )
    _require(
        value.get("snapshot_contract", {}).get("sample_seed") == SAMPLE_SEED,
        "snapshot sample seed drifted",
    )
    _require(
        value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS, "snapshot stage cap drifted"
    )
    return {
        "status": "valid",
        "format_version": PROTOCOL_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def _dataset_source_spec(
    dataset_id: str, data_root: Path | None, temporary_root: Path
) -> dict[str, Any]:
    _require(dataset_id in DATASET_SPECS, f"unsupported snapshot footprint dataset: {dataset_id}")
    module, _ = DATASET_SPECS[dataset_id]
    metadata = module.inspect(data_root)
    _require(metadata.get("source_present") is True, f"{dataset_id} audited source is unavailable")
    workload_path = temporary_root / f"{dataset_id}-workload.json"
    workload = module.extract_workload(workload_path, data_root, "test")
    _require(workload["query_count"] == 10_000, f"{dataset_id} workload query count drifted")
    return {
        "dataset_id": dataset_id,
        "dataset_content_identity": metadata["dataset_content_identity"],
        "relation": module.RELATION,
        "schema_contract_id": module.SCHEMA_CONTRACT_ID,
        "workload_id": workload["workload_id"],
        "workload_sha256": workload["sha256"],
        "workload_query_count": workload["query_count"],
        "canonical_workload_sha256": metadata.get("canonical_workload_sha256"),
    }


def _all_source_specs(data_root: Path | None) -> list[dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="rq5-snapshot-preflight-") as temporary:
        temporary_root = Path(temporary)
        return [
            _dataset_source_spec(dataset_id, data_root, temporary_root) for dataset_id in DATASETS
        ]


def build_preflight(
    research_root: Path,
    *,
    advisor_root: Path,
    stock_postgres_root: Path,
    output: Path | None = None,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Build an offline preflight; this does not connect to PostgreSQL."""
    root = research_root.resolve()
    output_path, output_relative = _canonical_repo_path(
        root, output or default_preflight_path(root), label="preflight output"
    )
    _require(not output_path.exists(), f"preflight output already exists: {output_path}")
    protocol = _protocol(root)
    research = verify_research_repository(root)
    advisor_sha = verify_git_sha(advisor_root.resolve(), FROZEN_ADVISOR_SHA)
    stock_sha = verify_git_sha(stock_postgres_root.resolve(), FROZEN_STOCK_POSTGRES_SHA)
    source_specs = _all_source_specs(data_root)
    planned = [
        "experiments/rq5-snapshot-footprint-v1.json",
        *[
            f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
            for dataset_id in DATASETS
        ],
    ]
    collisions = [path for path in planned if (root / path).exists()]
    _require(not collisions, f"snapshot footprint output collision: {collisions}")
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
        "dataset_order": list(DATASETS),
        "repetitions": len(REPETITIONS),
        "repetition_order": list(REPETITIONS),
        "sample_rows": SAMPLE_ROWS,
        "sample_seed": SAMPLE_SEED,
        "stage_hard_cap_seconds": STAGE_HARD_CAP_SECONDS,
        "datasets": source_specs,
        "planned_outputs": planned,
        "output_collision_check": {"collisions": [], "checked": planned},
        "no_truth_or_planner_work": True,
        "patched_postgres_required": False,
        "sensitive_snapshot_not_tracked": True,
        "preflight_path": output_relative.as_posix(),
    }
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    write_json(output_path, value)
    return value


def validate_preflight(path: Path, research_root: Path) -> dict[str, Any]:
    canonical_path, _ = _canonical_repo_path(research_root.resolve(), path, label="preflight")
    value = read_json(canonical_path)
    _require(
        value.get("format_version") == PREFLIGHT_FORMAT, "unsupported snapshot footprint preflight"
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "snapshot footprint preflight digest mismatch",
    )
    _require(
        value.get("status") == "ready-to-run", "snapshot footprint preflight is not ready-to-run"
    )
    _require(
        value.get("formal_execution_started") is False,
        "snapshot footprint preflight claims execution started",
    )
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        "snapshot footprint preflight protocol drifted",
    )
    _require(
        value.get("advisor_sha") == FROZEN_ADVISOR_SHA,
        "snapshot footprint preflight Advisor SHA drifted",
    )
    _require(
        value.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA,
        "snapshot footprint preflight stock SHA drifted",
    )
    _require(
        value.get("stock_postgres_version") == STOCK_POSTGRES_VERSION,
        "snapshot footprint preflight PostgreSQL version drifted",
    )
    _require(
        value.get("dataset_order") == list(DATASETS),
        "snapshot footprint preflight dataset order drifted",
    )
    _require(
        value.get("repetitions") == len(REPETITIONS),
        "snapshot footprint preflight repetition count drifted",
    )
    _require(
        value.get("sample_rows") == SAMPLE_ROWS, "snapshot footprint preflight sample rows drifted"
    )
    _require(
        value.get("sample_seed") == SAMPLE_SEED, "snapshot footprint preflight sample seed drifted"
    )
    _require(
        value.get("stage_hard_cap_seconds") == STAGE_HARD_CAP_SECONDS,
        "snapshot footprint preflight cap drifted",
    )
    _require(
        value.get("no_truth_or_planner_work") is True,
        "snapshot footprint preflight claims truth/planner work",
    )
    _require(
        value.get("patched_postgres_required") is False,
        "snapshot footprint preflight requires patched PostgreSQL",
    )
    _require(
        value.get("sensitive_snapshot_not_tracked") is True,
        "snapshot footprint preflight permits tracked snapshot data",
    )
    _require(
        len(value.get("datasets", [])) == len(DATASETS),
        "snapshot footprint preflight dataset count drifted",
    )
    return {
        "status": "valid",
        "format_version": PREFLIGHT_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


def _advisor_validate_snapshot(snapshot_root: Path) -> dict[str, Any]:
    from extstats_advisor.snapshot.bundle import validate_snapshot

    return validate_snapshot(snapshot_root)


def _component_role(relative_path: str) -> str:
    if relative_path in {"manifest.json", "schema.json", "population.json", "workload.json"}:
        return relative_path.removesuffix(".json")
    if relative_path.startswith("samples/") and relative_path.endswith(".arrow"):
        return "sample-payload"
    return "other-validated-component"


def measure_snapshot_footprint(
    snapshot_root: Path,
    *,
    validator: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate a sealed snapshot, then count logical regular-file bytes."""
    original = Path(snapshot_root).expanduser()
    _require(not original.is_symlink(), "snapshot root may not be a symlink")
    root = original.resolve()
    _require(root.is_dir(), f"snapshot root is not a directory: {snapshot_root}")
    validation = (validator or _advisor_validate_snapshot)(root)
    _require(validation.get("format_version") == ADVISOR_SNAPSHOT_FORMAT, "snapshot format drifted")
    semantic = validation.get("semantic_digest")
    _require(
        isinstance(semantic, str) and len(semantic) == 64,
        "validated snapshot lacks semantic digest",
    )

    files: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise RQ5SnapshotFootprintValidationError(
                f"snapshot contains symlink: {path.relative_to(root)}"
            )
        if path.is_dir():
            continue
        mode = path.stat().st_mode
        _require(
            stat.S_ISREG(mode), f"snapshot contains non-regular file: {path.relative_to(root)}"
        )
        relative = path.relative_to(root).as_posix()
        logical_bytes = path.stat().st_size
        files.append(
            {
                "relative_path": relative,
                "logical_bytes": logical_bytes,
                "sha256": sha256_file(path),
                "component_role": _component_role(relative),
            }
        )
    paths = {item["relative_path"] for item in files}
    _require(set(KNOWN_FILES).issubset(paths), "validated snapshot lacks a required JSON component")
    sample_total = sum(
        item["logical_bytes"] for item in files if item["component_role"] == "sample-payload"
    )
    total = sum(item["logical_bytes"] for item in files)
    component_bytes = {
        "manifest_json_bytes": next(
            item["logical_bytes"] for item in files if item["relative_path"] == "manifest.json"
        ),
        "schema_json_bytes": next(
            item["logical_bytes"] for item in files if item["relative_path"] == "schema.json"
        ),
        "population_json_bytes": next(
            item["logical_bytes"] for item in files if item["relative_path"] == "population.json"
        ),
        "workload_json_bytes": next(
            item["logical_bytes"] for item in files if item["relative_path"] == "workload.json"
        ),
        "sample_payload_bytes_total": sample_total,
        "sealed_snapshot_logical_bytes": total,
        "regular_file_count": len(files),
    }
    _require(
        total == sum(item["logical_bytes"] for item in files),
        "snapshot logical byte total mismatch",
    )
    _require(
        sample_total
        == sum(
            item["logical_bytes"]
            for item in files
            if item["relative_path"].startswith("samples/")
            and item["relative_path"].endswith(".arrow")
        ),
        "sample payload subtotal mismatch",
    )
    return {
        "snapshot_semantic_digest": semantic,
        "component_bytes": component_bytes,
        "per_file_inventory": files,
        "validation_passed": True,
    }


def _run_json(command: list[str]) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=STAGE_HARD_CAP_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RQ5SnapshotFootprintValidationError(
            f"snapshot command exceeded the {STAGE_HARD_CAP_SECONDS:g} second hard cap: {command[0]}"
        ) from exc
    if completed.returncode:
        raise RQ5SnapshotFootprintValidationError(
            f"Advisor snapshot command failed ({completed.returncode}): {command[0]}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    _require(lines, "Advisor snapshot command produced no JSON result")
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise RQ5SnapshotFootprintValidationError(
            "Advisor snapshot command did not produce JSON"
        ) from exc
    _require(isinstance(value, dict), "Advisor snapshot command result is not an object")
    return value, time.monotonic() - started


def _capture_command(
    advisor_command: str,
    *,
    target_dsn: str,
    relation: str,
    workload_path: Path,
    snapshot_path: Path,
) -> list[str]:
    """Build the capture command without any truth-output option."""
    return [
        advisor_command,
        "snapshot",
        "capture",
        "postgres",
        "--dsn",
        target_dsn,
        "--relation",
        relation,
        "--sample-rows",
        str(SAMPLE_ROWS),
        "--sample-seed",
        str(SAMPLE_SEED),
        "--workload",
        str(workload_path),
        "--output",
        str(snapshot_path),
    ]


def _database_cleanup_verified(stock_dsn: str, database_name: str, psycopg: Any) -> bool:
    admin_dsn = psycopg.conninfo.make_conninfo(stock_dsn, dbname="postgres")
    with psycopg.connect(admin_dsn, autocommit=True) as connection:
        return not bool(
            connection.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname=%s)",
                (database_name,),
            ).fetchone()[0]
        )


def _run_repetition(
    dataset_id: str,
    *,
    repetition_id: int,
    stock_dsn: str,
    data_root: Path | None,
    advisor_command: str,
    research_commit_sha: str,
) -> dict[str, Any]:
    module, loader = DATASET_SPECS[dataset_id]
    psycopg = _psycopg()
    database_name = (
        f"rq5_snapshot_{dataset_id.removeprefix('arecel-')}_{repetition_id}_{uuid.uuid4().hex[:8]}"
    )
    database_created = False
    database_cleaned = False
    temporary_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix=f"rq5-snapshot-{dataset_id}-") as temporary:
            temporary_path = Path(temporary)
            workload_path = temporary_path / "workload.json"
            workload = module.extract_workload(workload_path, data_root, "test")
            snapshot_path = temporary_path / "snapshot"
            setup_started = time.monotonic()
            target_dsn = _create_database(stock_dsn, database_name, psycopg)
            setup_elapsed = time.monotonic() - setup_started
            database_created = True
            _require(
                setup_elapsed <= STAGE_HARD_CAP_SECONDS,
                "fresh database setup exceeded the 300 second hard cap",
            )
            load_started = time.monotonic()
            load = loader(
                target_dsn,
                data_root=data_root,
                reset_disposable=False,
                statistics_target=100,
                seed_identifier=LOADER_SEED_IDENTIFIER,
            )
            load_elapsed = time.monotonic() - load_started
            _require(
                load_elapsed <= STAGE_HARD_CAP_SECONDS,
                "fresh data load exceeded the 300 second hard cap",
            )
            _require(load.get("validated") is True, f"{dataset_id} loader did not validate")
            _require(
                load.get("physical_extended_statistics_count", 0) == 0,
                f"{dataset_id} loader left extended statistics",
            )
            capture_command = _capture_command(
                advisor_command,
                target_dsn=target_dsn,
                relation=module.RELATION,
                workload_path=workload_path,
                snapshot_path=snapshot_path,
            )
            capture_result, capture_elapsed = _run_json(capture_command)
            _require(
                capture_elapsed <= STAGE_HARD_CAP_SECONDS,
                "snapshot capture exceeded the 300 second hard cap",
            )
            validate_result, validation_elapsed = _run_json(
                [advisor_command, "snapshot", "validate", str(snapshot_path)]
            )
            _require(
                validation_elapsed <= STAGE_HARD_CAP_SECONDS,
                "snapshot validation exceeded the 300 second hard cap",
            )
            _require(
                validate_result.get("status") == "valid", "Advisor snapshot validation did not pass"
            )
            footprint = measure_snapshot_footprint(snapshot_path)
            _require(
                capture_result.get("semantic_digest") == footprint["snapshot_semantic_digest"],
                "capture and footprint snapshot digests differ",
            )
            result = {
                "format_version": "rq5-snapshot-footprint-repetition-v1",
                "status": "complete",
                "dataset_id": dataset_id,
                "repetition_id": repetition_id,
                "research_commit_sha": research_commit_sha,
                "advisor_sha": FROZEN_ADVISOR_SHA,
                "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
                "postgres_version": STOCK_POSTGRES_VERSION,
                "sample_rows": SAMPLE_ROWS,
                "sample_seed": SAMPLE_SEED,
                "workload_id": workload["workload_id"],
                "workload_sha256": workload["sha256"],
                "workload_query_count": workload["query_count"],
                "relation": module.RELATION,
                "dataset_identity": module.BENCHMARK_ID,
                "setup": {
                    "fresh_database_setup_elapsed_seconds": setup_elapsed,
                    "data_load_elapsed_seconds": load_elapsed,
                    "excluded_from_snapshot_footprint_primary_metric": True,
                },
                "snapshot_capture_elapsed_seconds": capture_elapsed,
                "snapshot_validation_elapsed_seconds": validation_elapsed,
                **footprint,
                "no_truth_acquisition": True,
                "no_planner_evaluation": True,
                "no_candidate_or_native_work": True,
                "cleanup_passed": False,
            }
        if database_created:
            _drop_database(stock_dsn, database_name, psycopg)
            database_cleaned = True
            cleanup_passed = _database_cleanup_verified(stock_dsn, database_name, psycopg)
            _require(cleanup_passed, f"{dataset_id} database cleanup could not be verified")
            result["cleanup_passed"] = True
        _require(
            temporary_path is None or not temporary_path.exists(),
            "temporary snapshot directory was not removed",
        )
        return result
    finally:
        if database_created and not database_cleaned:
            _drop_database(stock_dsn, database_name, psycopg)


def _validate_per_file_inventory(value: Mapping[str, Any]) -> None:
    files = value.get("per_file_inventory")
    _require(isinstance(files, list) and files, "per_file_inventory is missing")
    paths: list[str] = []
    for item in files:
        _require(
            set(item) == {"relative_path", "logical_bytes", "sha256", "component_role"},
            "per-file inventory schema drifted",
        )
        path = item["relative_path"]
        _require(
            isinstance(path, str) and not Path(path).is_absolute() and ".." not in Path(path).parts,
            "per-file path is unsafe",
        )
        _require(
            isinstance(item["sha256"], str) and len(item["sha256"]) == 64,
            "per-file SHA256 is invalid",
        )
        _finite(item["logical_bytes"], "per-file logical bytes")
        paths.append(path)
    _require(paths == sorted(paths), "per-file inventory is not deterministically sorted")
    _require(len(paths) == len(set(paths)), "per-file inventory contains duplicates")
    _require(set(KNOWN_FILES).issubset(paths), "per-file inventory lacks known components")


def _validate_repetition_artifact(
    value: Mapping[str, Any], *, expected_producer_sha: str | None = None
) -> None:
    _require(
        value.get("format_version") == "rq5-snapshot-footprint-repetition-v1",
        "unsupported snapshot footprint repetition format",
    )
    _require(value.get("status") == "complete", "snapshot footprint repetition is not complete")
    _require(
        value.get("sample_rows") == SAMPLE_ROWS and value.get("sample_seed") == SAMPLE_SEED,
        "snapshot sampling contract drifted",
    )
    _require(value.get("advisor_sha") == FROZEN_ADVISOR_SHA, "repetition Advisor SHA drifted")
    _require(
        value.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA, "repetition stock SHA drifted"
    )
    _require(
        value.get("postgres_version") == STOCK_POSTGRES_VERSION,
        "repetition PostgreSQL version drifted",
    )
    _require(
        expected_producer_sha is None or value.get("research_commit_sha") == expected_producer_sha,
        "repetition producer SHA drifted",
    )
    _require(value.get("validation_passed") is True, "snapshot validation did not pass")
    _require(value.get("cleanup_passed") is True, "snapshot cleanup did not pass")
    _require(
        value.get("no_truth_acquisition") is True
        and value.get("no_planner_evaluation") is True
        and value.get("no_candidate_or_native_work") is True,
        "repetition claims forbidden work",
    )
    _finite(value.get("snapshot_capture_elapsed_seconds"), "snapshot capture elapsed seconds")
    _require(
        value["snapshot_capture_elapsed_seconds"] <= STAGE_HARD_CAP_SECONDS,
        "snapshot capture exceeded the 300 second hard cap",
    )
    _validate_per_file_inventory(value)
    components = value.get("component_bytes")
    _require(isinstance(components, Mapping), "raw component bytes are missing")
    _require(
        set(components)
        == {
            "manifest_json_bytes",
            "schema_json_bytes",
            "population_json_bytes",
            "workload_json_bytes",
            "sample_payload_bytes_total",
            "sealed_snapshot_logical_bytes",
            "regular_file_count",
        },
        "snapshot component byte schema drifted",
    )
    total = components.get("sealed_snapshot_logical_bytes")
    sample_total = components.get("sample_payload_bytes_total")
    for filename, component_key in (
        ("manifest.json", "manifest_json_bytes"),
        ("schema.json", "schema_json_bytes"),
        ("population.json", "population_json_bytes"),
        ("workload.json", "workload_json_bytes"),
    ):
        _require(
            components[component_key]
            == next(
                item["logical_bytes"]
                for item in value["per_file_inventory"]
                if item["relative_path"] == filename
            ),
            f"raw {component_key} does not match per-file inventory",
        )
    _require(
        components["regular_file_count"] == len(value["per_file_inventory"]),
        "raw regular file count does not match per-file inventory",
    )
    _require(
        total == sum(item["logical_bytes"] for item in value["per_file_inventory"]),
        "raw total does not equal per-file sum",
    )
    _require(
        sample_total
        == sum(
            item["logical_bytes"]
            for item in value["per_file_inventory"]
            if item["component_role"] == "sample-payload"
        ),
        "raw sample subtotal does not equal Arrow sum",
    )
    _require(
        "snapshot_path" not in value and "workload_path" not in value,
        "sensitive temporary path leaked into raw artifact",
    )
    reject_credentials(value)


def validate_raw_artifact(
    value: Mapping[str, Any], *, expected_producer_sha: str | None = None
) -> dict[str, Any]:
    """Validate a tracked dataset child and every repetition it contains."""
    _require(
        value.get("format_version") == "rq5-snapshot-footprint-dataset-v1",
        "unsupported snapshot footprint raw format",
    )
    _require(value.get("status") == "complete", "snapshot footprint raw artifact is not complete")
    _require(
        value.get("sample_rows") == SAMPLE_ROWS and value.get("sample_seed") == SAMPLE_SEED,
        "snapshot sampling contract drifted",
    )
    _require(value.get("advisor_sha") == FROZEN_ADVISOR_SHA, "raw Advisor SHA drifted")
    _require(value.get("stock_postgres_sha") == FROZEN_STOCK_POSTGRES_SHA, "raw stock SHA drifted")
    _require(
        value.get("postgres_version") == STOCK_POSTGRES_VERSION, "raw PostgreSQL version drifted"
    )
    producer = value.get("research_commit_sha")
    _require(isinstance(producer, str) and len(producer) == 40, "raw producer SHA is missing")
    _require(
        expected_producer_sha is None or producer == expected_producer_sha,
        "raw producer SHA drifted",
    )
    _require(value.get("validation_passed") is True, "raw snapshot validation did not pass")
    _require(value.get("cleanup_passed") is True, "raw cleanup did not pass")
    _require(
        value.get("no_truth_acquisition") is True
        and value.get("no_planner_evaluation") is True
        and value.get("no_candidate_or_native_work") is True,
        "raw artifact claims forbidden work",
    )
    _require(value.get("protocol_path") == PROTOCOL_PATH, "raw protocol path drifted")
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        "raw protocol digest drifted",
    )
    _require(isinstance(value.get("preflight_path"), str), "raw preflight path is missing")
    _require(
        isinstance(value.get("preflight_semantic_digest"), str),
        "raw preflight digest is missing",
    )
    repetitions = value.get("repetitions")
    _require(
        isinstance(repetitions, list)
        and [rep.get("repetition_id") for rep in repetitions] == list(REPETITIONS),
        "raw repetition schema/order drifted",
    )
    for repetition in repetitions:
        _validate_repetition_artifact(repetition, expected_producer_sha=producer)
        for field in (
            "dataset_id",
            "workload_id",
            "workload_sha256",
            "workload_query_count",
            "relation",
            "dataset_identity",
        ):
            _require(
                repetition.get(field) == value.get(field),
                f"raw repetition {field} differs from dataset provenance",
            )
    _require(value.get("dataset_id") in DATASETS, "raw dataset identity is unsupported")
    _require(
        "snapshot_path" not in value and "workload_path" not in value,
        "sensitive temporary path leaked into raw artifact",
    )
    reject_credentials(value)
    return {
        "status": "valid",
        "dataset_id": value.get("dataset_id"),
        "repetition_count": len(repetitions),
    }


def _stats(values: list[float | int]) -> dict[str, float | int]:
    return {"raw": values, "median": median(values), "min": min(values), "max": max(values)}


def _read_raw_children(root: Path, preflight: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    producer = preflight["research_commit_sha"]
    raw_children: dict[str, dict[str, Any]] = {}
    for dataset_id in DATASETS:
        path = root / f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
        _require(path.is_file(), f"missing snapshot footprint raw child: {path}")
        value = read_json(path)
        validate_raw_artifact(value, expected_producer_sha=producer)
        _require(
            value.get("semantic_digest") == semantic_digest(_without_digest(value)),
            f"raw digest mismatch: {dataset_id}",
        )
        _require(
            value.get("dataset_id") == dataset_id, f"raw dataset identity drifted: {dataset_id}"
        )
        raw_children[dataset_id] = value
    return raw_children


def build_formal_summary(research_root: Path, *, preflight: Path) -> dict[str, Any]:
    root = research_root.resolve()
    preflight_path = (
        preflight.resolve() if preflight.is_absolute() else (root / preflight).resolve()
    )
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    raw_children = _read_raw_children(root, preflight_value)
    datasets: dict[str, Any] = {}
    raw_refs = []
    for dataset_id in DATASETS:
        raw = raw_children[dataset_id]
        path = f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
        raw_refs.append(
            {"dataset_id": dataset_id, "path": path, "semantic_digest": raw["semantic_digest"]}
        )
        reps = [
            raw["repetitions"][str(i)]
            if isinstance(raw["repetitions"], Mapping)
            else raw["repetitions"][i - 1]
            for i in REPETITIONS
        ]
        size_values = [rep["component_bytes"]["sealed_snapshot_logical_bytes"] for rep in reps]
        sample_values = [rep["component_bytes"]["sample_payload_bytes_total"] for rep in reps]
        capture_values = [rep["snapshot_capture_elapsed_seconds"] for rep in reps]
        datasets[dataset_id] = {
            "repetition_count": len(reps),
            "workload_id": raw["workload_id"],
            "relation": raw["relation"],
            "sealed_snapshot_logical_bytes": _stats(size_values),
            "sample_payload_bytes_total": _stats(sample_values),
            "snapshot_capture_elapsed_seconds": _stats(capture_values),
            "snapshot_semantic_digests": [rep["snapshot_semantic_digest"] for rep in reps],
            "snapshot_semantic_digests_equal": len(
                {rep["snapshot_semantic_digest"] for rep in reps}
            )
            == 1,
            "cleanup_passed": all(rep["cleanup_passed"] for rep in reps),
            "validation_passed": all(rep["validation_passed"] for rep in reps),
        }
    value = {
        "format_version": FORMAT_VERSION,
        "experiment_id": EXPERIMENT_ID,
        "status": "complete",
        "snapshot_footprint_subexperiment": True,
        "rq5_completion_status": "incomplete",
        "rq5_registry_status": "planned",
        "research_commit_sha": preflight_value["research_commit_sha"],
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": STOCK_POSTGRES_VERSION,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": preflight_value["semantic_digest"],
        "dataset_order": list(DATASETS),
        "repetitions": len(REPETITIONS),
        "raw_children": raw_refs,
        "datasets": datasets,
        "no_truth_acquisition": True,
        "no_planner_evaluation": True,
        "no_candidate_or_native_work": True,
        "no_snapshot_contents_tracked": True,
        "not_refresh": True,
        "semantic_digest": "",
    }
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    return value


def run_snapshot_footprint(
    dataset_id: str,
    *,
    research_root: Path,
    stock_dsn: str,
    output: Path,
    preflight: Path,
    advisor_command: str = "extstats-advisor",
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run exactly three fresh stock realizations for one dataset."""
    root = research_root.resolve()
    output_path, _ = _canonical_repo_path(root, output, label="raw output")
    _require(not output_path.exists(), f"raw output already exists: {output_path}")
    preflight_path = (
        preflight.resolve() if preflight.is_absolute() else (root / preflight).resolve()
    )
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    producer = verify_research_repository(root)["research_commit_sha"]
    _require(
        producer == preflight_value["research_commit_sha"], "implementation changed after preflight"
    )
    _require(dataset_id in DATASETS, f"unsupported snapshot footprint dataset: {dataset_id}")
    repetitions = []
    for repetition_id in REPETITIONS:
        repetition = _run_repetition(
            dataset_id,
            repetition_id=repetition_id,
            stock_dsn=stock_dsn,
            data_root=data_root,
            advisor_command=advisor_command,
            research_commit_sha=producer,
        )
        repetitions.append(repetition)
    value = {
        "format_version": "rq5-snapshot-footprint-dataset-v1",
        "status": "complete",
        "dataset_id": dataset_id,
        "research_commit_sha": producer,
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": STOCK_POSTGRES_VERSION,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "preflight_path": "experiments/rq5-snapshot-footprint-preflight-v1.json",
        "preflight_semantic_digest": preflight_value["semantic_digest"],
        "workload_id": repetitions[0]["workload_id"],
        "workload_sha256": repetitions[0]["workload_sha256"],
        "workload_query_count": repetitions[0]["workload_query_count"],
        "relation": repetitions[0]["relation"],
        "dataset_identity": repetitions[0]["dataset_identity"],
        "repetitions": repetitions,
        "validation_passed": True,
        "cleanup_passed": all(rep["cleanup_passed"] for rep in repetitions),
        "no_truth_acquisition": True,
        "no_planner_evaluation": True,
        "no_candidate_or_native_work": True,
        "semantic_digest": "",
    }
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    validate_raw_artifact(value, expected_producer_sha=producer)
    write_json(output_path, value)
    return value


def summarize_snapshot_footprint(
    research_root: Path, *, output: Path, preflight: Path
) -> dict[str, Any]:
    root = research_root.resolve()
    output_path, _ = _canonical_repo_path(root, output, label="formal output")
    _require(not output_path.exists(), f"formal output already exists: {output_path}")
    value = build_formal_summary(root, preflight=preflight)
    write_json(output_path, value)
    return value


def validate_formal_artifact(path: Path, research_root: Path) -> dict[str, Any]:
    value = read_json(path)
    _require(
        value.get("format_version") == FORMAT_VERSION,
        "unsupported snapshot footprint formal format",
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "snapshot footprint formal digest mismatch",
    )
    _require(
        value.get("status") == "complete", "snapshot footprint formal artifact is not complete"
    )
    _require(
        value.get("rq5_completion_status") == "incomplete",
        "snapshot footprint completed RQ5 unexpectedly",
    )
    _require(
        value.get("rq5_registry_status") == "planned",
        "snapshot footprint changed RQ5 registry status",
    )
    _require(
        value.get("no_snapshot_contents_tracked") is True,
        "snapshot contents are claimed to be tracked",
    )
    _require(value.get("not_refresh") is True, "snapshot repetitions are mislabeled as refresh")
    _require("total_advisor_seconds" not in value, "synthetic end-to-end cost is forbidden")
    _require(value.get("protocol_path") == PROTOCOL_PATH, "snapshot formal protocol path drifted")
    _require(
        value.get("protocol_semantic_digest") == PROTOCOL_DIGEST,
        "snapshot formal protocol digest drifted",
    )
    _require(
        len(value.get("datasets", {})) == len(DATASETS), "snapshot footprint dataset count drifted"
    )
    _require(value.get("dataset_order") == list(DATASETS), "snapshot formal dataset order drifted")
    root = research_root.resolve()
    preflight_path = root / value["preflight_path"]
    preflight_validation = validate_preflight(preflight_path, root)
    _require(
        preflight_validation["semantic_digest"] == value.get("preflight_semantic_digest"),
        "snapshot formal preflight digest drifted",
    )
    preflight_value = read_json(preflight_path)
    _require(
        value.get("research_commit_sha") == preflight_value.get("research_commit_sha"),
        "snapshot formal producer SHA drifted",
    )
    raw_children = value.get("raw_children")
    _require(
        isinstance(raw_children, list) and len(raw_children) == len(DATASETS),
        "snapshot formal raw child references drifted",
    )
    for reference, dataset_id in zip(raw_children, DATASETS, strict=True):
        expected_path = f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
        _require(
            reference.get("dataset_id") == dataset_id and reference.get("path") == expected_path,
            "snapshot formal raw child path drifted",
        )
        raw_path = root / expected_path
        raw = read_json(raw_path)
        validate_raw_artifact(raw, expected_producer_sha=value["research_commit_sha"])
        _require(
            reference.get("semantic_digest") == raw.get("semantic_digest"),
            "snapshot formal raw child digest drifted",
        )
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION,
        "semantic_digest": value["semantic_digest"],
        "dataset_count": len(value["datasets"]),
    }
