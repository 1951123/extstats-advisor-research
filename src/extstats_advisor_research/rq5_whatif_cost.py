"""Offline-first RQ5 what-if evaluation-cost harness.

This module owns the benchmark protocol, immutable manifests, stage timing,
failure accounting, and offline cost validation.  Database work is delegated
to explicit adapters; importing or using this module never opens a connection.
The live adapters are deliberately not hidden behind a default DSN.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, Self

from .provenance import read_json, semantic_digest, sha256_file, write_json

PROTOCOL_FORMAT = "rq5-whatif-evaluation-cost-v2"
PROTOCOL_PATH = "docs/protocols/rq5-whatif-evaluation-cost-v2.md"
MANIFEST_FORMAT = "rq5-whatif-evaluation-cost-manifest-v1"
EVENT_FORMAT = "rq5-whatif-evaluation-cost-event-v1"
FAILURE_FORMAT = "rq5-whatif-evaluation-cost-failure-v1"
ANALYSIS_FORMAT = "rq5-whatif-evaluation-cost-analysis-v1"
DATASET_ID = "arecel-forest10"
WORKLOAD_ID = "arecel_forest10_test_v1"
QUERY_SUBSET_SIZE = 128
SINGLETON_COUNT = 20
ANALYSIS_A_CHECKPOINTS = (1, 5, 10, 20)
ANALYSIS_B_SIZES = (0, 1, 5, 10, 20, 50)
ADVISOR_SHA_V1 = "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
RQ4_ABLATION_PATH = "experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json"
RQ4_ABLATION_DIGEST = "736ba74fcd41e34bc9e468dbf1e1ea149ee8caac7ca940be5236af6319d226a9"
RQ4_DESIGN_PATH = "experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json"
RQ4_DESIGN_DIGEST = "71483b2e67c7d57cd2696ed0925ea6d817c6548eec5df9b594cc71c8cfe44283"
_IDENTITY = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9._:]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class WhatIfCostError(ValueError):
    """Raised when a protocol, manifest, event, or adapter contract is invalid."""


class IncompleteRunError(WhatIfCostError):
    """Raised when a complete cost summary would require missing evidence."""


class AdapterExecutionError(WhatIfCostError):
    """Preserve a primary adapter error and an independent cleanup error."""

    def __init__(self, primary: BaseException, cleanup: BaseException | None = None) -> None:
        self.primary = primary
        self.cleanup = cleanup
        suffix = f"; cleanup failed: {cleanup}" if cleanup is not None else ""
        super().__init__(f"adapter execution failed: {primary}{suffix}")


class PhysicalCostAdapter(Protocol):
    """Explicit research-owned boundary for a stock PostgreSQL run."""

    def prepare_run(self) -> int: ...

    def create_clone(self, configuration: Mapping[str, Any]) -> int: ...

    def create_statistics(self, configuration: Mapping[str, Any]) -> int: ...

    def analyze(self, configuration: Mapping[str, Any]) -> int: ...

    def explain(self, configuration: Mapping[str, Any], query_ids: Sequence[str]) -> int: ...

    def drop_statistics(self, configuration: Mapping[str, Any]) -> int: ...

    def destroy_clone(self, configuration: Mapping[str, Any]) -> int: ...

    def cleanup(self) -> int: ...


class CataloglessCostAdapter(Protocol):
    """Explicit research-owned boundary for a patched planner run."""

    def prepare_sample(self) -> int: ...

    def materialize_payloads(self) -> int: ...

    def register_repository(self) -> int: ...

    def activate(self, configuration: Mapping[str, Any]) -> int: ...

    def explain(self, configuration: Mapping[str, Any], query_ids: Sequence[str]) -> int: ...

    def deactivate(self, configuration: Mapping[str, Any]) -> int: ...

    def cleanup(self) -> int: ...


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _digest(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["semantic_digest"] = semantic_digest(_body(result))
    return result


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WhatIfCostError(message)


def _identity(value: Any, label: str) -> str:
    _require(isinstance(value, str) and _IDENTITY.fullmatch(value) is not None, f"invalid {label}")
    return str(value)


def _sha(value: Any, label: str) -> str:
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None, f"invalid {label}")
    return str(value)


def _commit(value: Any, label: str) -> str:
    _require(isinstance(value, str) and _COMMIT.fullmatch(value) is not None, f"invalid {label}")
    return str(value)


def _git_head(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise WhatIfCostError(f"cannot resolve producer commit: {root}")
    return _commit(result.stdout.strip(), "producer commit")


def _repo_relative(root: Path, path: Path, label: str) -> str:
    root = root.resolve()
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise WhatIfCostError(f"{label} must be inside the research repository") from exc


def _pinned_artifact(root: Path, relative: str, expected: str) -> dict[str, Any]:
    path = root / relative
    _require(path.is_file(), f"missing source artifact: {relative}")
    value = read_json(path)
    embedded = value.get("semantic_digest")
    computed = semantic_digest(_body(value))
    _require(embedded == expected and computed == expected, f"source digest drifted: {relative}")
    return value


def _candidate_definitions(
    root: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    ablation = _pinned_artifact(root, RQ4_ABLATION_PATH, RQ4_ABLATION_DIGEST)
    design = _pinned_artifact(root, RQ4_DESIGN_PATH, RQ4_DESIGN_DIGEST)
    universe = design.get("eligible_universe", {})
    candidates = list(universe.get("eligible_candidates", []))
    _require(len(candidates) == 57, "Forest10 eligible candidate count must remain 57")
    candidates.sort(key=lambda item: int(item.get("canonical_deployment_position", 0)))
    positions = [int(item.get("canonical_deployment_position", 0)) for item in candidates]
    _require(positions == list(range(1, len(candidates) + 1)), "candidate order is not canonical")
    ids = [item.get("candidate_id") for item in candidates]
    _require(
        ids == ablation["candidate_universe"]["eligible_candidate_ids"], "candidate IDs drifted"
    )
    _require(
        all(isinstance(item.get("column_names"), list) for item in candidates),
        "candidate definitions incomplete",
    )
    return candidates, ablation, design


def _load_workload(root: Path, workload_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = (workload_path if workload_path.is_absolute() else root / workload_path).resolve()
    _require(path.is_file(), f"missing workload source: {path}")
    value = read_json(path)
    _require(value.get("workload_id") == WORKLOAD_ID, "Forest10 workload identity drifted")
    queries = list(value.get("queries", []))
    _require(len(queries) == 10_000, "Forest10 workload must contain 10,000 queries")
    expected_ids = [f"arecel_forest10_test_{index:06d}" for index in range(10_000)]
    _require(
        [item.get("query_id") for item in queries] == expected_ids, "workload query order drifted"
    )
    _require(
        all(isinstance(item.get("sql"), str) and item["sql"].strip() for item in queries),
        "workload SQL is incomplete",
    )
    indices = [
        index * (len(queries) - 1) // (QUERY_SUBSET_SIZE - 1) for index in range(QUERY_SUBSET_SIZE)
    ]
    selected = []
    for subset_index, source_index in enumerate(indices):
        query = queries[source_index]
        selected.append(
            {
                "subset_ordinal": subset_index,
                "source_index": source_index,
                "query_id": query["query_id"],
                "sql_sha256": hashlib.sha256(query["sql"].encode("utf-8")).hexdigest(),
                "weight": float(query.get("weight", 1.0)),
            }
        )
    source = {
        "path": _repo_relative(root, path, "workload source"),
        "sha256": sha256_file(path),
        "workload_id": value["workload_id"],
        "query_count": len(queries),
        "selection_rule": "evenly-spaced-canonical-index-v1",
        "provenance": value.get("provenance", {}),
    }
    return source, selected


def _configuration(
    analysis: str, ordinal: int, label: str, candidate_ids: Sequence[str]
) -> dict[str, Any]:
    return {
        "analysis": analysis,
        "ordinal": ordinal,
        "configuration_id": label,
        "candidate_ids": list(candidate_ids),
        "declared_order": list(candidate_ids),
        "configuration_size": len(candidate_ids),
    }


def _analysis_configurations(
    candidates: Sequence[Mapping[str, Any]], analysis: str
) -> list[dict[str, Any]]:
    ids = [str(item["candidate_id"]) for item in candidates]
    if analysis == "A":
        return [
            _configuration("A", index + 1, f"A-singleton-{index + 1:02d}", [candidate_id])
            for index, candidate_id in enumerate(ids[:SINGLETON_COUNT])
        ]
    if analysis == "B":
        return [
            _configuration("B", ordinal, f"B-prefix-k-{size:02d}", ids[:size])
            for ordinal, size in enumerate(ANALYSIS_B_SIZES, start=1)
        ]
    raise WhatIfCostError(f"unknown analysis: {analysis}")


def _binding_material(
    candidates: Sequence[Mapping[str, Any]],
    workload: Mapping[str, Any],
    queries: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "dataset_id": DATASET_ID,
        "candidate_definitions": [dict(item) for item in candidates],
        "workload": dict(workload),
        "query_subset": [dict(item) for item in queries],
        "analysis_a_configurations": _analysis_configurations(candidates, "A"),
        "analysis_b_configurations": _analysis_configurations(candidates, "B"),
    }


def build_manifest(
    research_root: Path,
    *,
    workload_path: Path,
    analysis: str,
    producer_sha: str | None = None,
    protocol_path: Path | None = None,
) -> dict[str, Any]:
    """Build one producer-bound Analysis A or B manifest without DB access."""

    root = research_root.resolve()
    protocol_value = protocol_path or root / PROTOCOL_PATH
    protocol = (protocol_value if protocol_value.is_absolute() else root / protocol_value).resolve()
    _require(protocol.is_file(), f"missing protocol: {protocol}")
    candidates, ablation, design = _candidate_definitions(root)
    workload, queries = _load_workload(root, workload_path)
    material = _binding_material(candidates, workload, queries)
    body = {
        "format_version": MANIFEST_FORMAT,
        "experiment_id": "rq5-whatif-evaluation-cost-v2-forest10",
        "analysis": analysis,
        "status": "ready-for-offline-validation",
        "producer_commit_sha": _commit(producer_sha or _git_head(root), "producer commit"),
        "protocol": {
            "format": PROTOCOL_FORMAT,
            "path": _repo_relative(root, protocol, "protocol"),
            "sha256": sha256_file(protocol),
        },
        "dataset": {
            "dataset_id": DATASET_ID,
            "workload_id": WORKLOAD_ID,
            "sample_rows": 10_000,
            "sample_seed": 42,
            "statistics_target": 100,
            "candidate_count": len(candidates),
        },
        "source_bindings": {
            "stock_postgres_sha": STOCK_POSTGRES_SHA,
            "patched_postgres_sha": PATCHED_POSTGRES_SHA,
            "advisor_sha": ADVISOR_SHA_V1,
            "rq4_ablation": {
                "path": RQ4_ABLATION_PATH,
                "semantic_digest": ablation["semantic_digest"],
            },
            "rq4_design": {"path": RQ4_DESIGN_PATH, "semantic_digest": design["semantic_digest"]},
        },
        "candidate_universe": {
            "semantic_digest": design["eligible_universe"]["semantic_digest"],
            "count": len(candidates),
            "definitions": [dict(item) for item in candidates],
        },
        "workload": workload,
        "query_subset": queries,
        "query_subset_size": len(queries),
        "analysis_a": {
            "evaluation_count": SINGLETON_COUNT,
            "configurations": _analysis_configurations(candidates, "A"),
        },
        "analysis_b": {
            "configuration_sizes": list(ANALYSIS_B_SIZES),
            "configurations": _analysis_configurations(candidates, "B"),
        },
        "binding_digest": semantic_digest(material),
        "timing_contract": {
            "clock": "client-monotonic-perf-counter-ns",
            "explain_is_ordinary": True,
            "explain_analyze": False,
            "physical_analyze_per_configuration": True,
            "catalogless_materialization_per_run": True,
            "catalogless_analyze_per_configuration": False,
            "complete_total_includes_mandatory_preparation": True,
            "warm_total_excludes_mandatory_preparation": True,
        },
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def write_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """Write one immutable manifest; never overwrite a previous binding."""

    if path.exists():
        raise WhatIfCostError(f"manifest output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, dict(manifest))


def validate_manifest(
    manifest: Mapping[str, Any],
    *,
    research_root: Path | None = None,
    verify_sources: bool = False,
) -> dict[str, Any]:
    """Validate structure, immutable source bindings, and both analysis boundaries."""

    _require(manifest.get("format_version") == MANIFEST_FORMAT, "unsupported cost manifest")
    digest = manifest.get("semantic_digest")
    _require(digest == semantic_digest(_body(manifest)), "manifest semantic digest mismatch")
    _commit(manifest.get("producer_commit_sha"), "producer commit")
    _require(manifest.get("dataset", {}).get("candidate_count") == 57, "candidate count drifted")
    _require(manifest.get("query_subset_size") == QUERY_SUBSET_SIZE, "query subset size drifted")
    _require(
        manifest.get("source_bindings", {}).get("advisor_sha") == ADVISOR_SHA_V1,
        "Advisor source drifted",
    )
    _require(
        manifest.get("source_bindings", {}).get("stock_postgres_sha") == STOCK_POSTGRES_SHA,
        "stock source drifted",
    )
    _require(
        manifest.get("source_bindings", {}).get("patched_postgres_sha") == PATCHED_POSTGRES_SHA,
        "patched source drifted",
    )
    _sha(manifest.get("protocol", {}).get("sha256"), "protocol sha256")
    _sha(manifest.get("binding_digest"), "binding digest")
    _require(manifest.get("analysis") in {"A", "B"}, "manifest analysis must be A or B")
    candidate_ids = [
        item.get("candidate_id")
        for item in manifest.get("candidate_universe", {}).get("definitions", [])
    ]
    _require(
        len(candidate_ids) == 57 and len(set(candidate_ids)) == 57,
        "candidate definitions are incomplete",
    )
    _require(
        manifest.get("workload", {}).get("workload_id") == WORKLOAD_ID, "workload identity drifted"
    )
    queries = manifest.get("query_subset", [])
    _require(len(queries) == QUERY_SUBSET_SIZE, "query subset is incomplete")
    _require(
        len({item.get("query_id") for item in queries}) == QUERY_SUBSET_SIZE, "duplicate query IDs"
    )
    for query in queries:
        _identity(query.get("query_id"), "query ID")
        _sha(query.get("sql_sha256"), "SQL digest")
    configs = (
        manifest.get("analysis_a", {}).get("configurations", [])
        if manifest.get("analysis") == "A"
        else manifest.get("analysis_b", {}).get("configurations", [])
    )
    _require(
        configs == manifest.get("analysis_a", {}).get("configurations", [])
        if manifest.get("analysis") == "A"
        else configs == manifest.get("analysis_b", {}).get("configurations", []),
        "configuration binding malformed",
    )
    _require(configs, "manifest has no configurations")
    ordinals = [item.get("ordinal") for item in configs]
    _require(
        ordinals == list(range(1, len(configs) + 1)), "configuration ordinals are not contiguous"
    )
    for config in configs:
        _identity(config.get("configuration_id"), "configuration ID")
        ids = config.get("candidate_ids")
        _require(ids == config.get("declared_order"), "configuration order drifted")
        _require(len(ids) == config.get("configuration_size"), "configuration size drifted")
        _require(
            all(candidate_id in candidate_ids for candidate_id in ids),
            "unknown candidate in configuration",
        )
    if manifest.get("analysis") == "A":
        _require(
            len(configs) == SINGLETON_COUNT
            and all(item["configuration_size"] == 1 for item in configs),
            "Analysis A contract drifted",
        )
    else:
        _require(
            [item["configuration_size"] for item in configs] == list(ANALYSIS_B_SIZES),
            "Analysis B contract drifted",
        )
    if verify_sources:
        _require(research_root is not None, "research root is required for source verification")
        root = research_root.resolve()
        protocol = root / manifest["protocol"]["path"]
        _require(
            protocol.is_file() and sha256_file(protocol) == manifest["protocol"]["sha256"],
            "protocol binding drifted",
        )
        _pinned_artifact(root, RQ4_ABLATION_PATH, RQ4_ABLATION_DIGEST)
        _pinned_artifact(root, RQ4_DESIGN_PATH, RQ4_DESIGN_DIGEST)
        workload_path = root / manifest["workload"]["path"]
        _require(
            workload_path.is_file()
            and sha256_file(workload_path) == manifest["workload"]["sha256"],
            "workload binding drifted",
        )
    return {
        "status": "valid",
        "format_version": MANIFEST_FORMAT,
        "analysis": manifest["analysis"],
        "semantic_digest": digest,
        "binding_digest": manifest["binding_digest"],
        "configuration_count": len(configs),
        "query_count": len(queries),
    }


class EventWriter:
    """Append-only JSONL writer whose writes occur after timed operations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._stream = path.open("xb")
        except FileExistsError as exc:
            raise WhatIfCostError(f"event output already exists: {path}") from exc
        self.events: list[dict[str, Any]] = []

    def write(self, event: Mapping[str, Any]) -> None:
        record = dict(event)
        payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        self._stream.write(payload)
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self.events.append(record)

    def close(self) -> None:
        self._stream.flush()
        self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def read_events(path: Path) -> list[dict[str, Any]]:
    _require(path.is_file(), f"missing event stream: {path}")
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


class StageTimer:
    def __init__(
        self,
        writer: EventWriter,
        *,
        producer_sha: str,
        manifest_digest: str,
        binding_digest: str,
        run_id: str,
        arm: str,
        repetition: int,
        clock: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self.writer = writer
        self.producer_sha = producer_sha
        self.manifest_digest = manifest_digest
        self.binding_digest = binding_digest
        self.run_id = _identity(run_id, "run ID")
        self.arm = arm
        self.repetition = repetition
        self.clock = clock

    def measure(
        self,
        stage: str,
        operation: Callable[[], Any],
        *,
        operation_count: int | None = None,
        configuration: Mapping[str, Any] | None = None,
    ) -> Any:
        started = self.clock()
        try:
            result = operation()
            status = "complete"
            error: dict[str, str] | None = None
            count = operation_count
            if count is None and isinstance(result, int) and not isinstance(result, bool):
                count = result
            if count is None:
                raise WhatIfCostError(f"stage {stage} did not provide an operation count")
        except BaseException as exc:  # preserve timed operation failures
            ended = self.clock()
            self.writer.write(self._event(stage, started, ended, 0, "failed", configuration, exc))
            raise
        ended = self.clock()
        self.writer.write(self._event(stage, started, ended, count, status, configuration, error))
        return result

    def _event(
        self,
        stage: str,
        started: int,
        ended: int,
        count: int,
        status: str,
        configuration: Mapping[str, Any] | None,
        error: BaseException | Mapping[str, str] | None,
    ) -> dict[str, Any]:
        _require(ended >= started, "monotonic clock moved backwards")
        _require(isinstance(count, int) and count >= 0, "operation count must be nonnegative")
        error_value = None
        if error is not None:
            error_value = {
                "class": type(error).__name__,
                "message": str(error)[:32_000],
            }
        return {
            "format_version": EVENT_FORMAT,
            "protocol_format": PROTOCOL_FORMAT,
            "producer_commit_sha": self.producer_sha,
            "manifest_semantic_digest": self.manifest_digest,
            "binding_digest": self.binding_digest,
            "run_id": self.run_id,
            "arm": self.arm,
            "repetition": self.repetition,
            "configuration_id": None
            if configuration is None
            else configuration["configuration_id"],
            "configuration_ordinal": None if configuration is None else configuration["ordinal"],
            "stage": stage,
            "monotonic_start_ns": started,
            "monotonic_end_ns": ended,
            "elapsed_ns": ended - started,
            "operation_count": count,
            "status": status,
            "error": error_value,
        }


def _run_failure_path(output: Path, identity: str) -> Path:
    return output / "failures" / f"{_identity(identity, 'failure ID')}.json"


def write_failure(
    output: Path,
    *,
    failure_id: str,
    run_id: str,
    arm: str,
    repetition: int,
    producer_sha: str,
    manifest_digest: str,
    events: Sequence[Mapping[str, Any]],
    primary: BaseException,
    cleanup: BaseException | None = None,
) -> dict[str, Any]:
    path = _run_failure_path(output, failure_id)
    if path.exists():
        raise WhatIfCostError(f"failure output already exists: {path}")
    record = _digest(
        {
            "format_version": FAILURE_FORMAT,
            "failure_id": failure_id,
            "run_id": run_id,
            "arm": arm,
            "repetition": repetition,
            "producer_commit_sha": producer_sha,
            "manifest_semantic_digest": manifest_digest,
            "phase": events[-1].get("stage") if events else "before-first-stage",
            "primary_exception": {
                "class": type(primary).__name__,
                "message": str(primary)[:32_000],
            },
            "cleanup_exception": None
            if cleanup is None
            else {"class": type(cleanup).__name__, "message": str(cleanup)[:32_000]},
            "event_count": len(events),
            "completed_explain_calls": sum(
                int(event.get("operation_count", 0))
                for event in events
                if event.get("stage") == "explain" and event.get("status") == "complete"
            ),
            "completed_configuration_ordinals": sorted(
                {
                    int(event["configuration_ordinal"])
                    for event in events
                    if event.get("stage") in {"clone-destroy", "deactivate"}
                    and event.get("status") == "complete"
                    and event.get("configuration_ordinal") is not None
                }
            ),
            "status": "failed",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, record)
    return record


_PHYSICAL_CONFIG_STAGES = (
    "clone-create",
    "create-alter-statistics",
    "analyze",
    "explain",
    "drop-statistics",
    "clone-destroy",
)
_CATALOGLESS_CONFIG_STAGES = ("activate", "explain", "deactivate")
_PHYSICAL_RUN_STAGES = ("baseline-initialization", "arm-cleanup")
_CATALOGLESS_RUN_STAGES = (
    "fixed-sample-preparation",
    "native-payload-materialization",
    "repository-registration",
    "arm-cleanup",
)


def _manifest_configs(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    return list(
        manifest["analysis_a"]["configurations"]
        if manifest["analysis"] == "A"
        else manifest["analysis_b"]["configurations"]
    )


def _expected_count(
    stage: str, configuration: Mapping[str, Any] | None, query_count: int, candidate_count: int
) -> int:
    if stage in {"baseline-initialization", "fixed-sample-preparation", "arm-cleanup", "analyze"}:
        return 1
    if stage == "native-payload-materialization" or stage == "repository-registration":
        return candidate_count
    if stage in {"clone-create", "clone-destroy"}:
        return 1
    if stage == "explain":
        return query_count
    if configuration is None:
        raise WhatIfCostError(f"configuration required for stage {stage}")
    if stage in {"create-alter-statistics", "drop-statistics", "activate", "deactivate"}:
        return len(configuration["candidate_ids"])
    raise WhatIfCostError(f"unknown cost stage: {stage}")


def validate_events(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    arm: str,
    repetition: int,
    require_complete: bool = True,
) -> dict[str, Any]:
    validate_manifest(manifest)
    _identity(run_id, "run ID")
    _require(arm in {"physical", "catalogless"}, "unknown arm")
    _require(isinstance(repetition, int) and repetition >= 1, "invalid repetition")
    configs = _manifest_configs(manifest)
    config_by_ordinal = {int(item["ordinal"]): item for item in configs}
    run_stages = set(_PHYSICAL_RUN_STAGES if arm == "physical" else _CATALOGLESS_RUN_STAGES)
    config_stages = set(
        _PHYSICAL_CONFIG_STAGES if arm == "physical" else _CATALOGLESS_CONFIG_STAGES
    )
    seen: set[tuple[str, int | None]] = set()
    errors: list[str] = []
    for event in events:
        _require(event.get("format_version") == EVENT_FORMAT, "unsupported event format")
        _require(
            event.get("run_id") == run_id and event.get("arm") == arm, "event identity mismatch"
        )
        _require(event.get("repetition") == repetition, "event repetition mismatch")
        _require(
            event.get("manifest_semantic_digest") == manifest["semantic_digest"],
            "event manifest mismatch",
        )
        _require(
            event.get("binding_digest") == manifest["binding_digest"], "event binding mismatch"
        )
        stage = event.get("stage")
        ordinal = event.get("configuration_ordinal")
        config = None
        if stage in config_stages:
            _require(
                isinstance(ordinal, int) and ordinal in config_by_ordinal,
                "event configuration is unknown",
            )
            config = config_by_ordinal[ordinal]
            _require(
                event.get("configuration_id") == config["configuration_id"],
                "event configuration ID mismatch",
            )
        else:
            _require(stage in run_stages, f"unexpected stage for {arm}: {stage}")
            _require(
                ordinal is None and event.get("configuration_id") is None,
                "run stage has configuration identity",
            )
        key = (str(stage), ordinal if isinstance(ordinal, int) else None)
        _require(key not in seen, f"duplicate event: {key}")
        seen.add(key)
        _require(event.get("status") in {"complete", "failed"}, "invalid event status")
        _require(
            isinstance(event.get("operation_count"), int) and event["operation_count"] >= 0,
            "invalid operation count",
        )
        _require(isinstance(event.get("monotonic_start_ns"), int), "invalid event start")
        _require(isinstance(event.get("monotonic_end_ns"), int), "invalid event end")
        _require(
            event["monotonic_end_ns"] >= event["monotonic_start_ns"], "event clock moved backwards"
        )
        _require(
            event.get("elapsed_ns") == event["monotonic_end_ns"] - event["monotonic_start_ns"],
            "event elapsed mismatch",
        )
        expected = _expected_count(str(stage), config, int(manifest["query_subset_size"]), 57)
        if event.get("status") == "complete" and event.get("operation_count") != expected:
            errors.append(
                f"{stage}/{ordinal} operation count {event.get('operation_count')} != {expected}"
            )
    required = {(stage, None) for stage in run_stages}
    required.update((stage, ordinal) for ordinal in config_by_ordinal for stage in config_stages)
    complete = (
        not errors
        and required.issubset(seen)
        and all(event.get("status") == "complete" for event in events)
    )
    if errors:
        raise WhatIfCostError("; ".join(errors))
    if require_complete and not complete:
        missing = sorted(required - seen, key=str)
        raise IncompleteRunError(f"incomplete {arm} run; missing events: {missing}")
    return {
        "status": "complete" if complete else "incomplete",
        "event_count": len(events),
        "missing_events": sorted(required - seen, key=str),
        "configuration_count": len(configs),
        "explain_calls": sum(
            int(event["operation_count"])
            for event in events
            if event.get("stage") == "explain" and event.get("status") == "complete"
        ),
    }


def summarize_events(
    manifest: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    run_id: str,
    arm: str,
    repetition: int,
) -> dict[str, Any]:
    """Recompute complete and warm cumulative costs from verified raw events."""

    validate_events(
        manifest, events, run_id=run_id, arm=arm, repetition=repetition, require_complete=True
    )
    configs = _manifest_configs(manifest)
    preparation_stages = set(_PHYSICAL_RUN_STAGES if arm == "physical" else _CATALOGLESS_RUN_STAGES)
    prep_ns = sum(
        int(event["elapsed_ns"])
        for event in events
        if event["stage"] in preparation_stages and event["stage"] != "arm-cleanup"
    )
    by_ordinal: dict[int, int] = {}
    by_stage: dict[str, int] = {}
    for event in events:
        if event["stage"] == "arm-cleanup":
            continue
        by_stage[event["stage"]] = by_stage.get(event["stage"], 0) + int(event["elapsed_ns"])
        if event["configuration_ordinal"] is not None:
            ordinal = int(event["configuration_ordinal"])
            by_ordinal[ordinal] = by_ordinal.get(ordinal, 0) + int(event["elapsed_ns"])
    cumulative = []
    running = prep_ns
    warm_running = 0
    for config in configs:
        ordinal = int(config["ordinal"])
        running += by_ordinal[ordinal]
        warm_running += by_ordinal[ordinal]
        cumulative.append(
            {
                "ordinal": ordinal,
                "configuration_id": config["configuration_id"],
                "configuration_size": config["configuration_size"],
                "complete_lifecycle_ns": running,
                "warm_start_ns": warm_running,
            }
        )
    return {
        "format_version": ANALYSIS_FORMAT,
        "status": "complete",
        "manifest_semantic_digest": manifest["semantic_digest"],
        "binding_digest": manifest["binding_digest"],
        "run_id": run_id,
        "arm": arm,
        "repetition": repetition,
        "preparation_ns": prep_ns,
        "stage_totals_ns": by_stage,
        "cumulative": cumulative,
        "explain_calls": sum(
            int(event["operation_count"]) for event in events if event["stage"] == "explain"
        ),
        "analyze_calls": sum(
            int(event["operation_count"]) for event in events if event["stage"] == "analyze"
        ),
        "configuration_count": len(configs),
    }


def compare_costs(physical: Mapping[str, Any], catalogless: Mapping[str, Any]) -> dict[str, Any]:
    _require(
        physical.get("status") == "complete" and catalogless.get("status") == "complete",
        "cannot compare incomplete runs",
    )
    _require(
        physical.get("binding_digest") == catalogless.get("binding_digest"),
        "arms do not share binding",
    )
    p = {int(item["ordinal"]): item for item in physical["cumulative"]}
    c = {int(item["ordinal"]): item for item in catalogless["cumulative"]}
    _require(set(p) == set(c), "arms do not share configuration sequence")
    checkpoints = []
    for ordinal in sorted(p):
        checkpoints.append(
            {
                "ordinal": ordinal,
                "configuration_id": p[ordinal]["configuration_id"],
                "physical_complete_lifecycle_ns": p[ordinal]["complete_lifecycle_ns"],
                "catalogless_complete_lifecycle_ns": c[ordinal]["complete_lifecycle_ns"],
                "physical_warm_start_ns": p[ordinal]["warm_start_ns"],
                "catalogless_warm_start_ns": c[ordinal]["warm_start_ns"],
                "catalogless_is_lower": c[ordinal]["complete_lifecycle_ns"]
                < p[ordinal]["complete_lifecycle_ns"],
            }
        )
    return {
        "format_version": ANALYSIS_FORMAT,
        "status": "complete",
        "binding_digest": physical["binding_digest"],
        "checkpoints": checkpoints,
        "observed_complete_lifecycle_crossovers": [
            item["ordinal"] for item in checkpoints if item["catalogless_is_lower"]
        ],
        "claim_boundary": "observed mechanism-level cumulative cost only",
    }


def _run_arm_failure(
    output: Path | None,
    failure_id: str,
    run_id: str,
    arm: str,
    repetition: int,
    manifest: Mapping[str, Any],
    writer: EventWriter,
    primary: BaseException,
    cleanup: BaseException | None,
) -> None:
    if output is not None:
        write_failure(
            output,
            failure_id=failure_id,
            run_id=run_id,
            arm=arm,
            repetition=repetition,
            producer_sha=manifest["producer_commit_sha"],
            manifest_digest=manifest["semantic_digest"],
            events=writer.events,
            primary=primary,
            cleanup=cleanup,
        )


def run_physical_arm(
    manifest: Mapping[str, Any],
    adapter: PhysicalCostAdapter,
    *,
    run_id: str,
    repetition: int,
    events_path: Path,
    failure_output: Path | None = None,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    """Run the physical orchestration against an explicit adapter."""

    validate_manifest(manifest)
    with EventWriter(events_path) as writer:
        timer = StageTimer(
            writer,
            producer_sha=manifest["producer_commit_sha"],
            manifest_digest=manifest["semantic_digest"],
            binding_digest=manifest["binding_digest"],
            run_id=run_id,
            arm="physical",
            repetition=repetition,
            clock=clock,
        )
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            timer.measure("baseline-initialization", adapter.prepare_run)
            for config in _manifest_configs(manifest):
                timer.measure(
                    "clone-create", lambda c=config: adapter.create_clone(c), configuration=config
                )
                timer.measure(
                    "create-alter-statistics",
                    lambda c=config: adapter.create_statistics(c),
                    configuration=config,
                )
                timer.measure("analyze", lambda c=config: adapter.analyze(c), configuration=config)
                timer.measure(
                    "explain",
                    lambda c=config: adapter.explain(
                        c, [item["query_id"] for item in manifest["query_subset"]]
                    ),
                    configuration=config,
                )
                timer.measure(
                    "drop-statistics",
                    lambda c=config: adapter.drop_statistics(c),
                    configuration=config,
                )
                timer.measure(
                    "clone-destroy", lambda c=config: adapter.destroy_clone(c), configuration=config
                )
        except BaseException as exc:  # noqa: BLE001 - preserve arbitrary adapter failures
            primary = exc
        try:
            timer.measure("arm-cleanup", adapter.cleanup)
        except BaseException as exc:  # noqa: BLE001 - preserve independent cleanup failures
            cleanup_error = exc
        if primary is not None:
            _run_arm_failure(
                failure_output,
                f"{run_id}-physical-failure",
                run_id,
                "physical",
                repetition,
                manifest,
                writer,
                primary,
                cleanup_error,
            )
            raise AdapterExecutionError(primary, cleanup_error) from primary
        if cleanup_error is not None:
            _run_arm_failure(
                failure_output,
                f"{run_id}-physical-cleanup-failure",
                run_id,
                "physical",
                repetition,
                manifest,
                writer,
                cleanup_error,
                cleanup_error,
            )
            raise AdapterExecutionError(cleanup_error, cleanup_error) from cleanup_error
        return summarize_events(
            manifest, writer.events, run_id=run_id, arm="physical", repetition=repetition
        )


def run_catalogless_arm(
    manifest: Mapping[str, Any],
    adapter: CataloglessCostAdapter,
    *,
    run_id: str,
    repetition: int,
    events_path: Path,
    failure_output: Path | None = None,
    clock: Callable[[], int] = time.perf_counter_ns,
) -> dict[str, Any]:
    """Run the catalogless orchestration against an explicit adapter."""

    validate_manifest(manifest)
    with EventWriter(events_path) as writer:
        timer = StageTimer(
            writer,
            producer_sha=manifest["producer_commit_sha"],
            manifest_digest=manifest["semantic_digest"],
            binding_digest=manifest["binding_digest"],
            run_id=run_id,
            arm="catalogless",
            repetition=repetition,
            clock=clock,
        )
        primary: BaseException | None = None
        cleanup_error: BaseException | None = None
        try:
            timer.measure("fixed-sample-preparation", adapter.prepare_sample)
            timer.measure("native-payload-materialization", adapter.materialize_payloads)
            timer.measure("repository-registration", adapter.register_repository)
            for config in _manifest_configs(manifest):
                timer.measure(
                    "activate", lambda c=config: adapter.activate(c), configuration=config
                )
                timer.measure(
                    "explain",
                    lambda c=config: adapter.explain(
                        c, [item["query_id"] for item in manifest["query_subset"]]
                    ),
                    configuration=config,
                )
                timer.measure(
                    "deactivate", lambda c=config: adapter.deactivate(c), configuration=config
                )
        except BaseException as exc:  # noqa: BLE001 - preserve arbitrary adapter failures
            primary = exc
        try:
            timer.measure("arm-cleanup", adapter.cleanup)
        except BaseException as exc:  # noqa: BLE001 - preserve independent cleanup failures
            cleanup_error = exc
        if primary is not None:
            _run_arm_failure(
                failure_output,
                f"{run_id}-catalogless-failure",
                run_id,
                "catalogless",
                repetition,
                manifest,
                writer,
                primary,
                cleanup_error,
            )
            raise AdapterExecutionError(primary, cleanup_error) from primary
        if cleanup_error is not None:
            _run_arm_failure(
                failure_output,
                f"{run_id}-catalogless-cleanup-failure",
                run_id,
                "catalogless",
                repetition,
                manifest,
                writer,
                cleanup_error,
                cleanup_error,
            )
            raise AdapterExecutionError(cleanup_error, cleanup_error) from cleanup_error
        return summarize_events(
            manifest, writer.events, run_id=run_id, arm="catalogless", repetition=repetition
        )


def integration_preflight_plan(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return a non-executing plan for a future, separately authorized smoke."""

    validate_manifest(manifest)
    candidates = manifest["candidate_universe"]["definitions"][:2]
    queries = manifest["query_subset"][:3]
    return _digest(
        {
            "format_version": "rq5-whatif-evaluation-cost-integration-preflight-plan-v1",
            "status": "not-executed",
            "authorization_required": True,
            "live_database_connections": 0,
            "formal_timing_authorized": False,
            "manifest_semantic_digest": manifest["semantic_digest"],
            "fixture_scope": {
                "candidate_count": len(candidates),
                "candidate_ids": [item["candidate_id"] for item in candidates],
                "query_count": len(queries),
                "query_ids": [item["query_id"] for item in queries],
                "purpose": "catalog identity, payload state, activation order, EXPLAIN shape, and cleanup only",
            },
            "required_future_inputs": [
                "explicit stock and patched PostgreSQL DSNs",
                "pinned binary identity verification",
                "isolated fixture database and output namespace",
                "research-owned stock and catalogless adapters",
            ],
        }
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="offline RQ5 what-if cost harness")
    commands = parser.add_subparsers(dest="command", required=True)
    manifest = commands.add_parser("manifest", help="build the two offline manifests")
    manifest.add_argument("--research-root", type=Path, required=True)
    manifest.add_argument("--workload", type=Path, required=True)
    manifest.add_argument("--output-dir", type=Path, required=True)
    manifest.add_argument("--producer-sha", default=None)
    validate = commands.add_parser("validate-manifest")
    validate.add_argument("artifact", type=Path)
    validate.add_argument("--research-root", type=Path, default=None)
    validate.add_argument("--verify-sources", action="store_true")
    preflight = commands.add_parser("integration-preflight-plan")
    preflight.add_argument("manifest", type=Path)
    preflight.add_argument("--output", type=Path, required=True)
    live = commands.add_parser(
        "integration-preflight", help="run an explicitly authorized tiny live fixture"
    )
    live.add_argument("manifest", type=Path)
    live.add_argument("--advisor-root", type=Path, required=True)
    live.add_argument("--snapshot", type=Path, required=True)
    live.add_argument("--candidate-universe", type=Path, required=True)
    live.add_argument("--workload", type=Path, required=True)
    live.add_argument("--stock-dsn", required=True)
    live.add_argument("--stock-admin-dsn", required=True)
    live.add_argument("--patched-dsn", required=True)
    live.add_argument("--stock-identity", type=Path, required=True)
    live.add_argument("--patched-identity", type=Path, required=True)
    live.add_argument("--output-dir", type=Path, required=True)
    live.add_argument("--output", type=Path, required=True)
    live.add_argument("--run-id", required=True)
    live.add_argument("--enable-live-preflight", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "manifest":
        for analysis in ("A", "B"):
            value = build_manifest(
                args.research_root,
                workload_path=args.workload,
                analysis=analysis,
                producer_sha=args.producer_sha,
            )
            output = args.output_dir / f"analysis-{analysis.lower()}-manifest-v1.json"
            write_manifest(output, value)
            print(
                json.dumps(
                    {
                        "status": "written",
                        "output": str(output),
                        "semantic_digest": value["semantic_digest"],
                    },
                    sort_keys=True,
                )
            )
        return 0
    if args.command == "validate-manifest":
        value = read_json(args.artifact)
        result = validate_manifest(
            value, research_root=args.research_root, verify_sources=args.verify_sources
        )
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    if args.command == "integration-preflight-plan":
        value = read_json(args.manifest)
        result = integration_preflight_plan(value)
        write_manifest(args.output, result)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "output": str(args.output),
                    "semantic_digest": result["semantic_digest"],
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "integration-preflight":
        from .rq5_whatif_cost_postgres import LiveAdapterConfig, run_integration_preflight

        manifest = read_json(args.manifest)
        config = LiveAdapterConfig(
            stock_dsn=args.stock_dsn,
            stock_admin_dsn=args.stock_admin_dsn,
            patched_dsn=args.patched_dsn,
            advisor_root=args.advisor_root,
            snapshot_path=args.snapshot,
            candidate_universe_path=args.candidate_universe,
            workload_path=args.workload,
            stock_identity_path=args.stock_identity,
            patched_identity_path=args.patched_identity,
            output_dir=args.output_dir,
            run_id=args.run_id,
            enable_live=args.enable_live_preflight,
        )
        result = run_integration_preflight(manifest, config, args.output)
        print(json.dumps(result, sort_keys=True, indent=2))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
