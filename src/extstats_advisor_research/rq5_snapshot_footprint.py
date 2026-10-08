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


def _git_head_sha(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RQ5SnapshotFootprintValidationError(
            f"could not resolve formal campaign HEAD for {root}"
        ) from exc
    head = completed.stdout.strip()
    _require(len(head) == 40, "formal campaign HEAD is not a full commit SHA")
    return head


def _git_status_entries(root: Path) -> list[tuple[str, str]]:
    """Return porcelain entries, rejecting anything except untracked files."""
    try:
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RQ5SnapshotFootprintValidationError(
            f"could not inspect formal campaign working tree for {root}"
        ) from exc
    entries: list[tuple[str, str]] = []
    for line in completed.stdout.splitlines():
        _require(len(line) >= 4 and line[2] == " ", "malformed Git status entry")
        status, path = line[:2], line[3:]
        _require(path != "", "Git status entry has no path")
        entries.append((status, path))
    return entries


def _raw_relative_path(dataset_id: str) -> str:
    return f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"


def _preflight_relative_path(value: Mapping[str, Any], actual: Path) -> str:
    expected = actual.as_posix()
    _require(
        value.get("preflight_path") == expected,
        "snapshot footprint preflight path does not match its actual repository path",
    )
    return expected


def _validate_raw_child_reference(
    root: Path,
    *,
    dataset_id: str,
    preflight: Mapping[str, Any],
    preflight_relative: str,
) -> dict[str, Any]:
    path = root / _raw_relative_path(dataset_id)
    _require(path.is_file(), f"missing snapshot footprint raw child: {path}")
    value = read_json(path)
    source_specs = {item["dataset_id"]: item for item in preflight["datasets"]}
    validate_raw_artifact(
        value,
        expected_producer_sha=preflight["research_commit_sha"],
        expected_source_spec=source_specs[dataset_id],
    )
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        f"raw digest mismatch: {dataset_id}",
    )
    _require(
        value.get("preflight_path") == preflight_relative
        and value.get("preflight_semantic_digest") == preflight["semantic_digest"],
        f"raw preflight provenance drifted: {dataset_id}",
    )
    return value


def _verify_formal_campaign_tree(
    root: Path,
    *,
    preflight: Mapping[str, Any],
    preflight_relative: str,
    phase: str,
    dataset_id: str | None = None,
    output_relative: str | None = None,
) -> str:
    """Validate the intentionally accumulating untracked formal evidence set."""
    _require(phase in {"run", "summarize"}, "unsupported snapshot campaign phase")
    head = _git_head_sha(root)
    _require(
        head == preflight.get("research_commit_sha"),
        "formal campaign HEAD differs from preflight producer SHA",
    )
    entries = _git_status_entries(root)
    _require(
        all(status == "??" for status, _ in entries),
        "formal campaign tree contains tracked modifications or staged changes",
    )
    actual_paths = {path for _, path in entries}
    expected_preflight = preflight_relative
    _require(
        expected_preflight in actual_paths,
        "formal campaign preflight must be an untracked artifact",
    )

    if phase == "run":
        _require(dataset_id in DATASETS, "formal campaign dataset is unsupported")
        index = DATASETS.index(dataset_id)
        allowed = {expected_preflight, *(_raw_relative_path(item) for item in DATASETS[:index])}
        expected_output = _raw_relative_path(dataset_id)
        _require(output_relative == expected_output, "formal raw output path/order is invalid")
        _require(
            expected_output not in actual_paths,
            "current formal raw child already exists before its run",
        )
        for future_dataset in DATASETS[index:]:
            _require(
                not (root / _raw_relative_path(future_dataset)).exists(),
                f"future formal raw child already exists: {future_dataset}",
            )
        _require(
            not (root / "experiments/rq5-snapshot-footprint-v1.json").exists(),
            "formal snapshot summary already exists before dataset run",
        )
        _require(actual_paths == allowed, "formal campaign tree has unexpected dirty artifacts")
        for prior_dataset in DATASETS[:index]:
            _validate_raw_child_reference(
                root,
                dataset_id=prior_dataset,
                preflight=preflight,
                preflight_relative=preflight_relative,
            )
    else:
        expected_output = "experiments/rq5-snapshot-footprint-v1.json"
        _require(output_relative == expected_output, "formal summary output path is invalid")
        _require(
            expected_output not in actual_paths and not (root / expected_output).exists(),
            "formal snapshot summary already exists",
        )
        allowed = {expected_preflight, *(_raw_relative_path(item) for item in DATASETS)}
        _require(actual_paths == allowed, "formal summary tree has unexpected dirty artifacts")
        for child_dataset in DATASETS:
            _validate_raw_child_reference(
                root,
                dataset_id=child_dataset,
                preflight=preflight,
                preflight_relative=preflight_relative,
            )
    return head


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
    workload_identity = _normalized_workload_identity(workload)
    return {
        "dataset_id": dataset_id,
        "benchmark_id": module.BENCHMARK_ID,
        "dataset_content_identity": metadata["dataset_content_identity"],
        "relation": module.RELATION,
        "schema_contract_id": module.SCHEMA_CONTRACT_ID,
        **workload_identity,
        "canonical_workload_sha256": metadata.get("canonical_workload_sha256"),
    }


def _normalized_workload_identity(workload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize an adapter workload result into formal provenance fields."""
    _require(isinstance(workload, Mapping), "adapter workload result is not an object")
    workload_id = workload.get("workload_id")
    workload_sha256 = workload.get("sha256")
    workload_query_count = workload.get("query_count")
    _require(
        isinstance(workload_id, str) and bool(workload_id),
        "adapter workload ID is missing",
    )
    _require(
        isinstance(workload_sha256, str) and len(workload_sha256) == 64,
        "adapter workload SHA256 is invalid",
    )
    _require(
        isinstance(workload_query_count, int) and not isinstance(workload_query_count, bool),
        "adapter workload query count is invalid",
    )
    _require(
        workload_query_count == 10_000,
        "adapter workload query count drifted",
    )
    return {
        "workload_id": workload_id,
        "workload_sha256": workload_sha256,
        "workload_query_count": workload_query_count,
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
    canonical_path, relative_path = _canonical_repo_path(
        research_root.resolve(), path, label="preflight"
    )
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
        value.get("preflight_path") == relative_path.as_posix(),
        "snapshot footprint preflight path does not match its actual repository path",
    )
    datasets = value.get("datasets")
    _require(
        isinstance(datasets, list) and len(datasets) == len(DATASETS),
        "snapshot footprint preflight dataset count drifted",
    )
    _require(
        [item.get("dataset_id") for item in datasets] == list(DATASETS),
        "snapshot footprint preflight dataset order or identity drifted",
    )
    required_source_fields = {
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
    for item in datasets:
        _require(
            isinstance(item, Mapping) and required_source_fields <= set(item),
            "snapshot footprint preflight source specification is incomplete",
        )
        _require(
            item["benchmark_id"] == item["dataset_id"],
            "snapshot footprint preflight benchmark identity drifted",
        )
        for field in ("dataset_content_identity", "workload_sha256"):
            _require(
                isinstance(item[field], str) and len(item[field]) == 64,
                f"snapshot footprint preflight {field} is invalid",
            )
        canonical_workload_sha256 = item["canonical_workload_sha256"]
        _require(
            canonical_workload_sha256 is None
            or (
                isinstance(canonical_workload_sha256, str) and len(canonical_workload_sha256) == 64
            ),
            "snapshot footprint preflight canonical workload SHA is invalid",
        )
        _require(
            isinstance(item["relation"], str) and item["relation"],
            "snapshot footprint preflight relation is missing",
        )
        _require(
            isinstance(item["schema_contract_id"], str) and item["schema_contract_id"],
            "snapshot footprint preflight schema contract is missing",
        )
        _require(
            isinstance(item["workload_id"], str) and item["workload_id"],
            "snapshot footprint preflight workload identity is missing",
        )
        _require(
            item["workload_query_count"] == 10_000,
            "snapshot footprint preflight workload query count drifted",
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
    source_spec: Mapping[str, Any],
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
            source_check_path = temporary_path / "source-check"
            source_check_path.mkdir()
            current_source_spec = _dataset_source_spec(dataset_id, data_root, source_check_path)
            _require(
                current_source_spec == dict(source_spec),
                f"{dataset_id} source identity changed before repetition {repetition_id}",
            )
            workload = module.extract_workload(workload_path, data_root, "test")
            workload_identity = _normalized_workload_identity(workload)
            for field in (
                "workload_id",
                "workload_sha256",
                "workload_query_count",
            ):
                _require(
                    workload_identity[field] == source_spec[field],
                    f"{dataset_id} workload source drifted before repetition {repetition_id}",
                )
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
                "advisor_command": advisor_command,
                "advisor_sha": FROZEN_ADVISOR_SHA,
                "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
                "postgres_version": STOCK_POSTGRES_VERSION,
                "sample_rows": SAMPLE_ROWS,
                "sample_seed": SAMPLE_SEED,
                "benchmark_id": source_spec["benchmark_id"],
                "dataset_content_identity": source_spec["dataset_content_identity"],
                "schema_contract_id": source_spec["schema_contract_id"],
                "canonical_workload_sha256": source_spec["canonical_workload_sha256"],
                **workload_identity,
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
    value: Mapping[str, Any],
    *,
    expected_producer_sha: str | None = None,
    expected_source_spec: Mapping[str, Any] | None = None,
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
        isinstance(value.get("advisor_command"), str) and value["advisor_command"],
        "repetition Advisor command identity is missing",
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
    if expected_source_spec is not None:
        for field in (
            "dataset_id",
            "benchmark_id",
            "dataset_content_identity",
            "schema_contract_id",
            "relation",
            "workload_id",
            "workload_sha256",
            "workload_query_count",
            "canonical_workload_sha256",
        ):
            _require(
                value.get(field) == expected_source_spec[field],
                f"repetition {field} differs from bound source specification",
            )
        _require(
            value.get("dataset_identity") == expected_source_spec["benchmark_id"],
            "repetition dataset benchmark identity differs from bound source specification",
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
    value: Mapping[str, Any],
    *,
    expected_producer_sha: str | None = None,
    expected_source_spec: Mapping[str, Any] | None = None,
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
    _require(
        isinstance(value.get("advisor_command"), str) and value["advisor_command"],
        "raw Advisor command identity is missing",
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
    source_fields = (
        "benchmark_id",
        "dataset_content_identity",
        "schema_contract_id",
        "canonical_workload_sha256",
        "relation",
        "workload_id",
        "workload_sha256",
        "workload_query_count",
    )
    _require(
        all(field in value for field in source_fields),
        "raw source specification is incomplete",
    )
    _require(
        value["benchmark_id"] == value.get("dataset_id") == value.get("dataset_identity"),
        "raw benchmark identity drifted",
    )
    for field in ("dataset_content_identity", "workload_sha256"):
        _require(
            isinstance(value[field], str) and len(value[field]) == 64,
            f"raw {field} is invalid",
        )
    if expected_source_spec is not None:
        for field in (
            "dataset_id",
            "benchmark_id",
            "dataset_content_identity",
            "schema_contract_id",
            "relation",
            "workload_id",
            "workload_sha256",
            "workload_query_count",
            "canonical_workload_sha256",
        ):
            actual_field = "dataset_identity" if field == "benchmark_id" else field
            _require(
                value.get(actual_field) == expected_source_spec[field],
                f"raw {field} differs from bound source specification",
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
        _validate_repetition_artifact(
            repetition,
            expected_producer_sha=producer,
            expected_source_spec=expected_source_spec or value,
        )
        for field in (
            "dataset_id",
            "benchmark_id",
            "dataset_content_identity",
            "schema_contract_id",
            "canonical_workload_sha256",
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
        _require(
            repetition.get("advisor_command") == value.get("advisor_command"),
            "raw repetition Advisor command differs from dataset provenance",
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


def _read_raw_children(
    root: Path,
    preflight: Mapping[str, Any],
    *,
    preflight_relative: str | None = None,
) -> dict[str, dict[str, Any]]:
    producer = preflight["research_commit_sha"]
    source_specs = {item["dataset_id"]: item for item in preflight["datasets"]}
    raw_children: dict[str, dict[str, Any]] = {}
    for dataset_id in DATASETS:
        path = root / f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
        _require(path.is_file(), f"missing snapshot footprint raw child: {path}")
        value = read_json(path)
        validate_raw_artifact(
            value,
            expected_producer_sha=producer,
            expected_source_spec=source_specs[dataset_id],
        )
        _require(
            value.get("semantic_digest") == semantic_digest(_without_digest(value)),
            f"raw digest mismatch: {dataset_id}",
        )
        _require(
            value.get("dataset_id") == dataset_id, f"raw dataset identity drifted: {dataset_id}"
        )
        if preflight_relative is not None:
            _require(
                value.get("preflight_path") == preflight_relative
                and value.get("preflight_semantic_digest") == preflight["semantic_digest"],
                f"raw preflight provenance drifted: {dataset_id}",
            )
        raw_children[dataset_id] = value
    return raw_children


def build_formal_summary(research_root: Path, *, preflight: Path) -> dict[str, Any]:
    root = research_root.resolve()
    preflight_path, preflight_relative_path = _canonical_repo_path(
        root, preflight, label="preflight"
    )
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    preflight_relative = _preflight_relative_path(preflight_value, preflight_relative_path)
    raw_children = _read_raw_children(root, preflight_value, preflight_relative=preflight_relative)
    datasets: dict[str, Any] = {}
    raw_refs = []
    for dataset_id in DATASETS:
        raw = raw_children[dataset_id]
        path = _raw_relative_path(dataset_id)
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
        "preflight_path": preflight_relative,
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
    advisor_root: Path,
    stock_postgres_root: Path,
    advisor_command: str = "extstats-advisor",
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run exactly three fresh stock realizations for one dataset."""
    root = research_root.resolve()
    output_path, output_relative_path = _canonical_repo_path(root, output, label="raw output")
    _require(not output_path.exists(), f"raw output already exists: {output_path}")
    preflight_path, preflight_relative_path = _canonical_repo_path(
        root, preflight, label="preflight"
    )
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    preflight_relative = _preflight_relative_path(preflight_value, preflight_relative_path)
    producer = _verify_formal_campaign_tree(
        root,
        preflight=preflight_value,
        preflight_relative=preflight_relative,
        phase="run",
        dataset_id=dataset_id,
        output_relative=output_relative_path.as_posix(),
    )
    _require(
        producer == preflight_value["research_commit_sha"], "implementation changed after preflight"
    )
    _require(dataset_id in DATASETS, f"unsupported snapshot footprint dataset: {dataset_id}")
    advisor_sha = verify_git_sha(advisor_root.resolve(), FROZEN_ADVISOR_SHA)
    stock_sha = verify_git_sha(stock_postgres_root.resolve(), FROZEN_STOCK_POSTGRES_SHA)
    _require(
        advisor_sha == preflight_value["advisor_sha"],
        "live Advisor source differs from preflight",
    )
    _require(
        stock_sha == preflight_value["stock_postgres_sha"],
        "live stock PostgreSQL source differs from preflight",
    )
    source_spec = next(
        item for item in preflight_value["datasets"] if item["dataset_id"] == dataset_id
    )
    with tempfile.TemporaryDirectory(prefix="rq5-snapshot-source-gate-") as temporary:
        current_source_spec = _dataset_source_spec(dataset_id, data_root, Path(temporary))
    _require(
        current_source_spec == source_spec,
        f"{dataset_id} source identity differs from preflight",
    )
    repetitions = []
    for repetition_id in REPETITIONS:
        repetition = _run_repetition(
            dataset_id,
            repetition_id=repetition_id,
            stock_dsn=stock_dsn,
            data_root=data_root,
            advisor_command=advisor_command,
            research_commit_sha=producer,
            source_spec=source_spec,
        )
        repetitions.append(repetition)
    value = {
        "format_version": "rq5-snapshot-footprint-dataset-v1",
        "status": "complete",
        "dataset_id": dataset_id,
        "research_commit_sha": producer,
        "advisor_command": advisor_command,
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": STOCK_POSTGRES_VERSION,
        "sample_rows": SAMPLE_ROWS,
        "sample_seed": SAMPLE_SEED,
        "benchmark_id": source_spec["benchmark_id"],
        "dataset_content_identity": source_spec["dataset_content_identity"],
        "schema_contract_id": source_spec["schema_contract_id"],
        "canonical_workload_sha256": source_spec["canonical_workload_sha256"],
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": PROTOCOL_DIGEST,
        "preflight_path": preflight_relative,
        "preflight_semantic_digest": preflight_value["semantic_digest"],
        "workload_id": source_spec["workload_id"],
        "workload_sha256": source_spec["workload_sha256"],
        "workload_query_count": source_spec["workload_query_count"],
        "relation": source_spec["relation"],
        "dataset_identity": source_spec["benchmark_id"],
        "repetitions": repetitions,
        "validation_passed": True,
        "cleanup_passed": all(rep["cleanup_passed"] for rep in repetitions),
        "no_truth_acquisition": True,
        "no_planner_evaluation": True,
        "no_candidate_or_native_work": True,
        "semantic_digest": "",
    }
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    validate_raw_artifact(
        value,
        expected_producer_sha=producer,
        expected_source_spec=source_spec,
    )
    write_json(output_path, value)
    return value


def summarize_snapshot_footprint(
    research_root: Path, *, output: Path, preflight: Path
) -> dict[str, Any]:
    root = research_root.resolve()
    output_path, _ = _canonical_repo_path(root, output, label="formal output")
    _require(not output_path.exists(), f"formal output already exists: {output_path}")
    preflight_path, preflight_relative_path = _canonical_repo_path(
        root, preflight, label="preflight"
    )
    validate_preflight(preflight_path, root)
    preflight_value = read_json(preflight_path)
    preflight_relative = _preflight_relative_path(preflight_value, preflight_relative_path)
    _verify_formal_campaign_tree(
        root,
        preflight=preflight_value,
        preflight_relative=preflight_relative,
        phase="summarize",
        output_relative=output_path.relative_to(root).as_posix(),
    )
    value = build_formal_summary(root, preflight=preflight_path)
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
    preflight_path, preflight_relative_path = _canonical_repo_path(
        root, Path(value["preflight_path"]), label="formal preflight"
    )
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
    validated_raw_children = _read_raw_children(
        root,
        preflight_value,
        preflight_relative=_preflight_relative_path(preflight_value, preflight_relative_path),
    )
    for reference, dataset_id in zip(raw_children, DATASETS, strict=True):
        expected_path = f"experiments/rq5-snapshot-footprint-v1/raw/{dataset_id}.json"
        _require(
            reference.get("dataset_id") == dataset_id and reference.get("path") == expected_path,
            "snapshot formal raw child path drifted",
        )
        raw = validated_raw_children[dataset_id]
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
