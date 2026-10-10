"""Offline-first orchestration and validation for Native ANALYZE Stability v1.

The module deliberately separates the scientific protocol from the database
adapter.  The default entry points only construct plans or validate evidence;
an eventual live adapter must be supplied explicitly by a separately
authorized execution.  This makes it possible to exercise the complete state
machine with a mock adapter without opening a PostgreSQL connection.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import statistics
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .native_analyze_stability import METHOD_ORDER, PRIMARY_DATASETS, _source_ref, validate_protocol
from .paper_baseline import percentile, qerror
from .provenance import read_json, semantic_digest, sha256_file

HARNESS_FORMAT = "native-analyze-stability-harness-v1"
INVOCATION_FORMAT = "native-analyze-stability-invocation-v1"
REALIZATION_FORMAT = "native-analyze-stability-realization-v1"
ARM_FORMAT = "native-analyze-stability-method-arm-v1"
FAILURE_FORMAT = "native-analyze-stability-failure-v1"
CLEANUP_FORMAT = "native-analyze-stability-cleanup-v1"
SUMMARY_FORMAT = "native-analyze-stability-summary-v1"
QUERY_FORMAT = "native-analyze-stability-query-v1"
PROTOCOL_PATH = "paper/native-analyze-stability-protocol-v1.json"
_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class StabilityError(ValueError):
    """Raised when a protocol, identity, or evidence invariant is violated."""


class IncompleteEvidenceError(StabilityError):
    """Raised when a formal aggregate would require silent imputation."""


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _with_digest(value: dict[str, Any]) -> dict[str, Any]:
    value["semantic_digest"] = semantic_digest(_body(value))
    return value


def _require_identity(value: str, label: str) -> str:
    if not isinstance(value, str) or not _IDENTITY.fullmatch(value):
        raise StabilityError(f"invalid {label}: {value!r}")
    return value


def make_invocation_id(token: str) -> str:
    return _require_identity(token, "invocation id")


def make_dataset_id(invocation_id: str, dataset_id: str) -> str:
    return f"{make_invocation_id(invocation_id)}::{_require_identity(dataset_id, 'dataset id')}"


def make_realization_id(invocation_id: str, dataset_id: str, realization: int) -> str:
    if not isinstance(realization, int) or realization < 1 or realization > 5:
        raise StabilityError("realization must be in 1..5")
    return f"{make_dataset_id(invocation_id, dataset_id)}::realization-{realization:02d}"


def make_method_arm_id(realization_id: str, method_id: str) -> str:
    return f"{realization_id}::{_require_identity(method_id, 'method id')}"


@dataclass(frozen=True)
class OutputScope:
    root: Path
    invocation_id: str

    @property
    def path(self) -> Path:
        return self.root / self.invocation_id

    def reserve(self) -> None:
        if self.path.exists():
            raise StabilityError(f"invocation output already exists: {self.path}")
        self.path.mkdir(parents=True, exist_ok=False)

    def child(self, *parts: str) -> Path:
        if not parts or any(not _IDENTITY.fullmatch(part) for part in parts):
            raise StabilityError("invalid invocation-scoped output path")
        path = self.path.joinpath(*parts)
        if self.path not in path.parents:
            raise StabilityError("output escaped invocation namespace")
        return path


def output_scope(root: Path, invocation_id: str) -> OutputScope:
    return OutputScope(Path(root), make_invocation_id(invocation_id))


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise StabilityError(f"append-only artifact already exists: {path}")
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _bounded(value: Any, limit: int = 32_000) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"


def _redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[redacted]"
            if any(token in str(key).lower() for token in ("password", "secret", "dsn"))
            else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str) and any(
        token in value.lower() for token in ("password=", "passwd=", "postgresql://", "postgres://")
    ):
        return "[redacted]"
    return value


def write_failure(
    scope: OutputScope,
    *,
    identity: str,
    primary: BaseException,
    phase: str,
    producer_sha: str,
    protocol_digest: str,
    events: Sequence[Mapping[str, Any]],
    cleanup: Mapping[str, Any] | None = None,
    exit_code: int | None = None,
    stdout: Any = None,
    stderr: Any = None,
) -> dict[str, Any]:
    """Write one immutable failure record; cleanup is recorded independently."""
    path = scope.child("failures", f"{_require_identity(identity, 'failure identity')}.json")
    record: dict[str, Any] = {
        "format_version": FAILURE_FORMAT,
        "failure_id": identity,
        "invocation_id": scope.invocation_id,
        "producer_sha": producer_sha,
        "protocol_path": PROTOCOL_PATH,
        "protocol_semantic_digest": protocol_digest,
        "phase": phase,
        "exception": {"class": type(primary).__name__, "message": _bounded(primary)},
        "exit_code": exit_code,
        "stdout": _bounded(stdout),
        "stderr": _bounded(stderr),
        "phase_events": [dict(event) for event in events],
        "cleanup": dict(cleanup) if cleanup is not None else None,
        "status": "failed",
    }
    record = _with_digest(_redact(record))
    _atomic_json(path, record)
    return record


def _write_cleanup(
    scope: OutputScope, identity: str, operations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    record = _with_digest(
        {
            "format_version": CLEANUP_FORMAT,
            "cleanup_id": identity,
            "invocation_id": scope.invocation_id,
            "operations": [dict(operation) for operation in operations],
            "status": "complete"
            if all(item.get("status") == "complete" for item in operations)
            else "failed",
        }
    )
    _atomic_json(scope.child("cleanup", f"{identity}.json"), record)
    return record


def write_query_records(path: Path, records: Sequence[Mapping[str, Any]]) -> str:
    """Write deterministic gzip JSONL and return the actual byte hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise StabilityError(f"query evidence already exists: {path}")
    lines = b"".join(
        (json.dumps(dict(record), sort_keys=True, separators=(",", ":")) + "\n").encode()
        for record in records
    )
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(gzip.compress(lines, mtime=0))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    return sha256_file(path)


def _read_query_records(path: Path) -> list[dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return [json.loads(line) for line in stream if line.strip()]
    except (OSError, EOFError, json.JSONDecodeError) as exc:
        raise StabilityError(f"invalid query evidence: {path}: {exc}") from exc


def validate_query_evidence(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_query_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    if not path.is_file():
        raise StabilityError(f"query evidence is missing: {path}")
    actual = sha256_file(path)
    if expected_sha256 is not None and actual != expected_sha256:
        raise StabilityError(f"query evidence hash mismatch: {path}")
    return _check_query_records(_read_query_records(path), expected_query_ids)


def _validate_digest(value: Mapping[str, Any], label: str) -> None:
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise StabilityError(f"{label} semantic digest mismatch")


def canonical_union(
    method_memberships: Mapping[str, Sequence[str]], expected_methods: Sequence[str] = METHOD_ORDER
) -> tuple[str, ...]:
    if tuple(method_memberships) != tuple(expected_methods):
        raise StabilityError("method roster does not match frozen protocol")
    if any(not ids for ids in method_memberships.values()):
        raise StabilityError("empty method membership")
    union = tuple(sorted({candidate for ids in method_memberships.values() for candidate in ids}))
    if not union:
        raise StabilityError("empty canonical statistics union")
    return union


def filter_membership(union: Sequence[str], selected: Sequence[str]) -> tuple[str, ...]:
    union_set = set(union)
    selected_tuple = tuple(selected)
    if len(set(selected_tuple)) != len(selected_tuple) or not set(selected_tuple) <= union_set:
        raise StabilityError("method membership is not a subset of the canonical union")
    return tuple(candidate for candidate in union if candidate in set(selected_tuple))


def verify_oid_order_controls(
    parent: Mapping[str, Any], expected_order: Sequence[str]
) -> dict[str, Any]:
    relation_oid = parent.get("relation_oid")
    objects = list(parent.get("statistics_objects", []))
    if not isinstance(relation_oid, int) or relation_oid <= 0:
        raise StabilityError("missing relation OID")
    if not objects or any(item.get("relation_oid") != relation_oid for item in objects):
        raise StabilityError("statistics objects are not bound to the parent relation")
    ids = [item.get("candidate_id") for item in objects]
    if set(ids) != set(expected_order) or len(ids) != len(set(ids)):
        raise StabilityError("statistics object roster mismatch")
    if any(item.get("oid") == relation_oid for item in objects):
        raise StabilityError("relation OID was confused with a statistics-object OID")
    actual = [item["candidate_id"] for item in sorted(objects, key=lambda item: int(item["oid"]))]
    canonical = list(expected_order)
    status = "verified" if actual == canonical else "drifted"
    return {
        "relation_oid": relation_oid,
        "canonical_creation_order": canonical,
        "observed_oid_order": actual,
        "relative_order_status": status,
        "object_oids": {item["candidate_id"]: int(item["oid"]) for item in objects},
    }


def verify_parent_payloads(parent: Mapping[str, Any], expected_ids: Sequence[str]) -> None:
    payloads = parent.get("payloads", {})
    for candidate_id in expected_ids:
        item = payloads.get(candidate_id)
        if not isinstance(item, Mapping) or not item.get("payload_present"):
            raise StabilityError(f"missing native payload for {candidate_id}")
        if not _HEX64.fullmatch(str(item.get("payload_sha256", ""))):
            raise StabilityError(f"invalid payload digest for {candidate_id}")
    if not parent.get("ordinary_statistics_fingerprint"):
        raise StabilityError("missing ordinary-statistics fingerprint")


def verify_clone_controls(
    parent: Mapping[str, Any], clone: Mapping[str, Any], selected: Sequence[str]
) -> dict[str, Any]:
    expected = set(selected)
    retained = clone.get("retained_candidate_ids")
    if set(retained or ()) != expected or len(tuple(retained or ())) != len(expected):
        raise StabilityError("clone selected membership differs from frozen method membership")
    parent_payloads = parent.get("payloads", {})
    clone_payloads = clone.get("payloads", {})
    for candidate_id in selected:
        if clone_payloads.get(candidate_id, {}).get("payload_sha256") != parent_payloads.get(
            candidate_id, {}
        ).get("payload_sha256"):
            raise StabilityError(f"retained payload mismatch for {candidate_id}")
    if clone.get("ordinary_statistics_fingerprint") != parent.get(
        "ordinary_statistics_fingerprint"
    ):
        raise StabilityError("ordinary statistics changed in method clone")
    if int(clone.get("analyze_count", 0)) != 0:
        raise StabilityError("method clone was analyzed")
    if clone.get("unexpected_catalog_changes"):
        raise StabilityError("method clone has unexpected catalog changes")
    return {
        "membership_equal": True,
        "retained_payloads_equal": True,
        "ordinary_statistics_equal": True,
        "no_method_analyze": True,
        "causal_status": "shared-realization-verified",
    }


def _check_query_records(
    records: Sequence[Mapping[str, Any]], expected_ids: Sequence[str] | None = None
) -> list[dict[str, Any]]:
    if not records:
        raise StabilityError("query evidence is empty")
    ids = [record.get("query_id") for record in records]
    if any(not isinstance(item, str) or not item for item in ids) or len(set(ids)) != len(ids):
        raise StabilityError("query IDs are missing or duplicated")
    if expected_ids is not None and set(ids) != set(expected_ids):
        raise StabilityError("query IDs do not match the frozen workload")
    checked: list[dict[str, Any]] = []
    for record in records:
        try:
            truth = float(record["truth"])
            estimate = float(record["plan_rows"])
        except (KeyError, TypeError, ValueError) as exc:
            raise StabilityError("malformed query observation") from exc
        if not math.isfinite(truth) or not math.isfinite(estimate) or truth < 0 or estimate < 0:
            raise StabilityError("non-finite or negative query observation")
        expected = qerror(estimate, truth)
        if "qerror" in record and not math.isclose(
            float(record["qerror"]), expected, rel_tol=1e-12, abs_tol=1e-12
        ):
            raise StabilityError(f"q-error mismatch for {record['query_id']}")
        checked.append({**dict(record), "qerror": expected})
    return checked


def metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    checked = _check_query_records(records)
    values = [float(item["qerror"]) for item in checked]
    return {
        "query_count": len(checked),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
        "quantile_method": "linear-interpolation-n-minus-1",
        "qerror_contract": "qerror-cardinality-floor-1-v1",
        "lower_is_better": True,
    }


def paired_counts(
    reference: Sequence[Mapping[str, Any]], candidate: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    left = {item["query_id"]: float(item["qerror"]) for item in _check_query_records(reference)}
    right = {item["query_id"]: float(item["qerror"]) for item in _check_query_records(candidate)}
    if set(left) != set(right):
        raise StabilityError("paired query populations differ")
    result = {"improved": 0, "unchanged": 0, "worsened": 0}
    for query_id, value in right.items():
        if value < left[query_id]:
            result["improved"] += 1
        elif value > left[query_id]:
            result["worsened"] += 1
        else:
            result["unchanged"] += 1
    return result


def _rank(method_metrics: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    ordered = sorted((float(item["mean"]), method) for method, item in method_metrics.items())
    best = ordered[0][0]
    return {
        "ordered_methods": [method for _, method in ordered],
        "best_methods": [method for value, method in ordered if value == best],
        "best_mean": best,
    }


def aggregate_realizations(
    realizations: Sequence[Mapping[str, Any]], *, expected_realizations: int = 5
) -> dict[str, Any]:
    if len(realizations) != expected_realizations:
        raise IncompleteEvidenceError("all preregistered realizations are required")
    method_by_realization: list[dict[str, Mapping[str, Any]]] = []
    payload_fingerprints: dict[str, list[str]] = {}
    ordinary_fingerprints: list[str] = []
    for realization in realizations:
        if realization.get("status") != "complete":
            raise IncompleteEvidenceError("cannot aggregate an incomplete realization")
        arms = realization.get("method_arms", {})
        if set(arms) != set(METHOD_ORDER):
            raise IncompleteEvidenceError("method arm roster is incomplete")
        current: dict[str, Mapping[str, Any]] = {}
        for method in METHOD_ORDER:
            arm = arms[method]
            if arm.get("status") != "complete" or arm.get("causal_status") not in {
                "shared-realization-verified",
                "operational",
            }:
                raise IncompleteEvidenceError(f"method arm is not complete: {method}")
            records = _check_query_records(arm.get("records", []))
            current[method] = {"metrics": metrics(records), "records": records}
        method_by_realization.append(current)
        parent = realization.get("shared_parent", {})
        ordinary_fingerprints.append(str(parent.get("ordinary_statistics_fingerprint")))
        for candidate_id, payload in parent.get("payloads", {}).items():
            payload_fingerprints.setdefault(candidate_id, []).append(
                str(payload.get("payload_sha256"))
            )
    ranks = [
        _rank({method: current[method]["metrics"] for method in METHOD_ORDER})
        for current in method_by_realization
    ]
    by_method: dict[str, Any] = {}
    for method in METHOD_ORDER:
        values = [float(current[method]["metrics"]["mean"]) for current in method_by_realization]
        by_method[method] = {
            "realization_means": values,
            "min_mean": min(values),
            "max_mean": max(values),
            "descriptive_stddev_population": statistics.pstdev(values),
            "first_or_tie_count": sum(method in rank["best_methods"] for rank in ranks),
        }
    pairwise: dict[str, Any] = {}
    for left_index, left in enumerate(METHOD_ORDER):
        for right in METHOD_ORDER[left_index + 1 :]:
            key = f"{left}__vs__{right}"
            wins = ties = losses = 0
            for current in method_by_realization:
                a = current[left]["metrics"]["mean"]
                b = current[right]["metrics"]["mean"]
                if a < b:
                    wins += 1
                elif a > b:
                    losses += 1
                else:
                    ties += 1
            pairwise[key] = {"left_wins": wins, "ties": ties, "right_wins": losses}
    greedy = "greedy-ADD"
    singleton = "singleton-utility-top-k"
    paired = [
        float(current[greedy]["metrics"]["mean"] - current[singleton]["metrics"]["mean"])
        for current in method_by_realization
    ]
    reference_rank = [rank["ordered_methods"].index(greedy) + 1 for rank in ranks]
    return _with_digest(
        {
            "format_version": SUMMARY_FORMAT,
            "status": "complete",
            "realization_count": expected_realizations,
            "method_order": list(METHOD_ORDER),
            "per_method": by_method,
            "per_realization_ranking": ranks,
            "pairwise_method_win_tie_loss": pairwise,
            "paired_greedy_minus_singleton_mean_qerror": paired,
            "greedy_reference_rank": reference_rank,
            "payload_fingerprint_difference_fraction": {
                candidate: sum(value != values[0] for value in values[1:]) / max(len(values) - 1, 1)
                for candidate, values in payload_fingerprints.items()
            },
            "ordinary_statistics_fingerprint_difference_fraction": sum(
                value != ordinary_fingerprints[0] for value in ordinary_fingerprints[1:]
            )
            / max(len(ordinary_fingerprints) - 1, 1),
            "independence_unit": "native-ANALYZE-realization",
            "claims": "descriptive only; five realizations are not a population probability estimate",
        }
    )


@runtime_checkable
class StabilityDatabaseAdapter(Protocol):
    """Future live adapter contract; a live implementation must be explicit."""

    def create_parent(
        self, dataset: Mapping[str, Any], realization_id: str, union: Sequence[str]
    ) -> Mapping[str, Any]: ...
    def analyze_once(self, parent: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def clone_method(
        self, parent: Mapping[str, Any], method: str, selected: Sequence[str]
    ) -> Mapping[str, Any]: ...
    def explain(self, clone: Mapping[str, Any], query: Mapping[str, Any]) -> Mapping[str, Any]: ...
    def cleanup(self, state: Mapping[str, Any]) -> Mapping[str, Any]: ...


class LiveExecutionNotAuthorized(StabilityError):
    pass


class MockStabilityAdapter:
    """Deterministic in-memory adapter used by offline state-machine tests."""

    def __init__(self, plan_rows: Mapping[str, Mapping[str, int]] | None = None):
        self.calls: list[str] = []
        self.plan_rows = plan_rows or {}

    def create_parent(
        self, dataset: Mapping[str, Any], realization_id: str, union: Sequence[str]
    ) -> dict[str, Any]:
        self.calls.append("create_parent")
        objects = [
            {"candidate_id": candidate, "relation_oid": 700, "oid": 1000 + index}
            for index, candidate in enumerate(union)
        ]
        return {
            "relation_oid": 700,
            "statistics_objects": objects,
            "payloads": {
                candidate: {
                    "payload_sha256": hashlib.sha256(candidate.encode()).hexdigest(),
                    "payload_present": True,
                    "payload_bytes": len(candidate),
                }
                for candidate in union
            },
            "ordinary_statistics_fingerprint": "ordinary-fixed",
            "analyze_count": 0,
            "state": "created",
        }

    def analyze_once(self, parent: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append("analyze")
        return {
            **dict(parent),
            "analyze_count": int(parent.get("analyze_count", 0)) + 1,
            "state": "analyzed",
        }

    def clone_method(
        self, parent: Mapping[str, Any], method: str, selected: Sequence[str]
    ) -> dict[str, Any]:
        self.calls.append("clone")
        return {
            "retained_candidate_ids": list(selected),
            "payloads": {key: parent["payloads"][key] for key in selected},
            "ordinary_statistics_fingerprint": parent["ordinary_statistics_fingerprint"],
            "analyze_count": 0,
            "unexpected_catalog_changes": False,
        }

    def explain(self, clone: Mapping[str, Any], query: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append("explain")
        method = str(query.get("method", ""))
        query_id = str(query["query_id"])
        return {
            "plan_rows": self.plan_rows.get(method, {}).get(query_id, int(query.get("truth", 1)))
        }

    def cleanup(self, state: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append("cleanup")
        return {"status": "complete"}


def dry_run_plan(
    protocol: Mapping[str, Any], *, invocation_id: str, output_root: Path
) -> dict[str, Any]:
    if protocol.get("format_version") != "native-analyze-stability-protocol-v1":
        raise StabilityError("unsupported Native ANALYZE protocol")
    return _with_digest(
        {
            "format_version": HARNESS_FORMAT,
            "mode": "offline-dry-run",
            "invocation_id": make_invocation_id(invocation_id),
            "output_root": str(Path(output_root) / invocation_id),
            "datasets": list(protocol.get("datasets", {}).get("primary", PRIMARY_DATASETS)),
            "realizations": int(protocol.get("realizations", {}).get("count", 5)),
            "method_arms_per_realization": len(protocol.get("method_roster", METHOD_ORDER)),
            "planned_analyze_calls": len(
                protocol.get("datasets", {}).get("primary", PRIMARY_DATASETS)
            )
            * 5,
            "planned_physical_explain_calls": len(
                protocol.get("datasets", {}).get("primary", PRIMARY_DATASETS)
            )
            * 5
            * 9
            * 10000,
            "database_connections_opened": 0,
            "formal_execution": False,
        }
    )


def _method_query_records(
    adapter: StabilityDatabaseAdapter,
    clone: Mapping[str, Any],
    method: str,
    queries: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    output = []
    for query in queries:
        observation = adapter.explain({**clone, "method": method}, {**query, "method": method})
        estimate = observation.get("plan_rows")
        record = {"query_id": query["query_id"], "truth": query["truth"], "plan_rows": estimate}
        record["qerror"] = qerror(float(estimate), float(query["truth"]))
        if "sql_digest" in query:
            record["sql_digest"] = query["sql_digest"]
        output.append(record)
    return _check_query_records(output, [str(query["query_id"]) for query in queries])


def execute_invocation(
    protocol: Mapping[str, Any],
    *,
    output_root: Path,
    invocation_id: str,
    dataset_id: str,
    method_memberships: Mapping[str, Sequence[str]],
    queries: Sequence[Mapping[str, Any]],
    adapter: StabilityDatabaseAdapter | None = None,
    producer_sha: str = "unknown",
    realization_numbers: Sequence[int] = (1,),
    live_authorized: bool = False,
) -> dict[str, Any]:
    """Run one invocation through an explicitly injected database adapter.

    ``MockStabilityAdapter`` is the only adapter permitted without the live
    execution gate.  A future stock adapter may use this same orchestration,
    but it must be passed with ``live_authorized=True`` by an explicitly
    authorized formal command; there is no DSN or host fallback here.
    """
    if adapter is None:
        raise LiveExecutionNotAuthorized("a database adapter must be explicitly supplied")
    if not isinstance(adapter, MockStabilityAdapter) and not live_authorized:
        raise LiveExecutionNotAuthorized("live adapters require explicit execution authorization")
    if dataset_id not in protocol.get("datasets", {}).get("primary", []):
        raise StabilityError(f"dataset is not a primary protocol dataset: {dataset_id}")
    if tuple(method_memberships) != tuple(METHOD_ORDER):
        raise StabilityError("formal method membership roster does not match the frozen protocol")
    binding = protocol.get("dataset_bindings", {}).get(dataset_id, {})
    frozen_memberships = binding.get("method_memberships")
    if frozen_memberships is not None and frozen_memberships != {
        method: list(method_memberships[method]) for method in METHOD_ORDER
    }:
        raise StabilityError("method memberships differ from the frozen dataset binding")
    if live_authorized and (tuple(realization_numbers) != (1, 2, 3, 4, 5) or len(queries) != 10000):
        raise StabilityError("formal execution requires five realizations and 10,000 queries")
    scope = output_scope(output_root, invocation_id)
    scope.reserve()
    events: list[dict[str, Any]] = []
    method_order = tuple(method_memberships)
    union = canonical_union(method_memberships, method_order)
    manifest = _with_digest(
        {
            "format_version": INVOCATION_FORMAT,
            "invocation_id": invocation_id,
            "producer_sha": producer_sha,
            "protocol_semantic_digest": protocol.get("semantic_digest"),
            "dataset_ids": [dataset_id],
            "formal_execution": bool(live_authorized),
            "query_ids": [str(query["query_id"]) for query in queries],
            "query_identity_digest": semantic_digest([str(query["query_id"]) for query in queries]),
            "workload_binding": binding.get("workload"),
            "truth_binding": binding.get("truth_binding"),
            "status": "running",
        }
    )
    _atomic_json(scope.child("invocation-manifest.json"), manifest)
    realization_values: list[dict[str, Any]] = []
    try:
        requested_realizations = tuple(realization_numbers)
        if not requested_realizations or len(set(requested_realizations)) != len(
            requested_realizations
        ):
            raise StabilityError("realization roster is empty or duplicated")
        if any(
            not isinstance(number, int) or number < 1 or number > 5
            for number in requested_realizations
        ):
            raise StabilityError("realization roster must contain values in 1..5")
        for realization_number in requested_realizations:
            realization_id = make_realization_id(invocation_id, dataset_id, realization_number)
            events.append({"phase": "realization-started", "realization_id": realization_id})
            parent = adapter.analyze_once(
                adapter.create_parent({"dataset_id": dataset_id}, realization_id, union)
            )
            order = verify_oid_order_controls(parent, union)
            verify_parent_payloads(parent, union)
            arms: dict[str, Any] = {}
            for method in method_order:
                selected = filter_membership(union, method_memberships[method])
                clone = adapter.clone_method(parent, method, selected)
                controls = verify_clone_controls(parent, clone, selected)
                controls["causal_status"] = (
                    "shared-realization-verified"
                    if order["relative_order_status"] == "verified"
                    else "invalid-control-evidence"
                )
                records = _method_query_records(adapter, clone, method, queries)
                arm = _with_digest(
                    {
                        "format_version": ARM_FORMAT,
                        "arm_id": make_method_arm_id(realization_id, method),
                        "method_id": method,
                        "status": "complete",
                        "causal_status": controls["causal_status"],
                        "selected_membership": list(selected),
                        "controls": controls,
                        "records": records,
                        "metrics": metrics(records),
                        "explain_count": len(records),
                    }
                )
                arm_dir = scope.child(
                    "datasets", dataset_id, f"realization-{realization_number:02d}"
                )
                evidence_path = arm_dir / f"{method}.jsonl.gz"
                evidence_sha = write_query_records(evidence_path, records)
                arm["query_evidence"] = {
                    "path": str(evidence_path.relative_to(scope.path)),
                    "sha256": evidence_sha,
                    "format": QUERY_FORMAT,
                }
                arm["semantic_digest"] = semantic_digest(_body(arm))
                _atomic_json(arm_dir / f"{method}.json", arm)
                arms[method] = arm
            realization = _with_digest(
                {
                    "format_version": REALIZATION_FORMAT,
                    "realization_id": realization_id,
                    "dataset_id": dataset_id,
                    "status": "complete",
                    "shared_parent": {**dict(parent), "oid_controls": order},
                    "method_arms": arms,
                    "analyze_count": 1,
                    "physical_explain_count": sum(arm["explain_count"] for arm in arms.values()),
                    "failure": None,
                }
            )
            cleanup = _write_cleanup(
                scope,
                f"cleanup-{dataset_id}-{realization_number:02d}",
                [
                    {
                        "realization_id": realization_id,
                        **dict(
                            adapter.cleanup(
                                {"dataset_id": dataset_id, "realization_id": realization_id}
                            )
                        ),
                    }
                ],
            )
            realization["cleanup_status"] = cleanup["status"]
            realization["semantic_digest"] = semantic_digest(_body(realization))
            _atomic_json(
                scope.child(
                    "datasets",
                    dataset_id,
                    f"realization-{realization_number:02d}",
                    "realization.json",
                ),
                realization,
            )
            realization_values.append(realization)
        cleanup = _write_cleanup(scope, f"cleanup-{invocation_id}", [{"status": "complete"}])
        result = _with_digest(
            {
                "format_version": INVOCATION_FORMAT,
                "invocation_id": invocation_id,
                "producer_sha": producer_sha,
                "protocol_semantic_digest": protocol.get("semantic_digest"),
                "status": "complete",
                "formal_execution": bool(live_authorized),
                "query_ids": [str(query["query_id"]) for query in queries],
                "query_identity_digest": semantic_digest(
                    [str(query["query_id"]) for query in queries]
                ),
                "workload_binding": binding.get("workload"),
                "truth_binding": binding.get("truth_binding"),
                "completed_datasets": 1,
                "completed_realizations": len(realization_values),
                "completed_method_arms": sum(
                    len(item["method_arms"]) for item in realization_values
                ),
                "analyze_count": sum(item["analyze_count"] for item in realization_values),
                "physical_explain_count": sum(
                    item["physical_explain_count"] for item in realization_values
                ),
                "failed_or_partial_arms": 0,
                "cleanup_status": cleanup["status"],
                "realizations": realization_values,
            }
        )
        _atomic_json(scope.child("invocation-result.json"), result)
        return result
    except Exception as exc:
        cleanup = _write_cleanup(
            scope,
            f"cleanup-{invocation_id}",
            [{"operation": "adapter-cleanup", "status": "complete"}],
        )
        failure = {
            "format_version": FAILURE_FORMAT,
            "failure_id": f"failure-{invocation_id}",
            "invocation_id": invocation_id,
            "producer_sha": producer_sha,
            "protocol_semantic_digest": protocol.get("semantic_digest"),
            "phase": events[-1]["phase"] if events else "invocation-started",
            "exception": {"class": type(exc).__name__, "message": str(exc)},
            "phase_events": events,
            "cleanup": cleanup,
            "status": "failed",
        }
        _atomic_json(
            scope.child("failures", f"failure-{invocation_id}.json"), _with_digest(failure)
        )
        raise


def execute_mock_invocation(
    protocol: Mapping[str, Any],
    *,
    output_root: Path,
    invocation_id: str,
    dataset_id: str,
    method_memberships: Mapping[str, Sequence[str]],
    queries: Sequence[Mapping[str, Any]],
    adapter: StabilityDatabaseAdapter | None = None,
    producer_sha: str = "mock-producer",
    realization_numbers: Sequence[int] = (1,),
) -> dict[str, Any]:
    """Convenience wrapper used by offline tests; it cannot open a database."""
    return execute_invocation(
        protocol,
        output_root=output_root,
        invocation_id=invocation_id,
        dataset_id=dataset_id,
        method_memberships=method_memberships,
        queries=queries,
        adapter=adapter,
        producer_sha=producer_sha,
        realization_numbers=realization_numbers,
        live_authorized=False,
    )


def execute_campaign(
    protocol: Mapping[str, Any],
    *,
    output_root: Path,
    campaign_id: str,
    datasets: Mapping[str, Mapping[str, Any]],
    adapter: StabilityDatabaseAdapter | None = None,
    producer_sha: str = "unknown",
    realization_numbers: Sequence[int] = (1, 2, 3, 4, 5),
    live_authorized: bool = False,
) -> dict[str, Any]:
    """Execute dataset-scoped invocations under one append-only campaign.

    Each dataset receives its own invocation namespace, while the campaign
    manifest records the complete roster.  This prevents a partial dataset
    failure from overwriting another dataset's evidence and makes resumption
    require a new dataset-scoped identity.
    """
    campaign = make_invocation_id(campaign_id)
    campaign_root = Path(output_root) / campaign
    if campaign_root.exists():
        raise StabilityError(f"campaign output already exists: {campaign_root}")
    campaign_root.mkdir(parents=True)
    if set(datasets) != set(protocol.get("datasets", {}).get("primary", [])):
        raise StabilityError("campaign dataset roster does not match the primary protocol roster")
    dataset_results: dict[str, Any] = {}
    try:
        for dataset_id, binding in datasets.items():
            scoped_id = f"{campaign}-{dataset_id}"
            dataset_results[dataset_id] = execute_invocation(
                protocol,
                output_root=campaign_root,
                invocation_id=scoped_id,
                dataset_id=dataset_id,
                method_memberships=binding["method_memberships"],
                queries=binding["queries"],
                adapter=adapter,
                producer_sha=producer_sha,
                realization_numbers=realization_numbers,
                live_authorized=live_authorized,
            )
        result = _with_digest(
            {
                "format_version": HARNESS_FORMAT,
                "campaign_id": campaign,
                "status": "complete",
                "formal_execution": bool(live_authorized),
                "dataset_ids": list(datasets),
                "completed_datasets": len(dataset_results),
                "completed_realizations": sum(
                    item["completed_realizations"] for item in dataset_results.values()
                ),
                "completed_method_arms": sum(
                    item["completed_method_arms"] for item in dataset_results.values()
                ),
                "analyze_count": sum(item["analyze_count"] for item in dataset_results.values()),
                "physical_explain_count": sum(
                    item["physical_explain_count"] for item in dataset_results.values()
                ),
                "dataset_invocations": {
                    dataset: item["invocation_id"] for dataset, item in dataset_results.items()
                },
            }
        )
        _atomic_json(campaign_root / "campaign-result.json", result)
        return result
    except Exception as exc:
        failure = _with_digest(
            {
                "format_version": FAILURE_FORMAT,
                "failure_id": f"failure-{campaign}",
                "campaign_id": campaign,
                "status": "failed",
                "exception": {"class": type(exc).__name__, "message": str(exc)},
                "completed_dataset_ids": list(dataset_results),
            }
        )
        _atomic_json(campaign_root / "campaign-failure.json", failure)
        raise


def validate_invocation_artifact(
    path: Path, *, expected_protocol_digest: str | None = None
) -> dict[str, Any]:
    value = read_json(path)
    _validate_digest(value, "invocation")
    if value.get("format_version") != INVOCATION_FORMAT:
        raise StabilityError("unsupported invocation artifact")
    if (
        expected_protocol_digest is not None
        and value.get("protocol_semantic_digest") != expected_protocol_digest
    ):
        raise StabilityError("invocation protocol binding mismatch")
    if value.get("status") == "complete":
        if value.get("failed_or_partial_arms") != 0:
            raise StabilityError("complete invocation contains partial arms")
        if value.get("cleanup_status") != "complete":
            raise StabilityError("complete invocation lacks cleanup evidence")
        expected_query_ids = value.get("query_ids")
        if not isinstance(expected_query_ids, list) or value.get(
            "query_identity_digest"
        ) != semantic_digest(expected_query_ids):
            raise StabilityError("invocation query identity binding is missing or invalid")
        for realization in value.get("realizations", []):
            _validate_digest(realization, "realization")
            if realization.get("status") != "complete":
                raise StabilityError("complete invocation contains incomplete realization")
            for method, arm in realization.get("method_arms", {}).items():
                _validate_digest(arm, f"method arm {method}")
                evidence = arm.get("query_evidence", {})
                evidence_path = path.parent / evidence.get("path", "")
                records = validate_query_evidence(
                    evidence_path, expected_sha256=evidence.get("sha256")
                )
                if records != _check_query_records(arm.get("records", []), expected_query_ids):
                    raise StabilityError(
                        f"embedded and raw query evidence differ for method arm {method}"
                    )
                if len(records) != arm.get("metrics", {}).get("query_count"):
                    raise StabilityError(f"query count mismatch for method arm {method}")
                if metrics(records) != arm.get("metrics"):
                    raise StabilityError(
                        f"stored metrics do not match raw evidence for method arm {method}"
                    )
                if (
                    arm.get("causal_status") == "shared-realization-verified"
                    and realization.get("shared_parent", {})
                    .get("oid_controls", {})
                    .get("relative_order_status")
                    != "verified"
                ):
                    raise StabilityError(f"method arm {method} overstates OID/order controls")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "formal_execution": value.get("formal_execution", False),
    }


def load_and_validate_protocol(root: Path) -> dict[str, Any]:
    path = root / PROTOCOL_PATH
    value = read_json(path)
    validate_protocol(value, root)
    return value


def validate_readiness_v2(
    value: Mapping[str, Any], root: Path, *, expected_producer_sha: str | None = None
) -> dict[str, str]:
    format_version = value.get("format_version")
    if not isinstance(format_version, str) or not re.fullmatch(
        r"native-analyze-stability-readiness-review-v[2-9][0-9]*", format_version
    ):
        raise StabilityError("unsupported Native ANALYZE readiness-v2 artifact")
    _validate_digest(value, "readiness-v2")
    producer = value.get("review_producer_sha")
    if not isinstance(producer, str) or not re.fullmatch(r"[0-9a-f]{40}", producer):
        raise StabilityError("readiness-v2 producer SHA is invalid")
    if expected_producer_sha is not None and producer != expected_producer_sha:
        raise StabilityError("readiness-v2 producer binding mismatch")
    protocol = value.get("protocol", {})
    actual_protocol = _source_ref(root, protocol.get("path", ""))
    if protocol != actual_protocol:
        raise StabilityError("readiness-v2 protocol binding mismatch")
    supersedes = value.get("supersedes", {})
    actual_previous = _source_ref(root, supersedes.get("path", ""))
    if supersedes != actual_previous:
        raise StabilityError("readiness-v2 previous readiness binding mismatch")
    for source in value.get("source_coverage", {}).values():
        actual = _source_ref(root, source.get("path", ""))
        if dict(source) != actual:
            raise StabilityError(f"readiness-v2 source binding mismatch: {source.get('path')}")
    gates = value.get("gates", {})
    expected_gates = {
        "protocol_ready": True,
        "source_bindings_ready": True,
        "harness_implementation_ready": True,
        "offline_tests_passed": True,
        "formal_execution_authorized": False,
        "scientific_results_available": False,
    }
    if any(gates.get(key) is not expected for key, expected in expected_gates.items()):
        raise StabilityError("readiness-v2 gate status is inconsistent")
    integration_tested = gates.get("real_postgresql_integration_tested")
    if integration_tested is True:
        smoke_ref = value.get("integration_smoke")
        if not isinstance(smoke_ref, Mapping):
            raise StabilityError("integration-tested readiness is missing its smoke binding")
        actual_smoke = _source_ref(root, str(smoke_ref.get("path", "")))
        if dict(smoke_ref) != actual_smoke:
            raise StabilityError("readiness integration smoke binding mismatch")
        smoke = read_json(root / str(smoke_ref["path"]))
        if (
            smoke.get("format_version") != "native-analyze-stability-integration-smoke-v1"
            or smoke.get("scientific_eligibility") != "integration-readiness-only"
            or smoke.get("cleanup", {}).get("status") != "complete"
        ):
            raise StabilityError(
                "readiness integration smoke is not a completed readiness-only smoke"
            )
    elif integration_tested is not False:
        raise StabilityError("readiness integration gate must be explicitly true or false")
    execution = value.get("execution_status", {})
    if (
        execution.get("formal_status") != "NOT_EXECUTED"
        or execution.get("formal_invocation_count") != 0
    ):
        raise StabilityError("readiness-v2 records formal execution")
    return {"status": "valid", "semantic_digest": str(value["semantic_digest"])}


__all__ = [
    "ARM_FORMAT",
    "CLEANUP_FORMAT",
    "FAILURE_FORMAT",
    "HARNESS_FORMAT",
    "INVOCATION_FORMAT",
    "REALIZATION_FORMAT",
    "SUMMARY_FORMAT",
    "IncompleteEvidenceError",
    "LiveExecutionNotAuthorized",
    "MockStabilityAdapter",
    "OutputScope",
    "StabilityDatabaseAdapter",
    "StabilityError",
    "aggregate_realizations",
    "canonical_union",
    "dry_run_plan",
    "execute_campaign",
    "execute_invocation",
    "execute_mock_invocation",
    "filter_membership",
    "load_and_validate_protocol",
    "make_dataset_id",
    "make_invocation_id",
    "make_method_arm_id",
    "make_realization_id",
    "metrics",
    "output_scope",
    "paired_counts",
    "validate_invocation_artifact",
    "validate_query_evidence",
    "validate_readiness_v2",
    "verify_clone_controls",
    "verify_oid_order_controls",
    "verify_parent_payloads",
    "write_failure",
    "write_query_records",
]
