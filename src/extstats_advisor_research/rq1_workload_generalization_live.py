"""Power7 RQ1b design/evaluation harness boundaries.

This module prepares the future live runner without executing it.  The two
stage API deliberately requires an injected executor: the frozen Advisor and
PostgreSQL are not imported or started by the offline readiness harness.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .arecel_truth import authoritative_truth_spec_for_split
from .datasets import DATASETS
from .paper_baseline import percentile, qerror
from .provenance import read_json, semantic_digest, sha256_file
from .rq1_workload_generalization import (
    PROTOCOL_V2_PATH,
    SOURCE_AUDIT_V2_PATH,
    STRICT_UNSEEN_PATH,
    TRUTH_POLICY_PATH,
    validate_protocol_v2,
    validate_source_audit_v2,
    validate_strict_unseen_membership,
    validate_truth_policy,
)
from .system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_STOCK_POSTGRES_SHA,
    FROZEN_SYSTEM_FREEZE_V2_DIGEST,
    load_system_freeze_v2,
)

POWER7 = "arecel-power7"
DESIGN_SPLIT = "valid"
EVALUATION_SPLIT = "test"
SAMPLE_ROWS = 10_000
SAMPLE_SEED = 42
STATISTICS_TARGET = 100
K_S = 8
B = 8
SEARCH_BUDGET_SECONDS = 300
PROTOCOL_DIGEST = "ffa5451d5212a09c4eb4713cb514945119d89a0ad6254b034a81ab7a3a88c602"
TRUTH_POLICY_DIGEST = "e880f0f5b6cebd468fbadcd235408ba3a4b07ee9313f4705d11cf8f4fe83d36a"
SOURCE_AUDIT_DIGEST = "05b32b600bea27a937db4de9af005c223fef67c168e3548c9c862eb6b710164e"
STRICT_UNSEEN_DIGEST = "30c8e516d44269508a944f8c73b0927b2cc213aa229ae57cb57ef757be6401be"
RQ1A_POWER7_DIGEST = "14a809c8677db1007fa602ead1b2b5ab362c119f8cb197724b5c5de4a2959124"
SYSTEM_FREEZE_PATH = Path("paper/system-freeze-v2.json")
DESIGN_FORMAT = "rq1-workload-generalization-power7-design-v1"
RESULT_FORMAT = "rq1-workload-generalization-power7-v1"
PREFLIGHT_FORMAT = "rq1-workload-generalization-power7-preflight-v1"
PREFLIGHT_PATH = Path(
    "experiments/arecel-power7/rq1-workload-generalization-v1/rq1b-preflight-v1.json"
)
RESULT_PATH = Path("experiments/arecel-power7/rq1-workload-generalization-v1/result-v1.json")
BASELINE_PATH = Path("experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json")


class RQ1BValidationError(ValueError):
    """Controlled failure for RQ1b provenance or stage-boundary violations."""


DesignExecutor = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ1BValidationError(message)


def _digest_body(value: Mapping[str, Any]) -> str:
    return semantic_digest({key: item for key, item in value.items() if key != "semantic_digest"})


def _sha(value: Any, label: str) -> None:
    _require(isinstance(value, str) and len(value) == 64, f"{label} must be a SHA-256 digest")


def _commit(value: Any, label: str) -> None:
    _require(isinstance(value, str) and len(value) == 40, f"{label} must be a commit SHA")


def _path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return (candidate if candidate.is_absolute() else root / candidate).resolve()


def _binding(path: Path, digest: str) -> dict[str, str]:
    return {"path": path.as_posix(), "semantic_digest": digest}


def _load_exact(root: Path, relative: Path, expected: str) -> dict[str, Any]:
    path = root / relative
    _require(path.is_file(), f"missing immutable RQ1b artifact: {relative}")
    value = read_json(path)
    actual = value.get("semantic_digest")
    _require(actual == expected and _digest_body(value) == expected, f"{relative} digest mismatch")
    return value


def _validate_immutable_inputs(root: Path, *, include_evaluation: bool) -> dict[str, Any]:
    protocol = _load_exact(root, PROTOCOL_V2_PATH, PROTOCOL_DIGEST)
    truth_policy = _load_exact(root, TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST)
    audit = _load_exact(root, SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST)
    membership = (
        _load_exact(root, STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST) if include_evaluation else None
    )
    validate_protocol_v2(root / PROTOCOL_V2_PATH)
    validate_truth_policy(root / TRUTH_POLICY_PATH, root)
    validate_source_audit_v2(root / SOURCE_AUDIT_V2_PATH, root)
    if include_evaluation:
        validate_strict_unseen_membership(root / STRICT_UNSEEN_PATH, root)
    return {
        "protocol": protocol,
        "truth_policy": truth_policy,
        "source_audit": audit,
        "strict_unseen": membership,
    }


def _power7_row(value: Mapping[str, Any]) -> dict[str, Any]:
    rows = [row for row in value.get("datasets", []) if row.get("dataset_id") == POWER7]
    _require(len(rows) == 1, "RQ1b immutable input must contain exactly one Power7 row")
    return dict(rows[0])


def _valid_truth_identity(root: Path) -> dict[str, Any]:
    spec = authoritative_truth_spec_for_split(POWER7, DESIGN_SPLIT, root)
    _require(spec.get("query_count") == SAMPLE_ROWS, "Power7 valid truth count is not 10000")
    truth_path = Path(spec["observations_path"])
    wire = read_json(truth_path)
    _require(wire.get("workload_id") == "arecel_power7_valid_v1", "Power7 valid truth ID drift")
    return {
        "source_kind": spec["kind"],
        "collection_contract": spec["collection_contract"],
        "dataset_identity": spec["dataset_identity"],
        "source_revision": spec["source_revision"],
        "observations_sha256": spec["observations_sha256"],
        "observations_semantic_digest": semantic_digest(
            {key: item for key, item in wire.items() if key != "semantic_digest"}
        ),
        "workload_id": wire["workload_id"],
        "query_count": spec["query_count"],
        "policy_status": spec["policy_status"],
    }


def _valid_design_inputs(root: Path, data_root: Path | None = None) -> dict[str, Any]:
    """Resolve only valid-side inputs; no test/membership artifacts are loaded."""

    dataset = DATASETS[POWER7]
    metadata = dataset.inspect(data_root)
    truth = _valid_truth_identity(root)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="rq1b-power7-valid-") as directory:
        workload_path = Path(directory) / "valid-workload.json"
        identity = dataset.extract_workload(workload_path, data_root, split=DESIGN_SPLIT)
        workload = read_json(workload_path)
    _require(identity["workload_id"] == "arecel_power7_valid_v1", "Power7 valid workload ID drift")
    _require(identity["query_count"] == SAMPLE_ROWS, "Power7 valid workload count is not 10000")
    return {
        "dataset": {
            "dataset_id": POWER7,
            "benchmark_id": metadata["benchmark_id"],
            "content_identity": metadata["dataset_content_identity"],
            "relation": metadata["relation"],
            "schema_contract_id": metadata["schema_contract"]["id"],
            "rows": dataset.EXPECTED_ROWS,
        },
        "workload": {
            "workload_id": identity["workload_id"],
            "sha256": identity["sha256"],
            "query_count": identity["query_count"],
            "canonical_source_sha256": dataset.CANONICAL_WORKLOAD_SHA256,
        },
        "truth": truth,
        "workload_queries": workload["queries"],
    }


def _system_binding(root: Path) -> dict[str, Any]:
    freeze = load_system_freeze_v2(root / SYSTEM_FREEZE_PATH)
    _require(
        semantic_digest({key: item for key, item in freeze.items() if key != "semantic_digest"})
        == FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        "system-freeze-v2 digest drift",
    )
    return {
        "path": SYSTEM_FREEZE_PATH.as_posix(),
        "semantic_digest": FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "patched_postgresql_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "stock_postgresql_sha": FROZEN_STOCK_POSTGRES_SHA,
        "postgres_version": "16.14",
    }


def _require_execution_digests(execution: Mapping[str, Any]) -> None:
    required = (
        "advisor_run_manifest_digest",
        "snapshot_digest",
        "ground_truth_set_digest",
        "candidate_universe_digest",
        "native_repository_digest",
        "singleton_profile_digest",
        "optimization_plan_digest",
        "search_result_digest",
        "recommendation_digest",
    )
    for field in required:
        _sha(execution.get(field), f"design {field}")
    _require(execution.get("design_stage_complete") is True, "design stage is not complete")
    _require(execution.get("recommendation_sealed") is True, "Recommendation is not sealed")
    _require(
        isinstance(execution.get("selected_candidate_ids"), list), "selected membership missing"
    )
    _require(
        isinstance(execution.get("deployment_ordered_candidate_ids"), list),
        "deployment order missing",
    )
    _require(
        execution["selected_candidate_ids"] == execution["deployment_ordered_candidate_ids"],
        "deployment order must bind sealed membership",
    )
    _require(execution["selected_candidate_ids"], "sealed Recommendation cannot be empty")


def build_design_artifact(
    *,
    research_root: Path,
    producer_sha: str,
    execution: Mapping[str, Any],
    data_root: Path | None = None,
    design_inputs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble and validate compact valid-only design evidence."""

    root = research_root.resolve()
    _commit(producer_sha, "research_commit_sha")
    _require_execution_digests(execution)
    inputs = (
        dict(design_inputs) if design_inputs is not None else _valid_design_inputs(root, data_root)
    )
    system = _system_binding(root)
    _require(
        execution.get("advisor_sha", FROZEN_ADVISOR_SHA) == FROZEN_ADVISOR_SHA,
        "design Advisor SHA drift",
    )
    _require(
        execution.get("patched_postgresql_sha", FROZEN_PATCHED_POSTGRES_SHA)
        == FROZEN_PATCHED_POSTGRES_SHA,
        "design patched PostgreSQL SHA drift",
    )
    body: dict[str, Any] = {
        "format_version": DESIGN_FORMAT,
        "experiment_id": "rq1-held-out-workload-generalization",
        "rq": "RQ1b",
        "status": "design-complete",
        "producer": {"research_commit_sha": producer_sha},
        "protocol": _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "truth_policy": _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "system_freeze": system,
        "dataset": inputs["dataset"],
        "design_stage": {
            "input_split": DESIGN_SPLIT,
            "workload": {
                key: value for key, value in inputs["workload"].items() if key != "queries"
            },
            "truth": inputs["truth"],
            "advisor_sha": FROZEN_ADVISOR_SHA,
            "patched_postgresql_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "K_s": K_S,
            "B": B,
            "T_seconds": SEARCH_BUDGET_SECONDS,
            **dict(execution),
        },
        "evaluation_stage": {
            "status": "not-started",
            "test_inputs_accessed": False,
            "recommendation_input_is_immutable": True,
        },
        "cleanup": {"required_before_publish": True, "passed": False},
    }
    validate_design_artifact(body, expected_producer=producer_sha)
    body["semantic_digest"] = semantic_digest(body)
    return body


def run_power7_rq1b_design(
    *,
    research_root: Path,
    producer_sha: str,
    design_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Run the valid-only orchestration through an injected frozen pipeline.

    ``design_executor`` is the only execution hook.  A future live adapter
    must call the frozen Advisor primitives in that hook; this readiness
    module itself never starts PostgreSQL or imports the Advisor CLI.
    """

    inputs = _valid_design_inputs(research_root.resolve(), data_root)
    execution = design_executor(inputs)
    _require(isinstance(execution, Mapping), "design executor must return an object")
    return build_design_artifact(
        research_root=research_root,
        producer_sha=producer_sha,
        execution=execution,
        data_root=data_root,
        design_inputs=inputs,
    )


def validate_design_artifact(
    value: Mapping[str, Any], *, expected_producer: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RQ1BValidationError("design artifact must be an object")
    _require(value.get("format_version") == DESIGN_FORMAT, "unsupported RQ1b design format")
    _require(value.get("status") == "design-complete", "design artifact is not complete")
    _require(value.get("rq") == "RQ1b", "design artifact has the wrong RQ")
    producer = value.get("producer", {}).get("research_commit_sha")
    _commit(producer, "design producer SHA")
    if expected_producer is not None:
        _require(producer == expected_producer, "design producer SHA mismatch")
    _require(
        value.get("protocol") == _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "design protocol binding drift",
    )
    _require(
        value.get("truth_policy") == _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "design truth policy binding drift",
    )
    system = value.get("system_freeze")
    _require(isinstance(system, Mapping), "design system freeze binding missing")
    _require(
        system.get("semantic_digest") == FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        "design system freeze digest drift",
    )
    _require(system.get("advisor_sha") == FROZEN_ADVISOR_SHA, "design Advisor SHA drift")
    _require(
        system.get("patched_postgresql_sha") == FROZEN_PATCHED_POSTGRES_SHA,
        "design patched PostgreSQL SHA drift",
    )
    _require(
        system.get("stock_postgresql_sha") == FROZEN_STOCK_POSTGRES_SHA,
        "design stock PostgreSQL SHA drift",
    )
    dataset = value.get("dataset")
    _require(
        isinstance(dataset, Mapping) and dataset.get("dataset_id") == POWER7,
        "design dataset must be Power7",
    )
    stage = value.get("design_stage")
    _require(
        isinstance(stage, Mapping) and stage.get("input_split") == DESIGN_SPLIT,
        "design stage split drift",
    )
    _require(stage.get("design_stage_complete") is True, "design stage completion missing")
    _require(stage.get("recommendation_sealed") is True, "Recommendation seal missing")
    _require(
        stage.get("workload", {}).get("workload_id") == "arecel_power7_valid_v1",
        "design workload is not valid",
    )
    _sha(stage.get("workload", {}).get("sha256"), "design valid workload SHA")
    _sha(
        stage.get("workload", {}).get("canonical_source_sha256"),
        "design canonical workload SHA",
    )
    _require(
        stage.get("truth", {}).get("workload_id") == "arecel_power7_valid_v1",
        "design truth is not valid",
    )
    _sha(stage.get("truth", {}).get("observations_sha256"), "design valid truth SHA")
    for forbidden in (
        "test_workload",
        "test_truth",
        "strict_unseen",
        "S_test",
        "test_qerror",
        "overlap",
    ):
        _require(
            forbidden not in json.dumps(stage, sort_keys=True),
            f"design stage contains forbidden test input: {forbidden}",
        )
    _require(
        stage.get("selected_candidate_ids") == stage.get("deployment_ordered_candidate_ids"),
        "sealed membership/order mismatch",
    )
    for field in ("recommendation_digest", "snapshot_digest", "ground_truth_set_digest"):
        _sha(stage.get(field), f"design {field}")
    if "semantic_digest" in value:
        _require(value["semantic_digest"] == _digest_body(value), "design artifact digest mismatch")
    return {
        "status": "valid",
        "experiment_id": value["experiment_id"],
        "semantic_digest": value.get("semantic_digest"),
    }


def _source_spec(root: Path) -> dict[str, Any]:
    inputs = _validate_immutable_inputs(root, include_evaluation=True)
    row = _power7_row(inputs["source_audit"])
    truth_rows = _power7_row(inputs["truth_policy"])
    return {
        "dataset_id": POWER7,
        "benchmark_id": row["benchmark_id"],
        "dataset_content_identity": row["dataset_content_identity"],
        "relation": row["relation"],
        "schema_contract_id": row["schema_contract_id"],
        "canonical_workload_sha256": row["source_file_sha256"]["canonical_workload"],
        "valid_workload_id": row["valid_workload_id"],
        "valid_workload_sha256": row["valid_workload_sha256"],
        "valid_query_count": row["valid_query_count"],
        "test_workload_id": row["test_workload_id"],
        "test_workload_sha256": row["test_workload_sha256"],
        "test_query_count": row["test_query_count"],
        "valid_observations_path": truth_rows["valid_observations_path"],
        "valid_observations_sha256": truth_rows["valid_observations_sha256"],
        "test_observations_path": truth_rows["test_observations_path"],
        "test_observations_sha256": truth_rows["test_observations_sha256"],
    }


def _baseline_binding(root: Path) -> dict[str, Any]:
    path = root / BASELINE_PATH
    _require(path.is_file(), "Power7 immutable RQ1a baseline is missing")
    artifact = read_json(path)
    _require(
        semantic_digest({key: item for key, item in artifact.items() if key != "semantic_digest"})
        == RQ1A_POWER7_DIGEST,
        "Power7 RQ1a source digest mismatch",
    )
    _require(
        artifact.get("format_version") == "rq1-matched-comparison-v1"
        and artifact.get("experiment_id") == "rq1-matched-comparison-v1",
        "Power7 RQ1a source artifact identity mismatch",
    )
    _require(
        artifact.get("arms") == ["pg16-default", "pg16-target10000", "pg16-advisor"],
        "Power7 RQ1a source arms drift",
    )
    source = _source_spec(root)
    _require(
        artifact["dataset"]["content_identity"] == source["dataset_content_identity"],
        "baseline dataset identity mismatch",
    )
    _require(
        artifact["workload"]["workload_id"] == source["test_workload_id"],
        "baseline test workload ID mismatch",
    )
    _require(
        artifact["workload"]["sha256"] == source["test_workload_sha256"],
        "baseline test workload hash mismatch",
    )
    _require(
        artifact["truth"]["observations_sha256"] == source["test_observations_sha256"],
        "baseline test truth mismatch",
    )
    result: dict[str, Any] = {
        "path": BASELINE_PATH.as_posix(),
        "semantic_digest": RQ1A_POWER7_DIGEST,
        "arms": {},
    }
    expected_columns = [name for name, _ in DATASETS[POWER7].COLUMNS]
    for arm_id in ("pg16-default", "pg16-target10000"):
        arm = artifact["per_arm"][arm_id]
        _require(
            arm.get("workload_identity") == artifact["workload"],
            f"baseline {arm_id} workload identity mismatch",
        )
        _require(
            arm.get("truth_identity") == artifact["truth"],
            f"baseline {arm_id} truth identity mismatch",
        )
        _require(
            arm.get("evaluation_mode") == "stock-full-data-physical",
            f"baseline {arm_id} is not stock full-data physical evidence",
        )
        _require(
            arm.get("physical_extended_statistics_count") == 0
            and arm.get("physical_extended_statistics") == [],
            f"baseline {arm_id} has an extended-statistics leak",
        )
        _require(
            arm["deployment_build_identity"]["source_commit_sha"] == FROZEN_STOCK_POSTGRES_SHA,
            "baseline stock build mismatch",
        )
        _require(
            arm["deployment_build_identity"]["postgres_version"] == "16.14",
            "baseline PostgreSQL version mismatch",
        )
        policy = arm.get("statistics_policy")
        target = 100 if arm_id == "pg16-default" else 10_000
        _require(
            isinstance(policy, dict)
            and policy.get("requested_target") == target
            and policy.get("seed_identifier") == 123
            and policy.get("setseed_sql") == "SELECT setseed(1.0 / 123)",
            f"baseline {arm_id} ordinary-statistics policy mismatch",
        )
        actual_targets = policy.get("actual_column_targets", [])
        _require(
            [row.get("name") for row in actual_targets] == expected_columns
            and all(row.get("target") == target for row in actual_targets),
            f"baseline {arm_id} ordinary column targets mismatch",
        )
        records = arm["per_query"]
        _require(len(records) == SAMPLE_ROWS, "baseline per-query count mismatch")
        expected_ids = [f"arecel_power7_test_{index:06d}" for index in range(SAMPLE_ROWS)]
        _require(
            [row.get("query_id") for row in records] == expected_ids,
            "baseline query order mismatch",
        )
        relative = Path(arm["per_query_artifact"]["logical_path"])
        per_query_path = root / relative
        _require(
            sha256_file(per_query_path) == arm["per_query_artifact"]["sha256"],
            f"baseline {arm_id} per-query SHA mismatch",
        )
        result["arms"][arm_id] = {
            "per_query_path": relative.as_posix(),
            "per_query_sha256": arm["per_query_artifact"]["sha256"],
            "ordinary_statistics_fingerprint": arm["ordinary_statistics_fingerprint"],
            "statistics_policy": arm["statistics_policy"],
        }
    return result


def build_power7_rq1b_preflight(
    *, research_root: Path, producer_sha: str, output: Path | None = None
) -> dict[str, Any]:
    """Build an in-memory preflight; callers may persist it in a future live phase."""

    root = research_root.resolve()
    _commit(producer_sha, "preflight producer SHA")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _require(not status, "RQ1b preflight requires a clean committed tree")
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _require(head == producer_sha, "preflight producer is not current HEAD")
    if output is not None:
        destination = _path(root, output)
        _require(not destination.exists(), "RQ1b formal preflight output already exists")
        _require(
            destination == root / PREFLIGHT_PATH, "RQ1b preflight output path is not canonical"
        )
    _validate_immutable_inputs(root, include_evaluation=True)
    source = _source_spec(root)
    baseline = _baseline_binding(root)
    body: dict[str, Any] = {
        "format_version": PREFLIGHT_FORMAT,
        "status": "ready-to-run",
        "formal_execution": "not-started",
        "research_commit_sha": producer_sha,
        "protocol": _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "truth_policy": _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "source_audit": _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "strict_unseen_membership": _binding(STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST),
        "system_freeze": _system_binding(root),
        "dataset": source,
        "baseline_reuse": baseline,
        "design_output": {
            "canonical_path": "experiments/arecel-power7/rq1-workload-generalization-v1/design-v1.json"
        },
        "evaluation_output": {"canonical_path": RESULT_PATH.as_posix()},
        "parameters": {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "K_s": K_S,
            "B": B,
            "T_seconds": SEARCH_BUDGET_SECONDS,
        },
        "no_formal_execution_started": True,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_power7_rq1b_preflight(
    value: Mapping[str, Any], *, research_root: Path
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RQ1BValidationError("RQ1b preflight must be an object")
    _require(value.get("format_version") == PREFLIGHT_FORMAT, "unsupported RQ1b preflight")
    _require(value.get("status") == "ready-to-run", "RQ1b preflight is not ready")
    _require(value.get("formal_execution") == "not-started", "RQ1b preflight execution state drift")
    _commit(value.get("research_commit_sha"), "preflight producer SHA")
    _require(
        value.get("protocol") == _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "preflight protocol drift",
    )
    _require(
        value.get("truth_policy") == _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "preflight truth policy drift",
    )
    _require(
        value.get("source_audit") == _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "preflight source audit drift",
    )
    _require(
        value.get("strict_unseen_membership") == _binding(STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST),
        "preflight strict membership drift",
    )
    _require(
        value.get("system_freeze", {}).get("semantic_digest") == FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        "preflight system freeze drift",
    )
    _require(
        value.get("dataset") == _source_spec(research_root.resolve()),
        "preflight Power7 source spec drift",
    )
    _require(
        value.get("baseline_reuse") == _baseline_binding(research_root.resolve()),
        "preflight baseline binding drift",
    )
    _require(
        value.get("parameters")
        == {
            "sample_rows": SAMPLE_ROWS,
            "sample_seed": SAMPLE_SEED,
            "statistics_target": STATISTICS_TARGET,
            "K_s": K_S,
            "B": B,
            "T_seconds": SEARCH_BUDGET_SECONDS,
        },
        "preflight parameter drift",
    )
    _require(
        value.get("no_formal_execution_started") is True, "preflight formal execution marker drift"
    )
    if "semantic_digest" in value:
        _require(
            value["semantic_digest"] == _digest_body(value), "preflight semantic digest mismatch"
        )
    return {
        "status": "valid",
        "semantic_digest": value.get("semantic_digest"),
        "dataset_id": POWER7,
    }


def verify_power7_formal_tree(
    *, research_root: Path, preflight_path: Path, preflight: Mapping[str, Any]
) -> dict[str, Any]:
    """Allow only the canonical untracked preflight before a future live run."""

    root = research_root.resolve()
    expected = root / PREFLIGHT_PATH
    actual = _path(root, preflight_path)
    _require(actual == expected, "RQ1b preflight path is not canonical")
    validate_power7_rq1b_preflight(preflight, research_root=root)
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _require(
        head == preflight.get("research_commit_sha"),
        "formal tree HEAD differs from preflight producer",
    )
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    allowed = {f"?? {PREFLIGHT_PATH.as_posix()}"}
    _require(set(status) <= allowed, "formal RQ1b tree contains disallowed changes")
    _require(
        status == sorted(allowed), "formal RQ1b tree must contain exactly the canonical preflight"
    )
    _require(not (root / RESULT_PATH).exists(), "RQ1b formal result output already exists")
    return {"status": "ready", "head": head, "allowed_untracked": PREFLIGHT_PATH.as_posix()}


def prepare_evaluation_stage(
    *, design_artifact: Mapping[str, Any], resolver: Callable[[], Mapping[str, Any]]
) -> dict[str, Any]:
    """Validate the seal before invoking any test-side resolver."""

    validate_design_artifact(design_artifact)
    value = dict(resolver())
    _require(value.get("dataset_id") == POWER7, "evaluation dataset must be Power7")
    _require(value.get("evaluation_split") == EVALUATION_SPLIT, "evaluation split must be test")
    _require(
        value.get("strict_unseen_membership_digest") == STRICT_UNSEEN_DIGEST,
        "strict membership binding drift",
    )
    return value


def validate_test_evaluation_records(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    expected = [f"arecel_power7_test_{index:06d}" for index in range(SAMPLE_ROWS)]
    _require(len(records) == SAMPLE_ROWS, "test evaluation must contain exactly 10000 records")
    normalized: list[dict[str, Any]] = []
    for expected_id, record in zip(expected, records, strict=True):
        _require(record.get("query_id") == expected_id, "test evaluation query order drift")
        _require(
            "estimate" in record and "truth" in record and "qerror" in record,
            "incomplete per-query evaluation record",
        )
        _require(
            float(record["qerror"]) == qerror(float(record["estimate"]), float(record["truth"])),
            "per-query q-error is not reproducible",
        )
        normalized.append(dict(record))
    return normalized


def strict_unseen_filter(
    records: Sequence[Mapping[str, Any]], membership: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Filter by the audited membership IDs; never recalculate unseen-ness."""

    validate_test_evaluation_records(records)
    _require(
        membership.get("semantic_digest") == STRICT_UNSEEN_DIGEST,
        "strict membership digest mismatch",
    )
    rows = [row for row in membership.get("datasets", []) if row.get("dataset_id") == POWER7]
    _require(len(rows) == 1, "Power7 strict membership row is missing")
    ids = set(rows[0]["strict_unseen_test_query_ids"])
    _require(
        rows[0]["seen_in_valid_count"] + rows[0]["strict_unseen_count"] == SAMPLE_ROWS,
        "strict membership partition drift",
    )
    return [dict(record) for record in records if record["query_id"] in ids]


def summarize_qerrors(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float(item["qerror"]) for item in records]
    _require(values, "cannot summarize empty q-error records")
    return {
        "query_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


def paired_outcomes(
    reference: Sequence[Mapping[str, Any]], candidate: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    left = {item["query_id"]: float(item["qerror"]) for item in reference}
    right = {item["query_id"]: float(item["qerror"]) for item in candidate}
    _require(list(left) == list(right), "paired evaluation query identity/order drift")
    return {
        "improved": sum(right[key] < left[key] for key in left),
        "unchanged": sum(right[key] == left[key] for key in left),
        "worsened": sum(right[key] > left[key] for key in left),
    }


def build_power7_rq1b_result(
    *,
    design_artifact: Mapping[str, Any],
    evaluation_inputs: Mapping[str, Any],
    advisor_records: Sequence[Mapping[str, Any]],
    baseline_records: Mapping[str, Sequence[Mapping[str, Any]]],
    cleanup_passed: bool,
) -> dict[str, Any]:
    """Derive full-test and strict-unseen results from one evaluation pass.

    The caller supplies records produced by a future stock PostgreSQL
    evaluator.  The strict-unseen arm is always an in-memory filter of those
    same records; this function has no planner or database path of its own.
    """

    validate_design_artifact(design_artifact)
    _require(evaluation_inputs.get("dataset_id") == POWER7, "result dataset must be Power7")
    _require(
        evaluation_inputs.get("evaluation_split") == EVALUATION_SPLIT,
        "result evaluation split must be test",
    )
    _require(
        evaluation_inputs.get("strict_unseen_membership_digest") == STRICT_UNSEEN_DIGEST,
        "result membership binding drift",
    )
    advisor = validate_test_evaluation_records(advisor_records)
    _require(
        set(baseline_records) == {"pg16-default", "pg16-target10000"},
        "both immutable baselines are required",
    )
    for records in baseline_records.values():
        validate_test_evaluation_records(records)
    membership = evaluation_inputs.get("strict_unseen_membership")
    _require(isinstance(membership, Mapping), "strict membership object is required")
    strict = strict_unseen_filter(advisor, membership)
    full_metrics: dict[str, Any] = {}
    strict_metrics: dict[str, Any] = {}
    for arm_id in ("pg16-default", "pg16-target10000"):
        baseline = validate_test_evaluation_records(baseline_records[arm_id])
        full_metrics[arm_id] = {
            "baseline": summarize_qerrors(baseline),
            "advisor": summarize_qerrors(advisor),
            "paired": paired_outcomes(baseline, advisor),
        }
        baseline_strict = [
            item for item in baseline if item["query_id"] in {row["query_id"] for row in strict}
        ]
        strict_metrics[arm_id] = {
            "subset_query_count": len(strict),
            "subset_fraction": len(strict) / SAMPLE_ROWS,
            "baseline": summarize_qerrors(baseline_strict),
            "advisor": summarize_qerrors(strict),
            "paired": paired_outcomes(baseline_strict, strict),
        }
    selected = list(design_artifact["design_stage"]["selected_candidate_ids"])
    test_selected = evaluation_inputs.get("s_test_candidate_ids")
    comparison = {"s_valid_count": len(selected), "s_test_secondary_only": True}
    if isinstance(test_selected, list):
        valid_set = set(selected)
        test_set = set(test_selected)
        comparison.update(
            {
                "s_test_count": len(test_selected),
                "intersection_count": len(valid_set & test_set),
                "union_count": len(valid_set | test_set),
                "jaccard": len(valid_set & test_set) / len(valid_set | test_set)
                if valid_set | test_set
                else None,
            }
        )
    status = "success" if cleanup_passed else "failed-cleanup"
    result = {
        "format_version": RESULT_FORMAT,
        "experiment_id": "rq1-held-out-workload-generalization",
        "rq": "RQ1b",
        "status": status,
        "producer": design_artifact["producer"],
        "protocol": design_artifact["protocol"],
        "truth_policy": design_artifact["truth_policy"],
        "source_audit": evaluation_inputs.get("source_audit"),
        "strict_unseen_membership": {
            "path": STRICT_UNSEEN_PATH.as_posix(),
            "semantic_digest": STRICT_UNSEEN_DIGEST,
        },
        "dataset": design_artifact["dataset"],
        "design_stage": {
            "design_artifact_semantic_digest": design_artifact.get("semantic_digest"),
            "recommendation_digest": design_artifact["design_stage"]["recommendation_digest"],
            "recommendation_sealed": True,
        },
        "evaluation_stage": {
            "evaluation_split": EVALUATION_SPLIT,
            "test_evaluated_once": True,
            "test_query_count": len(advisor),
            "strict_unseen_derived_by_offline_filter": True,
        },
        "baselines": full_metrics,
        "full_test_metrics": full_metrics,
        "strict_unseen_metrics": strict_metrics,
        "recommendation_comparison": comparison,
        "cleanup": {"passed": cleanup_passed},
        "provenance": {
            "no_production_exact": True,
            "no_second_planner_evaluation": True,
            "acceptance_target": "none; metrics are reported regardless of direction",
        },
    }
    result["semantic_digest"] = semantic_digest(result)
    return result


def validate_power7_rq1b_result(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the future compact result without re-running evaluation."""

    _require(isinstance(value, Mapping), "RQ1b result must be an object")
    _require(value.get("format_version") == RESULT_FORMAT, "unsupported RQ1b result format")
    _require(
        value.get("experiment_id") == "rq1-held-out-workload-generalization",
        "RQ1b result identity drift",
    )
    _require(value.get("rq") == "RQ1b", "RQ1b result RQ drift")
    _require(
        value.get("status") in {"success", "failed-cleanup"},
        "invalid RQ1b result status",
    )
    _require(
        value.get("protocol") == _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "result protocol drift",
    )
    _require(
        value.get("truth_policy") == _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "result truth policy drift",
    )
    _require(
        value.get("strict_unseen_membership") == _binding(STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST),
        "result membership drift",
    )
    design = value.get("design_stage")
    _require(isinstance(design, Mapping), "result design stage missing")
    _sha(design.get("design_artifact_semantic_digest"), "result design artifact digest")
    _sha(design.get("recommendation_digest"), "result Recommendation digest")
    _require(design.get("recommendation_sealed") is True, "result Recommendation is not sealed")
    evaluation = value.get("evaluation_stage")
    _require(isinstance(evaluation, Mapping), "result evaluation stage missing")
    _require(
        evaluation.get("evaluation_split") == EVALUATION_SPLIT,
        "result evaluation split drift",
    )
    _require(
        evaluation.get("test_query_count") == SAMPLE_ROWS,
        "result test query count drift",
    )
    _require(
        evaluation.get("test_evaluated_once") is True,
        "result does not prove one test evaluation",
    )
    _require(
        evaluation.get("strict_unseen_derived_by_offline_filter") is True,
        "result strict subset was not offline-derived",
    )
    provenance = value.get("provenance")
    _require(isinstance(provenance, Mapping), "result provenance missing")
    _require(
        provenance.get("no_production_exact") is True, "result production-exact path is forbidden"
    )
    _require(
        provenance.get("no_second_planner_evaluation") is True,
        "result has a second planner path",
    )
    _require(
        value.get("cleanup", {}).get("passed") is (value.get("status") == "success"),
        "result cleanup/status mismatch",
    )
    if "semantic_digest" in value:
        _require(value["semantic_digest"] == _digest_body(value), "RQ1b result digest mismatch")
    return {
        "status": "valid",
        "format_version": RESULT_FORMAT,
        "semantic_digest": value.get("semantic_digest"),
    }


def run_power7_rq1b_evaluation(
    *,
    design_artifact: Mapping[str, Any],
    evaluation_resolver: Callable[[], Mapping[str, Any]],
    evaluator: Callable[[Mapping[str, Any], Mapping[str, Any]], Sequence[Mapping[str, Any]]],
    baseline_records: Mapping[str, Sequence[Mapping[str, Any]]],
    cleanup_passed: bool,
) -> dict[str, Any]:
    """Evaluate a sealed design exactly once through an injected stock runner."""

    inputs = prepare_evaluation_stage(design_artifact=design_artifact, resolver=evaluation_resolver)
    advisor_records = evaluator(inputs, design_artifact)
    return build_power7_rq1b_result(
        design_artifact=design_artifact,
        evaluation_inputs=inputs,
        advisor_records=advisor_records,
        baseline_records=baseline_records,
        cleanup_passed=cleanup_passed,
    )


def publish_result(*, result: Mapping[str, Any], output: Path) -> dict[str, Any]:
    """Fail closed: only a cleanup-passed success may be published."""

    _require(result.get("status") == "success", "only successful RQ1b results may be published")
    _require(result.get("cleanup", {}).get("passed") is True, "RQ1b cleanup has not passed")
    destination = Path(output)
    _require(not destination.exists(), "RQ1b result output collision")
    payload = dict(result)
    payload.setdefault("format_version", RESULT_FORMAT)
    payload["semantic_digest"] = semantic_digest(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "DESIGN_FORMAT",
    "EVALUATION_SPLIT",
    "K_S",
    "POWER7",
    "PREFLIGHT_PATH",
    "PROTOCOL_DIGEST",
    "RESULT_FORMAT",
    "SAMPLE_ROWS",
    "B",
    "RQ1BValidationError",
    "build_design_artifact",
    "build_power7_rq1b_preflight",
    "build_power7_rq1b_result",
    "paired_outcomes",
    "prepare_evaluation_stage",
    "publish_result",
    "run_power7_rq1b_design",
    "run_power7_rq1b_evaluation",
    "strict_unseen_filter",
    "summarize_qerrors",
    "validate_design_artifact",
    "validate_power7_rq1b_preflight",
    "validate_power7_rq1b_result",
    "validate_test_evaluation_records",
    "verify_power7_formal_tree",
]
