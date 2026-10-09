"""OID/order-sensitivity ablation protocol, preflight, and live evaluator.

This module is intentionally independent of the RQ1b selector.  It binds the
already sealed RQ1b memberships, captures one fixed native payload realization
per dataset, and changes only the activation/deployment order.  The module
does not read any test-side artifact while constructing the preflight or
running the valid-side comparisons.
"""

from __future__ import annotations

import gc
import gzip
import hashlib
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paper_baseline import qerror
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq1_workload_generalization_live import (
    CENSUS13_SPEC,
    DMV11_SPEC,
    FOREST10_SPEC,
    POWER7_SPEC,
    RQ1BDatasetSpec,
    _validate_managed_lab_dsns,
    prepare_frozen_advisor_launcher,
    prepare_power7_rq1b_formal_labs,
)
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_STOCK_POSTGRES_SHA,
    formal_system_freeze_v2_identity,
    validate_system_freeze_v2,
    verify_frozen_systems_v2,
)

FORMAT = "postgresql-extstats-oid-order-sensitivity-v1"
PREFLIGHT_FORMAT = "postgresql-extstats-oid-order-sensitivity-preflight-v1"
SUMMARY_FORMAT = "postgresql-extstats-oid-order-sensitivity-summary-v1"
EXPERIMENT_ID = "postgresql-extstats-oid-order-sensitivity-v1"
PROTOCOL_PATH = Path("paper/oid-order-sensitivity-protocol-v1.json")
PREFLIGHT_PATH = Path("experiments/oid-order-sensitivity-v1/preflight-v1.json")
OUTPUT_ROOT = Path("experiments/oid-order-sensitivity-v1")
SEEDS = (17, 29, 43, 71, 101)
DATASET_SPECS = (CENSUS13_SPEC, FOREST10_SPEC, POWER7_SPEC, DMV11_SPEC)
QUERY_COUNT = 10_000
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
COLLISION_POLICY = "left-rotate-by-one-until-unique"
READINESS_PATH = OUTPUT_ROOT / "attempts" / "attempt-002" / "readiness-v1.json"
READINESS_FORMAT = "postgresql-extstats-oid-order-sensitivity-readiness-v1"


@dataclass
class ExecutionState:
    """Owned resources and phase history for one non-retryable invocation."""

    invocation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    phase: str = "preflight"
    phases: list[dict[str, Any]] = field(default_factory=list)
    stock_owned: bool = False
    patched_owned: bool = False
    snapshot_completed: bool = False
    sandbox_prepared: bool = False
    hypothetical_active: bool = False
    physical_deployment_started: bool = False
    synthetic_fixture_created: bool = False
    cleanup: list[dict[str, Any]] = field(default_factory=list)

    def transition(self, phase: str) -> None:
        self.phase = phase
        self.phases.append({"phase": phase, "timestamp_monotonic": time.monotonic()})


class CommandFailure(RuntimeError):
    """A command failure retaining diagnostics for append-only provenance."""

    def __init__(
        self,
        command: Sequence[str],
        returncode: int,
        stdout: str,
        stderr: str,
        elapsed_seconds: float,
    ) -> None:
        self.command = tuple(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.elapsed_seconds = elapsed_seconds
        super().__init__(f"command failed ({returncode}): {_redact_command(command)}")


def _redact_command(command: Sequence[str]) -> list[str]:
    return [re.sub(r"(password=)[^ ]+", r"\1<redacted>", str(item)) for item in command]


def _redact_text(value: str) -> str:
    return re.sub(r"(password=)[^\s]+", r"\1<redacted>", value)


def _atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    """Create one artifact without replacing an existing attempt record."""

    path = Path(path)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"append-only artifact already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode("utf-8")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(fd, payload[offset:])
            os.fsync(fd)
        finally:
            os.close(fd)
        # link() gives collision rejection even if another writer races us.
        os.link(temporary, path)
        os.unlink(temporary)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_failure_artifact(
    path: Path,
    *,
    producer_sha: str,
    preflight_path: Path,
    preflight: Mapping[str, Any],
    state: ExecutionState,
    exception: BaseException,
    cleanup_errors: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Persist one append-only failure record without masking the primary error."""

    failure: dict[str, Any] = {
        "format_version": "postgresql-extstats-oid-order-sensitivity-failed-attempt-v2",
        "experiment_id": EXPERIMENT_ID,
        "attempt_index": 2,
        "status": "non-evidence-remediation-or-pre-execution-failure",
        "invocation_id": state.invocation_id,
        "scientific_producer_sha": producer_sha,
        "preflight": {
            "path": str(preflight_path),
            "semantic_digest": preflight.get("semantic_digest"),
            "byte_sha256": sha256_file(preflight_path),
        },
        "phase": state.phase,
        "phase_transitions": [item["phase"] for item in state.phases],
        "resource_state": {
            "stock_owned": state.stock_owned,
            "patched_owned": state.patched_owned,
            "snapshot_completed": state.snapshot_completed,
            "sandbox_prepared": state.sandbox_prepared,
            "hypothetical_active": state.hypothetical_active,
            "physical_deployment_started": state.physical_deployment_started,
            "synthetic_fixture_created": state.synthetic_fixture_created,
        },
        "cleanup": [dict(item) for item in state.cleanup],
        "exception": {
            "type": f"{type(exception).__module__}.{type(exception).__name__}",
            "message": _redact_text(str(exception)),
        },
        "cleanup_errors": [dict(item) for item in cleanup_errors],
        "execution_boundary": {
            "explain_count": 0,
            "rq1b_w_test_accessed": False,
            "scientific_evidence_eligible": False,
        },
        "formal_execution": "NOT_EXECUTED" if state.phase == "preflight" else "FAILED",
        "formal_invocation_count": 0,
        "retry_performed": False,
        "evidence_eligible": False,
    }
    if isinstance(exception, CommandFailure):
        failure["command"] = {
            "argv": _redact_command(exception.command),
            "returncode": exception.returncode,
            "elapsed_seconds": exception.elapsed_seconds,
            "stdout": _redact_text(exception.stdout),
            "stderr": _redact_text(exception.stderr),
        }
    failure["semantic_digest"] = semantic_digest(failure)
    _atomic_write_json(path, failure)
    return failure


def build_readiness_artifact(
    *, root: Path, implementation_commit_sha: str, verification: Mapping[str, Any]
) -> dict[str, Any]:
    """Build append-only readiness metadata; this never executes scientific work."""

    protocol_path = root / PROTOCOL_PATH
    preflight_path = root / PREFLIGHT_PATH
    failure_path = root / OUTPUT_ROOT / "failed-attempts/attempt-001/failure-v1.json"
    protocol = read_json(protocol_path)
    preflight = read_json(preflight_path)
    failure = read_json(failure_path)
    value: dict[str, Any] = {
        "format_version": READINESS_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "READY_FOR_INDEPENDENT_REVIEW",
        "formal_execution": "NOT_EXECUTED",
        "formal_invocation_count": 0,
        "implementation_commit_sha": implementation_commit_sha,
        "protocol": {
            "path": PROTOCOL_PATH.as_posix(),
            "semantic_digest": protocol["semantic_digest"],
            "byte_sha256": sha256_file(protocol_path),
        },
        "original_preflight": {
            "path": PREFLIGHT_PATH.as_posix(),
            "semantic_digest": preflight["semantic_digest"],
            "byte_sha256": sha256_file(preflight_path),
        },
        "attempt_001_failure": {
            "path": failure_path.relative_to(root).as_posix(),
            "semantic_digest": failure["semantic_digest"],
            "byte_sha256": sha256_file(failure_path),
        },
        "treatment_identity": {
            "seeds": list(SEEDS),
            "collision_policy": COLLISION_POLICY,
            "datasets": {
                item["dataset_id"]: {
                    "selected_k": len(item["reference_order"]),
                    "permutations": item["permutations"],
                }
                for item in protocol["datasets"]
            },
        },
        "verification": dict(verification),
        "unresolved_blockers": [],
        "scientific_execution": {
            "attempt_002_started": False,
            "postgresql_started": False,
            "advisor_invoked": False,
            "explain_executed": False,
            "rq1b_w_test_accessed": False,
        },
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def write_readiness_artifact(
    *, root: Path, implementation_commit_sha: str, verification: Mapping[str, Any]
) -> dict[str, Any]:
    value = build_readiness_artifact(
        root=root,
        implementation_commit_sha=implementation_commit_sha,
        verification=verification,
    )
    _atomic_write_json(root / READINESS_PATH, value)
    return value


def validate_readiness_artifact(value: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    """Validate append-only Attempt 2 readiness metadata and frozen links."""

    if value.get("format_version") != READINESS_FORMAT:
        raise ValueError("unsupported OID-order readiness format")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order readiness semantic digest mismatch")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("OID-order readiness experiment identity drift")
    if value.get("status") != "READY_FOR_INDEPENDENT_REVIEW":
        raise ValueError("OID-order readiness status is not review-ready")
    if value.get("formal_execution") != "NOT_EXECUTED":
        raise ValueError("OID-order readiness formal execution drift")
    if value.get("formal_invocation_count") != 0:
        raise ValueError("OID-order readiness invocation count drift")
    implementation_sha = value.get("implementation_commit_sha")
    if not isinstance(implementation_sha, str) or not re.fullmatch(
        r"[0-9a-f]{40}", implementation_sha
    ):
        raise ValueError("OID-order readiness implementation binding is invalid")
    protocol_path = root / PROTOCOL_PATH
    preflight_path = root / PREFLIGHT_PATH
    failure_path = root / OUTPUT_ROOT / "failed-attempts/attempt-001/failure-v1.json"
    for binding_name, path in (
        ("protocol", protocol_path),
        ("original_preflight", preflight_path),
        ("attempt_001_failure", failure_path),
    ):
        binding = value.get(binding_name)
        if not isinstance(binding, Mapping):
            raise TypeError(f"OID-order readiness {binding_name} binding is missing")
        if binding.get("path") != path.relative_to(root).as_posix():
            raise ValueError(f"OID-order readiness {binding_name} path drift")
        if binding.get("byte_sha256") != sha256_file(path):
            raise ValueError(f"OID-order readiness {binding_name} byte SHA drift")
        actual_semantic = read_json(path).get("semantic_digest")
        if binding.get("semantic_digest") != actual_semantic:
            raise ValueError(f"OID-order readiness {binding_name} semantic binding drift")
    validate_protocol(read_json(protocol_path), root=root)
    validate_preflight(read_json(preflight_path), root=root)
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _body(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _require_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{label} must be a SHA-256 digest")
    int(value, 16)
    return value


def deterministic_orders(reference: Sequence[str]) -> list[dict[str, Any]]:
    """Return reference, reverse, and the five frozen seeded permutations."""

    values = list(reference)
    if not values or len(values) != len(set(values)):
        raise ValueError("reference membership must be non-empty and unique")
    orders: list[dict[str, Any]] = [
        {"permutation_id": "reference", "kind": "reference", "order": list(values)},
        {"permutation_id": "reverse", "kind": "reverse", "order": list(reversed(values))},
    ]
    seen = {tuple(item["order"]) for item in orders}
    for seed in SEEDS:
        candidate = list(values)
        random.Random(seed).shuffle(candidate)
        collision = tuple(candidate) in seen
        rotations = 0
        while tuple(candidate) in seen:
            candidate = candidate[1:] + candidate[:1]
            rotations += 1
            if rotations >= len(values):
                raise ValueError("collision policy could not produce a unique permutation")
        orders.append(
            {
                "permutation_id": f"random-seed-{seed}",
                "kind": "random",
                "seed": seed,
                "order": candidate,
                "collision_detected": collision,
                "collision_resolution_rotations": rotations,
            }
        )
        seen.add(tuple(candidate))
    if len(orders) != 7:
        raise AssertionError("the OID-order roster must contain exactly seven arms")
    return orders


def _canonical_valid_records(
    data_root: Path, dataset_key: str, expected_sha256: str
) -> list[dict[str, Any]]:
    path = data_root / "arecel" / "audit-v1" / f"{dataset_key}.canonical.jsonl.gz"
    if not path.is_file():
        raise FileNotFoundError(f"missing audited canonical workload: {path}")
    if sha256_file(path) != expected_sha256:
        raise ValueError(f"{dataset_key} canonical workload byte SHA drift")
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            record = json.loads(line)
            if record.get("split") == "valid":
                records.append(
                    {
                        "index": int(record["index"]),
                        "query_id": str(record["query_id"]),
                        "source_query_sha256": str(record["source_query_sha256"]),
                    }
                )
    if len(records) != QUERY_COUNT:
        raise ValueError(f"{dataset_key} valid canonical workload count is not 10000")
    if [item["index"] for item in records] != list(range(QUERY_COUNT)):
        raise ValueError(f"{dataset_key} valid canonical workload indices are not contiguous")
    if len({item["query_id"] for item in records}) != QUERY_COUNT:
        raise ValueError(f"{dataset_key} valid canonical workload IDs are not unique")
    if len({item["source_query_sha256"] for item in records}) != QUERY_COUNT:
        raise ValueError(f"{dataset_key} canonical source hashes are not unique")
    return records


def _valid_workload(root: Path, data_root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    import tempfile

    with tempfile.TemporaryDirectory(prefix=f"oid-order-{spec.runtime_name}-") as directory:
        path = Path(directory) / "valid-workload.json"
        identity = spec.dataset_module.extract_workload(path, data_root, split="valid")
        workload = read_json(path)
    if identity["sha256"] != spec.valid_workload_sha256:
        raise ValueError(f"{spec.dataset_id} valid workload SHA drift")
    canonical_path = data_root / "arecel" / "audit-v1" / f"{spec.runtime_name}.canonical.jsonl.gz"
    canonical = _canonical_valid_records(
        data_root, spec.runtime_name, spec.valid_canonical_workload_sha256
    )
    if len(workload.get("queries", [])) != QUERY_COUNT:
        raise ValueError(f"{spec.dataset_id} extracted valid workload count is not 10000")
    queries: list[dict[str, Any]] = []
    for index, (query, source) in enumerate(zip(workload["queries"], canonical, strict=True)):
        expected_id = f"{spec.valid_workload_id.removesuffix('_v1')}_{index:06d}"
        if query.get("query_id") != expected_id:
            raise ValueError(f"{spec.dataset_id} valid query ID drift at {index}")
        sql = str(query["sql"])
        queries.append(
            {
                "index": index,
                "query_id": query["query_id"],
                "source_query_sha256": source["source_query_sha256"],
                "sql_sha256": _sha256_text(sql),
            }
        )
    return {
        "workload_id": identity["workload_id"],
        "sha256": identity["sha256"],
        "canonical_source_sha256": sha256_file(canonical_path),
        "query_count": QUERY_COUNT,
        "queries": queries,
    }


def _valid_workload_isolated(root: Path, data_root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    """Extract one dataset in a short-lived process to bound adapter memory."""

    code = (
        "import json; from pathlib import Path; "
        "from extstats_advisor_research.oid_order_sensitivity import DATASET_SPECS, _valid_workload; "
        "spec = next(item for item in DATASET_SPECS if item.runtime_name == __import__('sys').argv[1]); "
        "print(json.dumps(_valid_workload(Path(__import__('sys').argv[2]), Path(__import__('sys').argv[3]), spec), separators=(',', ':')))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, spec.runtime_name, str(root), str(data_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"valid workload extraction failed for {spec.dataset_id}: {result.stderr}"
        )
    return json.loads(result.stdout)


def _selected_membership(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    from extstats_advisor.deployment.artifact import load_deployment_result

    design = read_json(root / spec.output_root / "design-v1.json")
    if design.get("semantic_digest") != semantic_digest(_body(design)):
        raise ValueError(f"{spec.dataset_id} design digest is invalid")
    deployment_path = root / spec.output_root / "deployment-result-v1.json"
    deployment_model = load_deployment_result(deployment_path)
    ordered = list(deployment_model.deployment_ordered_candidate_ids)
    if ordered != design.get("design_stage", {}).get("deployment_ordered_candidate_ids"):
        raise ValueError(f"{spec.dataset_id} deployment order is not sealed design order")
    objects = {item.candidate_id: item for item in deployment_model.deployed_objects}
    if set(objects) != set(ordered):
        raise ValueError(f"{spec.dataset_id} deployment membership is incomplete")
    definitions = []
    for candidate_id in ordered:
        item = objects[candidate_id]
        definitions.append(
            {
                "candidate_id": candidate_id,
                "kind": item.kind,
                "column_ordinals": list(item.column_ordinals),
                "statistics_target": item.statistics_target,
            }
        )
    return {
        "candidate_ids": list(ordered),
        "definitions": definitions,
        "recommendation_digest": design["design_stage"]["recommendation_digest"],
        "deployment_digest": deployment_model.computed_semantic_digest,
        "design_digest": design["semantic_digest"],
    }


def _truth_binding(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    path = root / spec.valid_observations_path
    wire = read_json(path)
    truth_digest = semantic_digest(_body(wire))
    if sha256_file(path) != spec.valid_observations_sha256:
        raise ValueError(f"{spec.dataset_id} valid truth byte SHA drift")
    if wire.get("workload_id") != spec.valid_workload_id:
        raise ValueError(f"{spec.dataset_id} valid truth workload ID drift")
    truths = wire.get("truths")
    if not isinstance(truths, list) or len(truths) != QUERY_COUNT:
        raise ValueError(f"{spec.dataset_id} valid truth count is not 10000")
    if any(not isinstance(item, Mapping) for item in truths):
        raise ValueError(f"{spec.dataset_id} valid truth record is malformed")
    ids = [item.get("query_id") for item in truths]
    if len(set(ids)) != QUERY_COUNT:
        raise ValueError(f"{spec.dataset_id} valid truth IDs are not unique")
    if any(
        not isinstance(item.get("cardinality"), int)
        or isinstance(item.get("cardinality"), bool)
        or item["cardinality"] < 0
        for item in truths
    ):
        raise ValueError(f"{spec.dataset_id} valid truth cardinalities are invalid")
    return {
        "path": spec.valid_observations_path.as_posix(),
        "sha256": spec.valid_observations_sha256,
        "semantic_digest": truth_digest,
        "workload_id": wire["workload_id"],
        "query_count": QUERY_COUNT,
    }


def build_protocol(*, root: Path, producer_sha: str) -> dict[str, Any]:
    """Build the protocol from existing immutable RQ1b evidence only."""

    source_audit = read_json(root / "experiments/rq1-workload-generalization-source-audit-v2.json")
    protocol = read_json(root / "paper/rq1-workload-generalization-protocol-v2.json")
    datasets: list[dict[str, Any]] = []
    for spec in DATASET_SPECS:
        membership = _selected_membership(root, spec)
        audit = next(
            row for row in source_audit["datasets"] if row["dataset_id"] == spec.dataset_id
        )
        datasets.append(
            {
                "dataset_id": spec.dataset_id,
                "cli_name": spec.cli_name,
                "relation": spec.dataset_module.RELATION,
                "rows": spec.dataset_module.EXPECTED_ROWS,
                "schema_contract_id": spec.dataset_module.SCHEMA_CONTRACT_ID,
                "dataset_content_identity": spec.dataset_content_identity,
                "upstream_commit": audit["upstream_commit"],
                "rq1b_design_digest": membership["design_digest"],
                "rq1b_recommendation_digest": membership["recommendation_digest"],
                "rq1b_deployment_digest": membership["deployment_digest"],
                "selected_definitions": membership["definitions"],
                "reference_order": membership["candidate_ids"],
                "permutations": deterministic_orders(membership["candidate_ids"]),
                "valid_workload_id": spec.valid_workload_id,
                "valid_workload_sha256": spec.valid_workload_sha256,
                "valid_canonical_workload_sha256": spec.valid_canonical_workload_sha256,
                "valid_truth_path": spec.valid_observations_path.as_posix(),
                "valid_truth_sha256": spec.valid_observations_sha256,
                "valid_query_count": QUERY_COUNT,
            }
        )
        gc.collect()
    value: dict[str, Any] = {
        "format_version": FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "preregistered",
        "research_question": {
            "SQ1": "controlled hypothetical activation-order sensitivity with fixed native payloads",
            "SQ2": "physical stock deployment-order sensitivity under explicit payload/OID controls",
            "SQ3": "overlapping-MCV masking witness and matched hypothetical/physical estimates",
        },
        "datasets": datasets,
        "source_bindings": {
            "rq1b_protocol_v2": {
                "path": "paper/rq1-workload-generalization-protocol-v2.json",
                "semantic_digest": protocol["semantic_digest"],
            },
            "source_audit_v2": {
                "path": "experiments/rq1-workload-generalization-source-audit-v2.json",
                "semantic_digest": source_audit["semantic_digest"],
            },
            "valid_truth_only": True,
            "test_workload_access": "forbidden-for-all-new-planner-calls",
        },
        "order_schedule": {
            "reference": "published deployment_ordered_candidate_ids",
            "reverse": "exact reverse of reference",
            "random": {
                "algorithm": "Python random.Random(seed).shuffle(reference copy)",
                "seeds": list(SEEDS),
                "collision_policy": COLLISION_POLICY,
            },
            "arm_count_per_dataset": 7,
        },
        "frozen_system": {
            "advisor_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
            "postgres_version": "16.14",
            "system_freeze": formal_system_freeze_v2_identity(),
        },
        "frozen_parameters": {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "query_count": QUERY_COUNT,
            "planner_gucs": {
                "timezone": "UTC",
                "DateStyle": "ISO, YMD",
                "default_statistics_target": "100",
                "transaction_isolation": "repeatable read",
                "transaction_read_only": "on",
            },
        },
        "causal_contract": {
            "hypothetical_primary_factor": "activation order only",
            "physical_comparison_status": "causal only when payload, ordinary stats, data, SQL, GUCs and OID order all match",
            "otherwise": "confounded-operational-deployment",
            "direct_catalog_writes": False,
            "setseed_is_analyze_reproducibility_proof": False,
        },
        "metrics": {
            "qerror_contract": "qerror-cardinality-floor-1-v1",
            "distribution": ["mean", "p50", "p95", "p99", "max"],
            "paired": ["improved", "unchanged", "worsened"],
            "order_diagnostics": [
                "plan_rows_changed",
                "ratio_vs_reference",
                "difference_vs_reference",
            ],
        },
        "synthetic_witness": {
            "fixture_id": "oid-order-overlapping-mcv-witness-v1",
            "schema": "(a integer, b integer, c integer)",
            "mcv_a": "(a,b)",
            "mcv_b": "(b,c)",
            "query": "a = 1 AND b = 1 AND c = 1",
            "arms": ["a-only", "b-only", "a-then-b", "b-then-a", "none"],
            "physical_hypothetical_root_plan_rows_must_match_exactly": True,
        },
        "stop_rules": [
            "missing source identity or payload",
            "query/truth identity mismatch",
            "ordinary-statistics drift",
            "physical payload mismatch is retained and labeled confounded",
            "cleanup failure",
            "any incomplete declared arm",
        ],
        "admissible_claims": [
            "order sensitivity may be measured for the declared fixed memberships and workloads",
            "null and negative results must be reported",
        ],
        "inadmissible_claims": [
            "Advisor ordering is globally optimal",
            "all configurations are order-sensitive",
            "workload drift or distribution-shift robustness",
            "runtime-speedup or RQ1b held-out claims",
        ],
        "preflight_manifest_path": PREFLIGHT_PATH.as_posix(),
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def _validate_frozen_protocol_bindings(value: Mapping[str, Any], root: Path) -> None:
    protocol_path = root / "paper/rq1-workload-generalization-protocol-v2.json"
    source_audit_path = root / "experiments/rq1-workload-generalization-source-audit-v2.json"
    rq1b_protocol = read_json(protocol_path)
    source_audit = read_json(source_audit_path)
    if value.get("source_bindings", {}).get("rq1b_protocol_v2") != {
        "path": "paper/rq1-workload-generalization-protocol-v2.json",
        "semantic_digest": rq1b_protocol.get("semantic_digest"),
    }:
        raise ValueError("OID-order RQ1b protocol source binding drift")
    if value.get("source_bindings", {}).get("source_audit_v2") != {
        "path": "experiments/rq1-workload-generalization-source-audit-v2.json",
        "semantic_digest": source_audit.get("semantic_digest"),
    }:
        raise ValueError("OID-order source-audit binding drift")
    if rq1b_protocol.get("semantic_digest") != semantic_digest(_body(rq1b_protocol)):
        raise ValueError("RQ1b protocol source digest is invalid")
    if source_audit.get("semantic_digest") != semantic_digest(_body(source_audit)):
        raise ValueError("RQ1b source-audit digest is invalid")
    freeze = read_json(root / "paper/system-freeze-v2.json")
    validate_system_freeze_v2(freeze)
    expected_system = formal_system_freeze_v2_identity()
    if value.get("frozen_system", {}).get("system_freeze") != expected_system:
        raise ValueError("OID-order system-freeze binding drift")

    specs = {spec.dataset_id: spec for spec in DATASET_SPECS}
    rows = {row.get("dataset_id"): row for row in source_audit.get("datasets", [])}
    if set(specs) != {item.get("dataset_id") for item in value.get("datasets", [])}:
        raise ValueError("OID-order dataset identity set drift")
    for dataset in value["datasets"]:
        dataset_id = dataset["dataset_id"]
        spec = specs[dataset_id]
        source = rows.get(dataset_id)
        if not isinstance(source, Mapping):
            raise TypeError(f"{dataset_id} source-audit row is missing")
        expected = {
            "dataset_id": spec.dataset_id,
            "cli_name": spec.cli_name,
            "relation": spec.dataset_module.RELATION,
            "rows": spec.dataset_module.EXPECTED_ROWS,
            "schema_contract_id": spec.dataset_module.SCHEMA_CONTRACT_ID,
            "dataset_content_identity": spec.dataset_content_identity,
            "upstream_commit": source.get("upstream_commit"),
            "valid_workload_id": spec.valid_workload_id,
            "valid_workload_sha256": spec.valid_workload_sha256,
            "valid_canonical_workload_sha256": spec.valid_canonical_workload_sha256,
            "valid_truth_path": spec.valid_observations_path.as_posix(),
            "valid_truth_sha256": spec.valid_observations_sha256,
            "valid_query_count": QUERY_COUNT,
        }
        for key, expected_value in expected.items():
            if dataset.get(key) != expected_value:
                raise ValueError(f"{dataset_id} immutable binding drift: {key}")
        membership = _selected_membership(root, spec)
        for key, membership_key in (
            ("rq1b_design_digest", "design_digest"),
            ("rq1b_recommendation_digest", "recommendation_digest"),
            ("rq1b_deployment_digest", "deployment_digest"),
        ):
            if dataset.get(key) != membership[membership_key]:
                raise ValueError(f"{dataset_id} RQ1b binding drift: {key}")
        if dataset.get("reference_order") != membership["candidate_ids"]:
            raise ValueError(f"{dataset_id} selected membership drift")
        if dataset.get("selected_definitions") != membership["definitions"]:
            raise ValueError(f"{dataset_id} selected definitions drift")
        truth = _truth_binding(root, spec)
        if dataset.get("valid_truth_sha256") != truth["sha256"]:
            raise ValueError(f"{dataset_id} valid truth binding drift")


def validate_protocol(value: Mapping[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    if value.get("format_version") != FORMAT:
        raise ValueError("unsupported OID-order protocol format")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("OID-order experiment identity drift")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order protocol semantic digest mismatch")
    if value.get("frozen_system", {}).get("advisor_sha") != FROZEN_ADVISOR_SHA:
        raise ValueError("OID-order Advisor pin drift")
    if value.get("frozen_system", {}).get("patched_postgres_sha") != FROZEN_PATCHED_POSTGRES_SHA:
        raise ValueError("OID-order patched PostgreSQL pin drift")
    if value.get("frozen_system", {}).get("stock_postgres_sha") != FROZEN_STOCK_POSTGRES_SHA:
        raise ValueError("OID-order stock PostgreSQL pin drift")
    if len(value.get("datasets", [])) != 4:
        raise ValueError("OID-order protocol must contain four datasets")
    for dataset in value["datasets"]:
        if len(dataset.get("permutations", [])) != 7:
            raise ValueError(f"{dataset.get('dataset_id')} must contain seven permutations")
        reference = dataset["reference_order"]
        expected = deterministic_orders(reference)
        if dataset["permutations"] != expected:
            raise ValueError(f"{dataset['dataset_id']} permutation schedule drift")
    if root is not None:
        _validate_frozen_protocol_bindings(value, root.resolve())
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def build_preflight(*, root: Path, data_root: Path, producer_sha: str) -> dict[str, Any]:
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol, root=root)
    datasets: list[dict[str, Any]] = []
    for spec in DATASET_SPECS:
        protocol_dataset = next(
            item for item in protocol["datasets"] if item["dataset_id"] == spec.dataset_id
        )
        datasets.append(
            {
                **protocol_dataset,
                "valid_workload": _valid_workload_isolated(root, data_root, spec),
                "valid_truth": _truth_binding(root, spec),
            }
        )
    value: dict[str, Any] = {
        "format_version": PREFLIGHT_FORMAT,
        "experiment_id": EXPERIMENT_ID,
        "status": "ready-to-run",
        "producer_research_sha": producer_sha,
        "protocol": {
            "path": PROTOCOL_PATH.as_posix(),
            "semantic_digest": protocol["semantic_digest"],
        },
        "formal_execution": "not-started",
        "preseal_test_content_accessed": False,
        "test_workload_accessed": False,
        "test_truth_accessed": False,
        "strict_unseen_accessed": False,
        "datasets": datasets,
        "source_data_root_policy": "valid-side-only extraction from audited external root",
        "query_manifest_contract": "query_id + source_query_sha256 + sql_sha256; no SQL payload",
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def validate_preflight(value: Mapping[str, Any], *, root: Path) -> dict[str, Any]:
    if value.get("format_version") != PREFLIGHT_FORMAT:
        raise ValueError("unsupported OID-order preflight format")
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol, root=root)
    if value.get("protocol") != {
        "path": PROTOCOL_PATH.as_posix(),
        "semantic_digest": protocol["semantic_digest"],
    }:
        raise ValueError("OID-order preflight protocol binding drift")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order preflight semantic digest mismatch")
    if value.get("formal_execution") != "not-started":
        raise ValueError("OID-order preflight execution state drift")
    if any(
        value.get(field) is not False
        for field in (
            "preseal_test_content_accessed",
            "test_workload_accessed",
            "test_truth_accessed",
            "strict_unseen_accessed",
        )
    ):
        raise ValueError("OID-order preflight contains test-side access")
    if len(value.get("datasets", [])) != len(DATASET_SPECS):
        raise ValueError("OID-order preflight must contain exactly four datasets")
    expected_ids = {spec.dataset_id for spec in DATASET_SPECS}
    actual_ids = {item.get("dataset_id") for item in value["datasets"]}
    if actual_ids != expected_ids:
        raise ValueError("OID-order preflight dataset identity set drift")
    for dataset in value["datasets"]:
        spec = next(item for item in DATASET_SPECS if item.dataset_id == dataset["dataset_id"])
        protocol_dataset = next(
            item for item in protocol["datasets"] if item["dataset_id"] == spec.dataset_id
        )
        if dataset.get("reference_order") != protocol_dataset["reference_order"]:
            raise ValueError(f"{spec.dataset_id} preflight membership drift")
        if dataset.get("valid_workload", {}).get("workload_id") != spec.valid_workload_id:
            raise ValueError(f"{spec.dataset_id} preflight workload identity drift")
        truth = _truth_binding(root, spec)
        if dataset.get("valid_truth") != truth:
            raise ValueError(f"{spec.dataset_id} preflight truth binding drift")
        workload = dataset.get("valid_workload", {})
        if (
            workload.get("query_count") != QUERY_COUNT
            or len(workload.get("queries", [])) != QUERY_COUNT
        ):
            raise ValueError(f"{dataset.get('dataset_id')} preflight query manifest is incomplete")
        ids = [item.get("query_id") for item in workload["queries"]]
        if len(ids) != len(set(ids)):
            raise ValueError(f"{dataset.get('dataset_id')} preflight query IDs are duplicated")
        truth_wire = read_json(root / spec.valid_observations_path)
        truth_ids = [item["query_id"] for item in truth_wire["truths"]]
        if set(ids) != set(truth_ids):
            raise ValueError(f"{spec.dataset_id} preflight truth/workload IDs differ")
        if any(
            item.get("index") != index
            or not _require_sha(item.get("source_query_sha256"), "source query SHA")
            or not _require_sha(item.get("sql_sha256"), "SQL SHA")
            for index, item in enumerate(workload["queries"])
        ):
            raise ValueError(f"{spec.dataset_id} preflight query manifest is malformed")
        if dataset.get("permutations") != deterministic_orders(dataset.get("reference_order", [])):
            raise ValueError(f"{dataset.get('dataset_id')} preflight permutation drift")
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "dataset_count": len(value["datasets"]),
    }


def write_protocol_and_preflight(
    *, root: Path, data_root: Path, producer_sha: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    protocol = build_protocol(root=root, producer_sha=producer_sha)
    write_json(root / PROTOCOL_PATH, protocol)
    preflight = build_preflight(root=root, data_root=data_root, producer_sha=producer_sha)
    write_json(root / PREFLIGHT_PATH, preflight)
    validate_preflight(preflight, root=root)
    return protocol, preflight


def _run_command(command: Sequence[str], log_dir: Path) -> dict[str, Any]:
    log_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    elapsed = time.monotonic() - started
    stem = f"command-{len(list(log_dir.glob('command-*.stdout'))):04d}"
    (log_dir / f"{stem}.stdout").write_text(result.stdout, encoding="utf-8")
    (log_dir / f"{stem}.stderr").write_text(result.stderr, encoding="utf-8")
    if result.returncode:
        raise CommandFailure(
            command,
            result.returncode,
            result.stdout,
            result.stderr,
            elapsed,
        )
    return {
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "elapsed_seconds": elapsed,
        "argv": _redact_command(command),
    }


def _plan_rows(document: Any, relation: str) -> int:
    document = document[0] if isinstance(document, list) else document
    nodes: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if (
                value.get("Relation Name") == relation.split(".")[-1]
                and value.get("Schema") == relation.split(".")[0]
            ):
                nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if len(nodes) != 1:
        raise ValueError(f"expected one managed base-relation plan node for {relation}")
    return int(nodes[0]["Plan Rows"])


def _explain(connection: Any, sql: str, relation: str) -> tuple[int, str]:
    document = connection.execute(f"EXPLAIN (VERBOSE, FORMAT JSON) {sql}").fetchone()[0]
    if isinstance(document, str):
        document = json.loads(document)
    return _plan_rows(document, relation), semantic_digest(document)


def _query_records(
    snapshot: Any, truth_by_id: Mapping[str, int], relation: str
) -> list[dict[str, Any]]:
    queries = []
    for query in snapshot.workload.queries:
        if query.query_id not in truth_by_id:
            raise ValueError(f"valid truth is missing {query.query_id}")
        queries.append(
            {
                "query_id": query.query_id,
                "sql": query.sql,
                "truth": int(truth_by_id[query.query_id]),
                "weight": float(query.weight),
            }
        )
    if len(queries) != QUERY_COUNT:
        raise ValueError("snapshot valid workload does not contain exactly 10000 queries")
    return queries


def _aggregate(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = sorted(float(record["qerror"]) for record in records)
    if not values:
        raise ValueError("cannot aggregate empty query records")

    def percentile(fraction: float) -> float:
        position = (len(values) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        return values[lower] + (values[upper] - values[lower]) * (position - lower)

    return {
        "query_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": max(values),
    }


def _paired_against(
    records: Sequence[Mapping[str, Any]], reference: Mapping[str, Mapping[str, Any]]
) -> dict[str, int]:
    counts = {"improved": 0, "unchanged": 0, "worsened": 0}
    for record in records:
        ref = float(reference[record["query_id"]]["qerror"])
        value = float(record["qerror"])
        key = "improved" if value < ref else "worsened" if value > ref else "unchanged"
        counts[key] += 1
    return counts


def _write_gzip_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as compressed:
        for record in records:
            line = json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            compressed.write(line.encode("utf-8"))
    return sha256_file(path)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return [json.loads(line) for line in stream]


def _artifact_path(root: Path, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path is missing")
    path = Path(value)
    if path.is_absolute():
        raise ValueError(f"{label} path must be repository-relative")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved.is_symlink():
        raise ValueError(f"{label} path escapes repository")
    if not resolved.is_file():
        raise FileNotFoundError(f"missing {label}: {resolved}")
    return resolved


def _close_enough(actual: Any, expected: Any) -> bool:
    return (
        isinstance(actual, (int, float))
        and isinstance(expected, (int, float))
        and math.isclose(float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-12)
    )


def _read_valid_context(
    root: Path, dataset_id: str
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    spec = next((item for item in DATASET_SPECS if item.dataset_id == dataset_id), None)
    if spec is None:
        raise ValueError(f"unknown OID-order dataset: {dataset_id}")
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol, root=root)
    preflight = read_json(root / PREFLIGHT_PATH)
    validate_preflight(preflight, root=root)
    dataset = next(item for item in preflight["datasets"] if item["dataset_id"] == dataset_id)
    truth = read_json(root / spec.valid_observations_path)
    if sha256_file(root / spec.valid_observations_path) != spec.valid_observations_sha256:
        raise ValueError(f"{dataset_id} valid truth byte SHA drift")
    if truth.get("workload_id") != spec.valid_workload_id:
        raise ValueError(f"{dataset_id} valid truth workload identity drift")
    truth_by_id = {item["query_id"]: str(item["cardinality"]) for item in truth["truths"]}
    queries = dataset["valid_workload"]["queries"]
    manifest = {item["query_id"]: item["sql_sha256"] for item in queries}
    source = {f"source:{item['query_id']}": item["source_query_sha256"] for item in queries}
    if set(truth_by_id) != set(manifest):
        raise ValueError(f"{dataset_id} valid query/truth membership drift")
    return (
        protocol,
        preflight,
        manifest | source | {f"truth:{key}": value for key, value in truth_by_id.items()},
    )


def _validate_query_records(
    *,
    root: Path,
    arm: Mapping[str, Any],
    expected_dataset_id: str,
    expected_permutation_id: str,
    expected_execution_arm: str,
    expected_sql: Mapping[str, str],
    expected_truth: Mapping[str, str],
) -> tuple[list[dict[str, Any]], str]:
    path = _artifact_path(root, arm.get("per_query_path"), "per-query")
    actual_sha = sha256_file(path)
    if actual_sha != arm.get("per_query_sha256"):
        raise ValueError(f"{expected_dataset_id}/{expected_permutation_id} per-query SHA drift")
    try:
        records = _read_jsonl(path)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"{expected_dataset_id}/{expected_permutation_id} invalid gzip JSONL"
        ) from exc
    if len(records) != QUERY_COUNT:
        raise ValueError(f"{expected_dataset_id}/{expected_permutation_id} query count drift")
    ids = [record.get("query_id") for record in records]
    if len(set(ids)) != QUERY_COUNT or set(ids) != set(expected_sql):
        raise ValueError(f"{expected_dataset_id}/{expected_permutation_id} query IDs drift")
    for record in records:
        required = {
            "dataset_id",
            "permutation_id",
            "execution_arm",
            "query_id",
            "sql_sha256",
            "source_query_sha256",
            "truth",
            "plan_rows",
            "qerror",
            "explain_sha256",
        }
        if set(record) != required:
            raise ValueError(f"{expected_dataset_id}/{expected_permutation_id} record schema drift")
        query_id = record["query_id"]
        if record["dataset_id"] != expected_dataset_id:
            raise ValueError("per-query dataset identity drift")
        if record["permutation_id"] != expected_permutation_id:
            raise ValueError("per-query permutation identity drift")
        if record["execution_arm"] != expected_execution_arm:
            raise ValueError("per-query execution-arm identity drift")
        if record["sql_sha256"] != expected_sql[query_id]:
            raise ValueError("per-query SQL identity drift")
        if record["source_query_sha256"] != expected_truth[f"source:{query_id}"]:
            raise ValueError("per-query source-query identity drift")
        truth = record["truth"]
        plan_rows = record["plan_rows"]
        if (
            not isinstance(truth, int)
            or isinstance(truth, bool)
            or truth < 0
            or not isinstance(plan_rows, int)
            or isinstance(plan_rows, bool)
            or plan_rows < 0
            or not isinstance(record["qerror"], (int, float))
            or not math.isfinite(float(record["qerror"]))
            or float(record["qerror"]) < 1.0
            or not isinstance(record["explain_sha256"], str)
            or len(record["explain_sha256"]) != 64
        ):
            raise ValueError("per-query numerical or EXPLAIN digest evidence is invalid")
        if int(expected_truth[f"truth:{query_id}"]) != truth:
            raise ValueError("per-query truth identity drift")
        recomputed = qerror(plan_rows, truth)
        if not _close_enough(record["qerror"], recomputed):
            raise ValueError("per-query q-error is not reproducible")
    return records, actual_sha


def _validate_metrics(arm: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> None:
    expected = _aggregate(records)
    actual = arm.get("metrics")
    if not isinstance(actual, Mapping):
        raise TypeError("arm metrics are missing")
    for key, value in expected.items():
        if key == "query_count":
            if actual.get(key) != value:
                raise ValueError("arm query-count metric drift")
        elif not _close_enough(actual.get(key), value):
            raise ValueError(f"arm metric drift: {key}")


def _validate_synthetic_witness(value: Mapping[str, Any]) -> None:
    if value.get("format_version") != "oid-order-overlapping-mcv-witness-v1":
        raise ValueError("synthetic witness format drift")
    if value.get("fixture_id") != "oid-order-overlapping-mcv-witness-v1":
        raise ValueError("synthetic witness identity drift")
    precondition = value.get("static_mechanism_precondition")
    if not isinstance(precondition, Mapping):
        raise TypeError("synthetic MCV tie precondition is missing")
    if precondition.get("tie_precondition_source_level") is not True:
        raise ValueError("synthetic MCV tie precondition was not established")
    if precondition.get("runtime_tie_observation") != "not-directly-instrumented":
        raise ValueError("synthetic runtime mechanism claim is overstated")
    expected_arms = {"a-only", "b-only", "a-then-b", "b-then-a", "none"}
    arms = value.get("arms")
    if not isinstance(arms, Mapping) or set(arms) != expected_arms:
        raise ValueError("synthetic witness arm inventory drift")
    for arm in arms.values():
        if not isinstance(arm, Mapping) or not arm.get("exact_root_plan_rows_match"):
            raise ValueError("synthetic physical/hypothetical fidelity failed")
    if value.get("physical_hypothetical_exact_match_count") != len(expected_arms):
        raise ValueError("synthetic witness completeness drift")
    if value.get("physical_hypothetical_arm_count") != len(expected_arms):
        raise ValueError("synthetic witness arm count drift")
    if value.get("post_cleanup") is not True:
        raise ValueError("synthetic witness cleanup was not verified")


def _validate_dataset_result(
    *,
    root: Path,
    summary: Mapping[str, Any],
    dataset_result: Mapping[str, Any],
    protocol_dataset: Mapping[str, Any],
    preflight_dataset: Mapping[str, Any],
    expected_sql: Mapping[str, str],
    expected_truth: Mapping[str, str],
) -> None:
    dataset_id = protocol_dataset["dataset_id"]
    spec = next(item for item in DATASET_SPECS if item.dataset_id == dataset_id)
    if dataset_result.get("format_version") != FORMAT:
        raise ValueError(f"{dataset_id} dataset result format drift")
    if dataset_result.get("dataset_id") != dataset_id:
        raise ValueError(f"{dataset_id} dataset result identity drift")
    if dataset_result.get("producer_research_sha") != summary.get("producer_research_sha"):
        raise ValueError(f"{dataset_id} dataset producer binding drift")
    if dataset_result.get("valid_workload") != {
        "workload_id": protocol_dataset["valid_workload_id"],
        "sha256": protocol_dataset["valid_workload_sha256"],
        "canonical_source_sha256": protocol_dataset["valid_canonical_workload_sha256"],
        "query_count": QUERY_COUNT,
    }:
        raise ValueError(f"{dataset_id} valid workload binding drift")
    expected_membership = protocol_dataset["reference_order"]
    candidate_membership = dataset_result.get("candidate_membership")
    expected_membership_digest = semantic_digest({"candidate_ids": expected_membership})
    if not isinstance(candidate_membership, Mapping):
        raise TypeError(f"{dataset_id} candidate membership is missing")
    if candidate_membership.get("candidate_ids") != expected_membership:
        raise ValueError(f"{dataset_id} candidate membership drift")
    if candidate_membership.get("digest") != expected_membership_digest:
        raise ValueError(f"{dataset_id} candidate membership digest drift")
    if candidate_membership.get("selected_k") != len(expected_membership):
        raise ValueError(f"{dataset_id} selected-k drift")
    if (
        dataset_result.get("rq1b_reference", {}).get("design_digest")
        != protocol_dataset["rq1b_design_digest"]
    ):
        raise ValueError(f"{dataset_id} design provenance drift")
    if (
        dataset_result.get("rq1b_reference", {}).get("recommendation_digest")
        != protocol_dataset["rq1b_recommendation_digest"]
    ):
        raise ValueError(f"{dataset_id} Recommendation provenance drift")
    if (
        dataset_result.get("rq1b_reference", {}).get("deployment_digest")
        != protocol_dataset["rq1b_deployment_digest"]
    ):
        raise ValueError(f"{dataset_id} deployment provenance drift")
    if dataset_result.get("valid_truth", {}).get("query_count") != QUERY_COUNT:
        raise ValueError(f"{dataset_id} valid truth count drift")
    if (
        dataset_result.get("valid_truth", {}).get("sha256")
        != protocol_dataset["valid_truth_sha256"]
    ):
        raise ValueError(f"{dataset_id} valid truth provenance drift")
    if (
        dataset_result.get("test_workload_accessed") is not False
        or dataset_result.get("test_truth_accessed") is not False
    ):
        raise ValueError(f"{dataset_id} test-side boundary was violated")
    if dataset_result.get("evidence_eligible") is not False:
        raise ValueError(
            f"{dataset_id} raw dataset result cannot self-declare evidence eligibility"
        )

    definitions = {item["candidate_id"]: item for item in protocol_dataset["selected_definitions"]}
    if candidate_membership.get("definitions") != protocol_dataset["selected_definitions"]:
        raise ValueError(f"{dataset_id} selected definition binding drift")
    arms = []
    for group_name, expected_execution_arm in (
        ("hypothetical", "controlled-hypothetical"),
        ("physical", "stock-physical"),
    ):
        group = dataset_result.get(group_name)
        if not isinstance(group, Mapping):
            raise TypeError(f"{dataset_id} {group_name} arms are missing")
        if set(group) != {item["permutation_id"] for item in protocol_dataset["permutations"]}:
            raise ValueError(f"{dataset_id} {group_name} arm inventory drift")
        arms.extend(
            (group_name, arm_id, arm, expected_execution_arm) for arm_id, arm in group.items()
        )

    records_by_arm: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for group_name, arm_id, arm, expected_execution_arm in arms:
        permutation = next(
            item for item in protocol_dataset["permutations"] if item["permutation_id"] == arm_id
        )
        if not isinstance(arm, Mapping) or arm.get("status") != "complete":
            raise ValueError(f"{dataset_id}/{group_name}/{arm_id} is incomplete")
        if arm.get("prescribed_order") != permutation["order"]:
            raise ValueError(f"{dataset_id}/{group_name}/{arm_id} order drift")
        if arm.get("candidate_membership_digest") != expected_membership_digest:
            raise ValueError(f"{dataset_id}/{group_name}/{arm_id} membership digest drift")
        records, _ = _validate_query_records(
            root=root,
            arm=arm,
            expected_dataset_id=dataset_id,
            expected_permutation_id=arm_id,
            expected_execution_arm=expected_execution_arm,
            expected_sql=expected_sql,
            expected_truth=expected_truth,
        )
        _validate_metrics(arm, records)
        records_by_arm[(group_name, arm_id)] = records

    reference_hyp = dataset_result["hypothetical"]["reference"]
    reference_phys = dataset_result["physical"]["reference"]
    for arm_id in dataset_result["hypothetical"]:
        arm = dataset_result["hypothetical"][arm_id]
        if arm.get("causal_status") != "controlled-order-only":
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical control status drift")
        if len(arm.get("active_virtual_oids", [])) != len(expected_membership):
            raise ValueError(f"{dataset_id}/{arm_id} virtual OID evidence incomplete")
        if len(set(arm["active_virtual_oids"])) != len(expected_membership):
            raise ValueError(f"{dataset_id}/{arm_id} virtual OID evidence is not unique")
        if arm.get("payload_sha256_by_candidate") != reference_hyp.get(
            "payload_sha256_by_candidate"
        ):
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical payload realization drift")
        if arm.get("ordinary_statistics_fingerprint") != reference_hyp.get(
            "ordinary_statistics_fingerprint"
        ):
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical ordinary statistics drift")
        if arm.get("planner_settings") != reference_hyp.get("planner_settings"):
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical planner settings drift")
        if arm.get("dataset_content_identity") != reference_hyp.get("dataset_content_identity"):
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical dataset identity drift")
        diagnostics = _order_diagnostics(arm, reference_hyp, root=root)
        if arm.get("order_diagnostics") != diagnostics:
            raise ValueError(f"{dataset_id}/{arm_id} hypothetical diagnostics drift")

    required_controls = {
        "membership_and_definitions_equal",
        "payload_bytes_equal_to_reference",
        "ordinary_statistics_equal_to_reference",
        "planner_settings_equal_to_reference",
        "prescribed_oid_order_observed",
        "relation_identity_equal",
        "schema_identity_equal",
        "statistics_target_equal",
    }
    for arm_id, arm in dataset_result["physical"].items():
        controls = arm.get("causal_controls")
        if not isinstance(controls, Mapping) or set(controls) != required_controls:
            raise ValueError(f"{dataset_id}/{arm_id} physical controls are incomplete")
        if any(not isinstance(controls[key], bool) for key in required_controls):
            raise ValueError(f"{dataset_id}/{arm_id} physical controls are not observations")
        if arm.get("actual_oid_order") != arm.get("prescribed_order"):
            raise ValueError(f"{dataset_id}/{arm_id} physical OID order drift")
        if arm_id == "reference" and arm.get("causal_status") != "reference-control":
            raise ValueError(f"{dataset_id}/{arm_id} reference control status drift")
        objects = arm.get("physical_oids")
        if not isinstance(objects, list) or len(objects) != len(expected_membership):
            raise ValueError(f"{dataset_id}/{arm_id} physical catalog evidence incomplete")
        object_ids = [item.get("candidate_id") for item in objects]
        if set(object_ids) != set(expected_membership) or len(set(object_ids)) != len(objects):
            raise ValueError(f"{dataset_id}/{arm_id} physical membership drift")
        if arm_id != "reference":
            all_controls = all(controls.values())
            expected_status = (
                "order-only-identified" if all_controls else "confounded-operational-deployment"
            )
            if arm.get("causal_status") != expected_status:
                raise ValueError(f"{dataset_id}/{arm_id} physical causal status drift")
        diagnostics = _order_diagnostics(arm, reference_phys, root=root)
        if arm.get("order_diagnostics") != diagnostics:
            raise ValueError(f"{dataset_id}/{arm_id} physical diagnostics drift")
        for item in objects:
            candidate_id = item["candidate_id"]
            if candidate_id not in definitions:
                raise ValueError(f"{dataset_id}/{arm_id} unknown physical candidate")
            expected_definition = definitions[candidate_id]
            expected_kind = (
                "mcv" if expected_definition["kind"] == "postgresql.mcv" else "dependencies"
            )
            expected_ordinals = list(expected_definition["column_ordinals"])
            expected_names = [
                spec.dataset_module.COLUMNS[int(ordinal) - 1][0] for ordinal in expected_ordinals
            ]
            if item.get("kind") != expected_kind:
                raise ValueError(f"{dataset_id}/{arm_id} physical kind drift")
            if item.get("column_ordinals") != expected_ordinals:
                raise ValueError(f"{dataset_id}/{arm_id} physical column ordinal drift")
            if item.get("column_names") != expected_names:
                raise ValueError(f"{dataset_id}/{arm_id} physical column name drift")
            if item.get("keys") != " ".join(str(value) for value in expected_ordinals):
                raise ValueError(f"{dataset_id}/{arm_id} physical catalog key drift")
            expected_candidate = dict(expected_definition)
            expected_candidate["column_names"] = expected_names
            if item.get("definition_fingerprint") != _definition_fingerprint(
                candidate_id, expected_candidate
            ):
                raise ValueError(f"{dataset_id}/{arm_id} physical definition fingerprint drift")
            if not item.get("payload_present") or not item.get("payload_sha256"):
                raise ValueError(f"{dataset_id}/{arm_id} missing native payload evidence")
            if item.get("statistics_target") != STATISTICS_TARGET:
                raise ValueError(f"{dataset_id}/{arm_id} statistics target drift")
        observed_definition_equal = set(object_ids) == set(expected_membership) and all(
            item.get("definition_fingerprint")
            == _definition_fingerprint(
                item["candidate_id"],
                {
                    **definitions[item["candidate_id"]],
                    "column_names": item["column_names"],
                },
            )
            for item in objects
        )
        observed_payload_equal = arm.get("payload_sha256_by_candidate") == reference_phys.get(
            "payload_sha256_by_candidate"
        )
        observed_ordinary_equal = arm.get("ordinary_statistics_fingerprint") == reference_phys.get(
            "ordinary_statistics_fingerprint"
        )
        observed_settings_equal = arm.get("planner_settings") == reference_phys.get(
            "planner_settings"
        )
        observed_relation_equal = arm.get("relation_identity") == reference_phys.get(
            "relation_identity"
        )
        observed_dataset_equal = arm.get("dataset_content_identity") == reference_phys.get(
            "dataset_content_identity"
        )
        observed_target_equal = all(
            item.get("statistics_target") == STATISTICS_TARGET for item in objects
        )
        observed_controls = {
            "membership_and_definitions_equal": observed_definition_equal,
            "payload_bytes_equal_to_reference": observed_payload_equal,
            "ordinary_statistics_equal_to_reference": observed_ordinary_equal,
            "planner_settings_equal_to_reference": observed_settings_equal,
            "prescribed_oid_order_observed": arm.get("actual_oid_order")
            == arm.get("prescribed_order"),
            "relation_identity_equal": observed_relation_equal and observed_dataset_equal,
            "schema_identity_equal": observed_relation_equal,
            "statistics_target_equal": observed_target_equal,
        }
        if dict(controls) != observed_controls:
            raise ValueError(f"{dataset_id}/{arm_id} physical controls are not observations")

    cleanup = dataset_result.get("cleanup")
    if not isinstance(cleanup, Mapping) or cleanup.get("sandbox_destroyed") is not True:
        raise ValueError(f"{dataset_id} cleanup evidence is incomplete")


def _infer_repository_root(path: Path) -> Path:
    resolved = Path(path).resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / PROTOCOL_PATH).is_file() and (candidate / PREFLIGHT_PATH).is_file():
            return candidate
    raise ValueError("cannot infer research repository root from OID result path")


def _connection_settings(connection: Any) -> dict[str, str]:
    names = (
        "DateStyle",
        "default_statistics_target",
        "enable_bitmapscan",
        "enable_indexscan",
        "enable_seqscan",
        "jit",
        "plan_cache_mode",
        "search_path",
        "timezone",
        "transaction_isolation",
        "transaction_read_only",
    )
    return {name: str(connection.execute(f"SHOW {name}").fetchone()[0]) for name in names}


def _candidate_name(candidate_id: str) -> str:
    return f"oid_order_{candidate_id.removeprefix('cand_')}"


def _definition_fingerprint(candidate_id: str, candidate: Mapping[str, Any]) -> str:
    return semantic_digest(
        {
            "candidate_id": candidate_id,
            "kind": candidate["kind"],
            "column_ordinals": list(candidate["column_ordinals"]),
            "column_names": list(candidate["column_names"]),
            "statistics_target": STATISTICS_TARGET,
        }
    )


def _load_sealed_snapshot(path: Path) -> Any:
    """Reload and verify a serialized snapshot before importing authoritative truth."""

    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(path)
    if not getattr(snapshot, "semantic_digest", None):
        raise ValueError("serialized snapshot has no semantic digest")
    return snapshot


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _physical_arm(
    *,
    dsn: str,
    parent_database: str,
    relation: str,
    candidates: Mapping[str, Mapping[str, Any]],
    order: Sequence[str],
    queries: Sequence[Mapping[str, Any]],
    truth_by_id: Mapping[str, int],
    output: Path,
    research_root: Path,
    dataset_id: str,
    dataset_content_identity: str,
) -> dict[str, Any]:
    """Build one disposable stock clone, evaluate valid queries, and remove it."""

    import psycopg
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    from extstats_advisor_research.rq3_fidelity import _physical_payload

    fields = conninfo_to_dict(dsn)
    clone = f"oid_order_{dataset_id.removeprefix('arecel-')}_{time.time_ns() % 10**10:010d}"
    admin = dict(fields)
    admin["dbname"] = "postgres"
    clone_fields = dict(fields)
    clone_fields["dbname"] = clone
    admin_dsn = make_conninfo(**admin)
    clone_dsn = make_conninfo(**clone_fields)
    schema, relation_name = relation.split(".", 1)
    names: list[str] = []
    try:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(
                f"CREATE DATABASE {_quote_identifier(clone)} TEMPLATE {_quote_identifier(parent_database)}"
            )
        with psycopg.connect(clone_dsn, autocommit=True) as conn:
            for candidate_id in order:
                candidate = candidates[candidate_id]
                name = _candidate_name(candidate_id)
                names.append(name)
                kind = "mcv" if candidate["kind"] == "postgresql.mcv" else "dependencies"
                columns = ", ".join(
                    _quote_identifier(str(value)) for value in candidate["column_names"]
                )
                conn.execute(
                    f"CREATE STATISTICS {_quote_identifier(schema)}.{_quote_identifier(name)} ({kind}) ON {columns} FROM {_quote_identifier(schema)}.{_quote_identifier(relation_name)}"
                )
                conn.execute(
                    f"ALTER STATISTICS {_quote_identifier(schema)}.{_quote_identifier(name)} SET STATISTICS {STATISTICS_TARGET}"
                )
            conn.execute(f"ANALYZE {_quote_identifier(schema)}.{_quote_identifier(relation_name)}")
            settings = _connection_settings(conn)
            rows = conn.execute(
                "SELECT e.oid::bigint, e.stxname, e.stxkind::text, e.stxkeys::text, e.stxstattarget FROM pg_catalog.pg_statistic_ext e JOIN pg_catalog.pg_namespace n ON n.oid=e.stxnamespace JOIN pg_catalog.pg_class c ON c.oid=e.stxrelid WHERE n.nspname=%s AND c.relname=%s ORDER BY e.oid",
                (schema, relation_name),
            ).fetchall()
            by_name = {str(row[1]): row for row in rows}
            physical_objects = []
            for candidate_id, name in zip(order, names, strict=True):
                row = by_name[name]
                kind = (
                    "mcv"
                    if candidates[candidate_id]["kind"] == "postgresql.mcv"
                    else "dependencies"
                )
                payload = _physical_payload(conn, int(row[0]), name, kind)
                physical_objects.append(
                    {
                        "candidate_id": candidate_id,
                        "oid": int(row[0]),
                        "name": name,
                        "catalog_kind": str(row[2]),
                        "keys": str(row[3]),
                        "kind": kind,
                        "column_ordinals": list(candidates[candidate_id]["column_ordinals"]),
                        "column_names": list(candidates[candidate_id]["column_names"]),
                        "statistics_target": int(row[4]),
                        "definition_fingerprint": _definition_fingerprint(
                            candidate_id, candidates[candidate_id]
                        ),
                        "payload_sha256": payload["payload_sha256"],
                        "payload_size": payload["payload_size"],
                        "payload_present": payload["payload_size"] > 0,
                    }
                )
            records = []
            for query in queries:
                plan_rows, explain_digest = _explain(conn, str(query["sql"]), relation)
                truth = int(truth_by_id[query["query_id"]])
                records.append(
                    {
                        "dataset_id": dataset_id,
                        "permutation_id": output.parent.name,
                        "execution_arm": "stock-physical",
                        "query_id": query["query_id"],
                        "sql_sha256": _sha256_text(str(query["sql"])),
                        "source_query_sha256": query["source_query_sha256"],
                        "truth": truth,
                        "plan_rows": plan_rows,
                        "qerror": qerror(plan_rows, truth),
                        "explain_sha256": explain_digest,
                    }
                )
            rel_oid = int(conn.execute("SELECT to_regclass(%s)::oid", (relation,)).fetchone()[0])
            fingerprint = ordinary_stats_fingerprint(conn, rel_oid)
        digest = _write_gzip_jsonl(output, records)
        if not all(item["payload_present"] for item in physical_objects):
            raise ValueError(f"{dataset_id} physical payload inventory contains an empty payload")
        return {
            "status": "complete",
            "execution_arm": "stock-physical",
            "per_query_path": output.relative_to(research_root).as_posix(),
            "per_query_sha256": digest,
            "metrics": _aggregate(records),
            "physical_oids": physical_objects,
            "prescribed_order": list(order),
            "actual_oid_order": [
                item["candidate_id"]
                for item in sorted(physical_objects, key=lambda item: item["oid"])
            ],
            "payload_sha256_by_candidate": {
                item["candidate_id"]: item["payload_sha256"] for item in physical_objects
            },
            "payload_size_by_candidate": {
                item["candidate_id"]: item["payload_size"] for item in physical_objects
            },
            "ordinary_statistics_fingerprint": fingerprint,
            "planner_settings": settings,
            "database_identity": str(conninfo_to_dict(clone_dsn).get("dbname")),
            "relation_identity": {"schema": schema, "relation": relation_name},
            "dataset_content_identity": dataset_content_identity,
            "analyze_count": 1,
            "causal_status": "pending-payload-control",
        }
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {_quote_identifier(clone)} WITH (FORCE)")


def _hypothetical_arms(
    *,
    planner_dsn: str,
    advisor_root: Path,
    snapshot_path: Path,
    candidate_path: Path,
    native_path: Path,
    orders: Sequence[Mapping[str, Any]],
    queries: Sequence[Mapping[str, Any]],
    truth_by_id: Mapping[str, int],
    relation: str,
    dataset_id: str,
    dataset_content_identity: str,
    output_dir: Path,
    research_root: Path,
) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(advisor_root / "src"))
    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres.planner import (
        PostgresPlannerSession,
        PostgresStatisticsConfiguration,
    )
    from extstats_advisor.native_stats import load_native_stats_repository
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(snapshot_path)
    universe = load_candidate_universe(candidate_path, snapshot)
    repository = load_native_stats_repository(native_path)
    payload_inventory = {
        candidate.candidate_id: {
            "sha256": _sha256_bytes(repository.payloads[candidate.candidate_id]),
            "size": len(repository.payloads[candidate.candidate_id]),
            "present": candidate.candidate_id in repository.payloads
            and len(repository.payloads[candidate.candidate_id]) > 0,
        }
        for candidate in repository.candidate_models
    }
    if not all(item["present"] for item in payload_inventory.values()):
        raise ValueError(f"{dataset_id} native repository contains an empty payload")
    results: dict[str, dict[str, Any]] = {}
    with PostgresPlannerSession(planner_dsn, snapshot, universe, repository) as planner:
        for arm in orders:
            order = tuple(arm["order"])
            planner.activate(PostgresStatisticsConfiguration(order))
            active_oids = list(planner.active_backend_oids())
            registered = planner.registered_oids
            expected_oids = [registered[candidate_id] for candidate_id in order]
            if active_oids != expected_oids:
                raise ValueError(f"{dataset_id} hypothetical activation order was not preserved")
            records = []
            for query in queries:
                plan_rows, explain_digest = _explain(
                    planner.connection, str(query["sql"]), relation
                )
                truth = int(truth_by_id[query["query_id"]])
                records.append(
                    {
                        "dataset_id": dataset_id,
                        "permutation_id": arm["permutation_id"],
                        "execution_arm": "controlled-hypothetical",
                        "query_id": query["query_id"],
                        "sql_sha256": _sha256_text(str(query["sql"])),
                        "source_query_sha256": query["source_query_sha256"],
                        "truth": truth,
                        "plan_rows": plan_rows,
                        "qerror": qerror(plan_rows, truth),
                        "explain_sha256": explain_digest,
                    }
                )
            path = (
                output_dir
                / "controlled-hypothetical"
                / arm["permutation_id"]
                / "per-query.jsonl.gz"
            )
            digest = _write_gzip_jsonl(path, records)
            results[arm["permutation_id"]] = {
                "status": "complete",
                "execution_arm": "controlled-hypothetical",
                "per_query_path": path.relative_to(research_root).as_posix(),
                "per_query_sha256": digest,
                "metrics": _aggregate(records),
                "prescribed_order": list(order),
                "active_virtual_oids": active_oids,
                "payload_sha256_by_candidate": {
                    candidate_id: payload_inventory[candidate_id]["sha256"]
                    for candidate_id in order
                },
                "payload_size_by_candidate": {
                    candidate_id: payload_inventory[candidate_id]["size"] for candidate_id in order
                },
                "ordinary_statistics_fingerprint": repository.ordinary_stats_fingerprint,
                "dataset_content_identity": dataset_content_identity,
                "candidate_membership_digest": semantic_digest({"candidate_ids": list(order)}),
                "planner_settings": _connection_settings(planner.connection),
                "causal_status": "controlled-order-only",
            }
    return results


def validate_result_artifact(path: Path, *, root: Path | None = None) -> dict[str, Any]:
    """Independently validate the complete OID-order result bundle offline."""

    path = Path(path)
    root = (root or _infer_repository_root(path)).resolve()
    value = read_json(path)
    if value.get("format_version") != SUMMARY_FORMAT:
        raise ValueError("unsupported OID-order summary format")
    if value.get("semantic_digest") != semantic_digest(_body(value)):
        raise ValueError("OID-order summary semantic digest mismatch")
    if value.get("status") != "complete":
        raise ValueError("OID-order summary is not complete")
    if value.get("experiment_id") != EXPERIMENT_ID:
        raise ValueError("OID-order summary experiment identity drift")
    protocol = read_json(root / PROTOCOL_PATH)
    validate_protocol(protocol, root=root)
    preflight = read_json(root / PREFLIGHT_PATH)
    validate_preflight(preflight, root=root)
    if value.get("producer_research_sha") != preflight.get("producer_research_sha"):
        raise ValueError("OID-order summary producer binding drift")
    if value.get("formal_invocation_count") != 1:
        raise ValueError("OID-order summary invocation count drift")
    frozen = value.get("frozen_system")
    if frozen != {
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": "16.14",
    }:
        raise ValueError("OID-order summary frozen-system binding drift")
    system_identity = value.get("system_identity")
    if not isinstance(system_identity, Mapping):
        raise TypeError("OID-order system identity evidence is missing")
    dataset_results = value.get("dataset_results")
    if not isinstance(dataset_results, list) or len(dataset_results) != len(DATASET_SPECS):
        raise ValueError("OID-order dataset result inventory is incomplete")
    if len({item.get("dataset_id") for item in dataset_results}) != len(DATASET_SPECS):
        raise ValueError("OID-order dataset result IDs are not unique")
    preflight_by_id = {item["dataset_id"]: item for item in preflight["datasets"]}
    protocol_by_id = {item["dataset_id"]: item for item in protocol["datasets"]}
    for dataset_result in dataset_results:
        dataset_id = dataset_result.get("dataset_id")
        if dataset_id not in protocol_by_id:
            raise ValueError(f"unexpected OID-order dataset result: {dataset_id}")
        _, _, bindings = _read_valid_context(root, dataset_id)
        expected_sql = {
            key: value
            for key, value in bindings.items()
            if not key.startswith("truth:") and not key.startswith("source:")
        }
        expected_truth = {
            key: value for key, value in bindings.items() if key.startswith(("truth:", "source:"))
        }
        _validate_dataset_result(
            root=root,
            summary=value,
            dataset_result=dataset_result,
            protocol_dataset=protocol_by_id[dataset_id],
            preflight_dataset=preflight_by_id[dataset_id],
            expected_sql=expected_sql,
            expected_truth=expected_truth,
        )
    synthetic = value.get("synthetic_witness")
    if not isinstance(synthetic, Mapping):
        raise TypeError("OID-order synthetic witness is missing")
    _validate_synthetic_witness(synthetic)
    return {
        "status": "valid",
        "semantic_digest": value["semantic_digest"],
        "dataset_count": len(dataset_results),
        "evidence_eligible": True,
        "causal_controls_validated": True,
    }


def _order_diagnostics(
    result: dict[str, Any], reference: Mapping[str, Any], *, root: Path
) -> dict[str, Any]:
    records = _read_jsonl(_artifact_path(root, result["per_query_path"], "per-query"))
    reference_records = {
        item["query_id"]: item
        for item in _read_jsonl(_artifact_path(root, reference["per_query_path"], "per-query"))
    }
    if set(reference_records) != {item["query_id"] for item in records}:
        raise ValueError("order arms do not contain the same query IDs")
    differences = [
        int(item["plan_rows"] != reference_records[item["query_id"]]["plan_rows"])
        for item in records
    ]
    return {
        "plan_rows_changed": sum(differences),
        "plan_rows_changed_fraction": sum(differences) / len(differences),
        "ratio_vs_reference": result["metrics"]["mean"] / reference["metrics"]["mean"],
        "difference_vs_reference": result["metrics"]["mean"] - reference["metrics"]["mean"],
        "paired_qerror": _paired_against(records, reference_records),
    }


def _assemble_dataset_result(
    *,
    root: Path,
    spec: RQ1BDatasetSpec,
    dataset: Mapping[str, Any],
    producer_sha: str,
    output_dir: Path,
    snapshot_digest: str,
    native_repository_digest: str,
    truth_by_id: Mapping[str, int],
    hypothetical: dict[str, dict[str, Any]],
    physical: dict[str, dict[str, Any]],
    sandbox: Mapping[str, Any],
) -> dict[str, Any]:
    reference_id = "reference"
    candidate_digest = semantic_digest({"candidate_ids": dataset["reference_order"]})
    reference_hyp = hypothetical[reference_id]
    reference_physical = physical[reference_id]
    native_payloads = reference_hyp["payload_sha256_by_candidate"]
    reference_settings = reference_physical["planner_settings"]
    for arm_id, arm in hypothetical.items():
        arm["candidate_membership_digest"] = candidate_digest
        arm["order_diagnostics"] = _order_diagnostics(arm, reference_hyp, root=root)
    for arm_id, arm in physical.items():
        payload_equal = (
            arm["payload_sha256_by_candidate"] == reference_physical["payload_sha256_by_candidate"]
        )
        ordinary_equal = (
            arm["ordinary_statistics_fingerprint"]
            == reference_physical["ordinary_statistics_fingerprint"]
        )
        settings_equal = arm["planner_settings"] == reference_settings
        order_equal = arm["actual_oid_order"] == arm["prescribed_order"]
        reference_objects = {
            item["candidate_id"]: item for item in reference_physical["physical_oids"]
        }
        observed_objects = {item["candidate_id"]: item for item in arm["physical_oids"]}
        definitions_equal = set(observed_objects) == set(reference_objects) and all(
            {
                "kind",
                "keys",
                "column_names",
                "statistics_target",
                "definition_fingerprint",
            }
            <= set(observed_objects[candidate_id])
            and all(
                observed_objects[candidate_id].get(field)
                == reference_objects[candidate_id].get(field)
                for field in (
                    "kind",
                    "keys",
                    "column_names",
                    "statistics_target",
                    "definition_fingerprint",
                )
            )
            for candidate_id in reference_objects
        )
        relation_equal = arm.get("relation_identity") == reference_physical.get("relation_identity")
        dataset_equal = arm.get("dataset_content_identity") == reference_physical.get(
            "dataset_content_identity"
        )
        target_equal = all(
            item.get("statistics_target") == STATISTICS_TARGET for item in arm["physical_oids"]
        ) and all(
            item.get("statistics_target") == STATISTICS_TARGET
            for item in reference_physical["physical_oids"]
        )
        arm["candidate_membership_digest"] = candidate_digest
        arm["causal_controls"] = {
            "membership_and_definitions_equal": definitions_equal,
            "payload_bytes_equal_to_reference": payload_equal,
            "ordinary_statistics_equal_to_reference": ordinary_equal,
            "planner_settings_equal_to_reference": settings_equal,
            "prescribed_oid_order_observed": order_equal,
            "relation_identity_equal": relation_equal and dataset_equal,
            "schema_identity_equal": relation_equal,
            "statistics_target_equal": target_equal,
        }
        arm["causal_status"] = (
            "reference-control"
            if arm_id == reference_id
            else "order-only-identified"
            if payload_equal
            and ordinary_equal
            and settings_equal
            and order_equal
            and definitions_equal
            and relation_equal
            and dataset_equal
            and target_equal
            else "confounded-operational-deployment"
        )
        arm["order_diagnostics"] = _order_diagnostics(arm, reference_physical, root=root)
    return {
        "format_version": FORMAT,
        "dataset_id": spec.dataset_id,
        "runtime_name": spec.runtime_name,
        "relation": spec.dataset_module.RELATION,
        "producer_research_sha": producer_sha,
        "rq1b_reference": {
            "design_digest": dataset["rq1b_design_digest"],
            "recommendation_digest": dataset["rq1b_recommendation_digest"],
            "deployment_digest": dataset["rq1b_deployment_digest"],
        },
        "valid_workload": {
            "workload_id": dataset["valid_workload_id"],
            "sha256": dataset["valid_workload_sha256"],
            "canonical_source_sha256": dataset["valid_canonical_workload_sha256"],
            "query_count": QUERY_COUNT,
        },
        "valid_truth": {
            "path": dataset["valid_truth_path"],
            "sha256": dataset["valid_truth_sha256"],
            "query_count": QUERY_COUNT,
            "query_ids": len(truth_by_id),
        },
        "candidate_membership": {
            "candidate_ids": list(dataset["reference_order"]),
            "digest": candidate_digest,
            "selected_k": len(dataset["reference_order"]),
            "definitions": dataset["selected_definitions"],
        },
        "snapshot_digest": snapshot_digest,
        "native_repository_digest": native_repository_digest,
        "native_payload_inventory": native_payloads,
        "sandbox": dict(sandbox),
        "hypothetical": hypothetical,
        "physical": physical,
        "test_workload_accessed": False,
        "test_truth_accessed": False,
        "formal_execution_scope": "valid-only-order-ablation",
        # The producer records only execution facts.  Evidence eligibility is
        # granted by the independent offline validator after all controls pass.
        "evidence_eligible": False,
        "cleanup": {"sandbox_destroyed": True, "runtime_removed": True},
    }


def _run_overlapping_mcv_witness(
    *, patched_dsn: str, stock_dsn: str, output: Path
) -> dict[str, Any]:
    """Run the preregistered small overlapping-MCV physical/overlay witness."""

    import psycopg
    from extstats_advisor.dbms.postgres.ordinary_stats import ordinary_stats_fingerprint

    del stock_dsn  # The patched catalogless backend is the source of this witness.
    table = f"oid_order_mcv_witness_{uuid.uuid4().hex[:12]}"
    query = f"SELECT * FROM {table} WHERE a = 1 AND b = 1 AND c = 1"
    arms = {
        "a-only": ("A",),
        "b-only": ("B",),
        "a-then-b": ("A", "B"),
        "b-then-a": ("B", "A"),
        "none": (),
    }
    candidates = {
        "A": {"kind": "mcv", "columns": ("a", "b")},
        "B": {"kind": "mcv", "columns": ("b", "c")},
    }
    results: dict[str, Any] = {}
    with psycopg.connect(patched_dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
        conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(f"CREATE TEMP TABLE {table} (a integer, b integer, c integer)")
        conn.execute(
            f"INSERT INTO {table} SELECT CASE WHEN i <= 700 THEN 1 ELSE i % 10 END, "
            f"CASE WHEN i <= 700 THEN 1 ELSE i % 10 END, "
            f"CASE WHEN i <= 350 THEN 1 ELSE (i + 3) % 10 END "
            f"FROM generate_series(1, 1000) AS s(i)"
        )
        conn.execute(f"ANALYZE {table}")
        relation_oid = int(conn.execute(f"SELECT '{table}'::regclass::oid").fetchone()[0])
        ordinary_baseline = ordinary_stats_fingerprint(conn, relation_oid)
        schema = str(conn.execute("SELECT current_schema()").fetchone()[0])
        relation = f"{schema}.{table}"
        for arm_id, order in arms.items():
            names: dict[str, str] = {}
            payloads: dict[str, bytes] = {}
            physical_objects: list[dict[str, Any]] = []
            for candidate_id in order:
                name = f"oid_order_mcv_{candidate_id.lower()}"
                names[candidate_id] = name
                columns = ", ".join(
                    _quote_identifier(item) for item in candidates[candidate_id]["columns"]
                )
                conn.execute(
                    f"CREATE STATISTICS {_quote_identifier(name)} (mcv) ON {columns} FROM {_quote_identifier(table)}"
                )
                conn.execute(f"ALTER STATISTICS {_quote_identifier(name)} SET STATISTICS 100")
            if order:
                conn.execute(f"ANALYZE {table}")
            for candidate_id in order:
                payload = __import__(
                    "extstats_advisor_research.rq3_fidelity", fromlist=["_physical_payload"]
                )._physical_payload(conn, relation_oid, names[candidate_id], "mcv")
                payloads[candidate_id] = payload["payload"]
                physical_objects.append(
                    {
                        "candidate_id": candidate_id,
                        "oid": payload["oid"],
                        "payload_sha256": payload["payload_sha256"],
                        "payload_size": payload["payload_size"],
                    }
                )
            physical_rows, physical_digest = _explain(conn, query, relation)
            physical_fingerprint = ordinary_stats_fingerprint(conn, relation_oid)
            conn.execute(
                f"DROP STATISTICS IF EXISTS {', '.join(_quote_identifier(name) for name in names.values())}"
            )
            if (
                int(
                    conn.execute(
                        "SELECT count(*) FROM pg_catalog.pg_statistic_ext WHERE stxrelid=%s",
                        (relation_oid,),
                    ).fetchone()[0]
                )
                != 0
            ):
                raise ValueError("synthetic witness physical statistics leaked")
            if ordinary_stats_fingerprint(conn, relation_oid) != ordinary_baseline:
                raise ValueError("synthetic witness ordinary statistics drifted")
            conn.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
            virtual_oids: list[int] = []
            for candidate_id in order:
                kind = "m"
                row = conn.execute(
                    'SELECT pg_catalog.pg_hypothetical_extstats_register_definition(%s::text,%s::oid,%s::"char",%s::smallint[],%s::bytea)',
                    (
                        candidate_id,
                        relation_oid,
                        kind,
                        [1, 2] if candidate_id == "A" else [2, 3],
                        payloads[candidate_id],
                    ),
                ).fetchone()
                virtual_oids.append(int(row[0]))
            conn.execute(
                "SELECT pg_catalog.pg_hypothetical_extstats_activate(%s::oid[])", (virtual_oids,)
            )
            hypothetical_rows, hypothetical_digest = _explain(conn, query, relation)
            conn.execute("SELECT pg_catalog.pg_hypothetical_extstats_reset()")
            results[arm_id] = {
                "order": list(order),
                "physical_plan_rows": physical_rows,
                "hypothetical_plan_rows": hypothetical_rows,
                "physical_explain_sha256": physical_digest,
                "hypothetical_explain_sha256": hypothetical_digest,
                "exact_root_plan_rows_match": physical_rows == hypothetical_rows,
                "payload_sha256_by_candidate": {
                    item["candidate_id"]: item["payload_sha256"] for item in physical_objects
                },
                "physical_objects": physical_objects,
                "ordinary_statistics_fingerprint": physical_fingerprint,
            }
        conn.execute(f"DROP TABLE {table}")
    value = {
        "format_version": "oid-order-overlapping-mcv-witness-v1",
        "fixture_id": "oid-order-overlapping-mcv-witness-v1",
        "query": query,
        "static_mechanism_precondition": {
            "mcvs": {"A": ["a", "b"], "B": ["b", "c"]},
            "matching_attribute_count": {"A": 2, "B": 2},
            "key_count": {"A": 2, "B": 2},
            "tie_precondition_source_level": True,
            "runtime_tie_observation": "not-directly-instrumented",
        },
        "arms": results,
        "physical_hypothetical_exact_match_count": sum(
            item["exact_root_plan_rows_match"] for item in results.values()
        ),
        "physical_hypothetical_arm_count": len(results),
        "post_cleanup": True,
    }
    value["semantic_digest"] = semantic_digest(value)
    write_json(output, value)
    return value


def run_formal(
    *,
    root: Path,
    preflight_path: Path,
    data_root: Path,
    stock_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    producer_sha: str,
    invocation_id: str,
    failure_output: Path,
) -> dict[str, Any]:
    """Run the declared four-dataset experiment once; no retry is internal."""

    preflight = read_json(preflight_path)
    state = ExecutionState(invocation_id=invocation_id)
    if not invocation_id or not re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", invocation_id):
        raise ValueError("invocation_id must be an explicit stable token")
    if failure_output.exists() or failure_output.is_symlink():
        raise FileExistsError(f"failure output already exists: {failure_output}")
    validate_preflight(preflight, root=root)
    if preflight.get("producer_research_sha") != producer_sha:
        raise ValueError("preflight producer SHA does not match formal producer")
    from .advisor_bridge import materialize_native_repository
    from .postgres.loader import load_census13, load_dmv11, load_forest10, load_power7
    from .postgres_lab import reinit_role, role_spec, stop_role

    loaders = {
        "arecel-census13": load_census13,
        "arecel-forest10": load_forest10,
        "arecel-power7": load_power7,
        "arecel-dmv11": load_dmv11,
    }
    _validate_managed_lab_dsns(stock_dsn=stock_dsn, planner_dsn=planner_dsn)
    validate_system_freeze_v2(read_json(root / "paper/system-freeze-v2.json"))
    pins = verify_frozen_systems_v2(
        advisor_root.resolve(), patched_postgres_root.resolve(), stock_postgres_root.resolve()
    )
    runtime_base = root / ".runtime" / "oid-order-sensitivity-v1"
    runtime_base.mkdir(parents=True, exist_ok=False)
    try:
        state.transition("launcher-probe")
        launcher = prepare_frozen_advisor_launcher(
            advisor_root=advisor_root, runtime_root=runtime_base
        )
        launcher_path = Path(launcher["launcher_path"])
    except Exception as error:
        write_failure_artifact(
            failure_output,
            producer_sha=producer_sha,
            preflight_path=preflight_path,
            preflight=preflight,
            state=state,
            exception=error,
        )
        shutil.rmtree(runtime_base, ignore_errors=True)
        raise
    dataset_results: list[dict[str, Any]] = []
    try:
        state.transition("lab-preparation")
        prepare_power7_rq1b_formal_labs(
            advisor_root=advisor_root,
            patched_postgres_root=patched_postgres_root,
            stock_postgres_root=stock_postgres_root,
        )
        state.stock_owned = True
        state.patched_owned = True
        for dataset in preflight["datasets"]:
            state.transition(f"{dataset['dataset_id']}:snapshot")
            spec = next(item for item in DATASET_SPECS if item.dataset_id == dataset["dataset_id"])
            runtime = runtime_base / spec.runtime_name
            runtime.mkdir(parents=True, exist_ok=False)
            log_dir = runtime / "logs"
            output_dir = root / OUTPUT_ROOT / spec.runtime_name
            output_dir.mkdir(parents=True, exist_ok=False)
            reinit_role("stock")
            reinit_role("patched")
            load = loaders[spec.dataset_id](
                stock_dsn,
                data_root=data_root,
                reset_disposable=True,
                statistics_target=STATISTICS_TARGET,
                seed_identifier=123,
            )
            if (
                load.get("physical_extended_statistics_count") != 0
                or load.get("analyze_count") != 1
            ):
                raise ValueError(f"{spec.dataset_id} fresh stock load contract failed")
            workload_path = runtime / "valid-workload.json"
            spec.dataset_module.extract_workload(workload_path, data_root, split="valid")
            workload = read_json(workload_path)
            sys.path.insert(0, str(advisor_root / "src"))
            from extstats_advisor.candidates import (
                derive_candidate_universe,
                write_candidate_universe,
            )
            from extstats_advisor.dbms.base import AcquisitionRequest, SamplePolicy
            from extstats_advisor.dbms.postgres.acquisition import PostgresSnapshotAcquirer
            from extstats_advisor.ground_truth.artifact import write_ground_truth_set
            from extstats_advisor.snapshot.bundle import write_snapshot
            from extstats_advisor.snapshot.model import Workload, WorkloadQuery

            from extstats_advisor_research.external_truth import import_audited_authoritative_truth

            w = Workload(
                workload["workload_id"],
                tuple(
                    WorkloadQuery(item["query_id"], item["sql"], item["weight"])
                    for item in workload["queries"]
                ),
                workload.get("provenance", {}),
            )
            snapshot_path = runtime / "snapshot"
            snapshot = PostgresSnapshotAcquirer(stock_dsn).capture(
                AcquisitionRequest(
                    spec.dataset_module.RELATION, SamplePolicy(SAMPLE_ROWS, seed=SAMPLE_SEED)
                ),
                w,
            )
            write_snapshot(snapshot, snapshot_path)
            sealed_snapshot = _load_sealed_snapshot(snapshot_path)
            state.snapshot_completed = True
            truth_set = import_audited_authoritative_truth(
                sealed_snapshot,
                root / spec.valid_observations_path,
                authority="sfu-db/AreCELearnedYet",
                dataset_identity=spec.dataset_content_identity,
                source_revision=dataset["upstream_commit"],
            )
            write_ground_truth_set(truth_set, runtime / "ground-truth.json")
            candidate_path = runtime / "candidate-universe.json"
            write_candidate_universe(derive_candidate_universe(sealed_snapshot), candidate_path)
            native_path = runtime / "native-stats-repository"
            materialize_native_repository(
                advisor_root,
                planner_dsn,
                snapshot_path,
                candidate_path,
                native_path,
                STATISTICS_TARGET,
            )
            prepare_result = _run_command(
                [
                    str(launcher_path),
                    "sandbox",
                    "prepare",
                    "postgres",
                    str(snapshot_path),
                    str(candidate_path),
                    str(native_path),
                    "--dsn",
                    planner_dsn,
                ],
                log_dir,
            )
            state.sandbox_prepared = True
            state.transition(f"{spec.dataset_id}:hypothetical")
            verify_result = _run_command(
                [
                    str(launcher_path),
                    "sandbox",
                    "verify",
                    "postgres",
                    str(snapshot_path),
                    str(candidate_path),
                    str(native_path),
                    "--dsn",
                    planner_dsn,
                ],
                log_dir,
            )
            truth_wire = read_json(root / spec.valid_observations_path)
            truth_by_id = {row["query_id"]: int(row["cardinality"]) for row in truth_wire["truths"]}
            if len(truth_by_id) != QUERY_COUNT:
                raise ValueError(f"{spec.dataset_id} valid truth IDs are not unique")
            manifest_by_id = {
                item["query_id"]: item for item in dataset["valid_workload"]["queries"]
            }
            queries = [
                {
                    "query_id": item.query_id,
                    "sql": item.sql,
                    "source_query_sha256": manifest_by_id[item.query_id]["source_query_sha256"],
                }
                for item in sealed_snapshot.workload.queries
            ]
            candidates = {
                item["candidate_id"]: item for item in read_json(candidate_path)["candidates"]
            }
            state.hypothetical_active = True
            hypothetical = _hypothetical_arms(
                planner_dsn=planner_dsn,
                advisor_root=advisor_root,
                snapshot_path=snapshot_path,
                candidate_path=candidate_path,
                native_path=native_path,
                orders=dataset["permutations"],
                queries=queries,
                truth_by_id=truth_by_id,
                relation=spec.dataset_module.RELATION,
                dataset_id=spec.dataset_id,
                dataset_content_identity=spec.dataset_content_identity,
                output_dir=output_dir,
                research_root=root,
            )
            state.hypothetical_active = False
            physical: dict[str, dict[str, Any]] = {}
            state.physical_deployment_started = True
            for arm in dataset["permutations"]:
                physical[arm["permutation_id"]] = _physical_arm(
                    dsn=stock_dsn,
                    parent_database=role_spec("stock").database,
                    relation=spec.dataset_module.RELATION,
                    candidates=candidates,
                    order=arm["order"],
                    queries=queries,
                    truth_by_id=truth_by_id,
                    output=output_dir
                    / "physical-stock"
                    / arm["permutation_id"]
                    / "per-query.jsonl.gz",
                    research_root=root,
                    dataset_id=spec.dataset_id,
                    dataset_content_identity=spec.dataset_content_identity,
                )
            state.physical_deployment_started = False
            _run_command(
                [str(launcher_path), "sandbox", "destroy", "postgres", "--dsn", planner_dsn],
                log_dir,
            )
            state.sandbox_prepared = False
            dataset_result = _assemble_dataset_result(
                root=root,
                spec=spec,
                dataset=dataset,
                producer_sha=producer_sha,
                output_dir=output_dir,
                snapshot_digest=_load_sealed_snapshot(snapshot_path).semantic_digest,
                native_repository_digest=read_json(native_path / "manifest.json")[
                    "semantic_digest"
                ],
                truth_by_id=truth_by_id,
                hypothetical=hypothetical,
                physical=physical,
                sandbox={"prepare": prepare_result, "verify": verify_result},
            )
            write_json(output_dir / "result-v1.json", dataset_result)
            dataset_results.append(dataset_result)
            shutil.rmtree(runtime, ignore_errors=True)
        state.synthetic_fixture_created = True
        synthetic = _run_overlapping_mcv_witness(
            patched_dsn=planner_dsn,
            stock_dsn=stock_dsn,
            output=root / OUTPUT_ROOT / "synthetic-witness-v1.json",
        )
        state.synthetic_fixture_created = False
        summary: dict[str, Any] = {
            "format_version": SUMMARY_FORMAT,
            "experiment_id": EXPERIMENT_ID,
            "status": "complete",
            "producer_research_sha": producer_sha,
            "formal_invocation_count": 1,
            "dataset_results": dataset_results,
            "synthetic_witness": synthetic,
            "system_identity": pins,
            "frozen_system": {
                "advisor_sha": FROZEN_ADVISOR_SHA,
                "patched_postgres_sha": FROZEN_PATCHED_POSTGRES_SHA,
                "stock_postgres_sha": FROZEN_STOCK_POSTGRES_SHA,
                "postgres_version": "16.14",
            },
        }
        summary["semantic_digest"] = semantic_digest(summary)
        write_json(root / OUTPUT_ROOT / "summary-v1.json", summary)
        return summary
    finally:
        primary_error = sys.exc_info()[1]
        cleanup_errors: list[dict[str, Any]] = []
        if state.sandbox_prepared:
            try:
                _run_command(
                    [str(launcher_path), "sandbox", "destroy", "postgres", "--dsn", planner_dsn],
                    runtime_base / "cleanup-logs",
                )
                state.sandbox_prepared = False
                state.cleanup.append({"operation": "sandbox-destroy", "status": "passed"})
            except Exception as error:  # noqa: BLE001 - preserve primary failure
                cleanup_errors.append(
                    {"operation": "sandbox-destroy", "status": "failed", "error": str(error)}
                )
        for role, owned in (("stock", state.stock_owned), ("patched", state.patched_owned)):
            if owned:
                try:
                    stop_role(role)
                    state.cleanup.append({"operation": f"stop-{role}", "status": "passed"})
                except Exception as error:  # noqa: BLE001 - preserve primary failure
                    cleanup_errors.append(
                        {"operation": f"stop-{role}", "status": "failed", "error": str(error)}
                    )
        shutil.rmtree(runtime_base, ignore_errors=True)
        state.cleanup.append({"operation": "runtime-remove", "status": "passed"})
        if primary_error is not None or cleanup_errors:
            error = primary_error or RuntimeError("cleanup failed")
            try:
                write_failure_artifact(
                    failure_output,
                    producer_sha=producer_sha,
                    preflight_path=preflight_path,
                    preflight=preflight,
                    state=state,
                    exception=error,
                    cleanup_errors=cleanup_errors,
                )
            except FileExistsError:
                pass
            if primary_error is None and cleanup_errors:
                raise RuntimeError(f"OID-order cleanup failed: {cleanup_errors}")
