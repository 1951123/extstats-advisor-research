"""Power7 RQ1b design/evaluation harness and explicit live adapters.

The injected APIs remain offline readiness seams.  The live adapters are
separate explicit entry points used only by the future formal command, so
ordinary validation and test collection never start PostgreSQL or Advisor.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .arecel_truth import authoritative_truth_spec_for_split, validate_observation_wire
from .datasets import DATASETS
from .paper_baseline import percentile, qerror
from .pins import verify_git_sha
from .provenance import read_json, semantic_digest, sha256_file, write_json
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
    verify_frozen_systems_v2,
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
VALID_WORKLOAD_ID = "arecel_power7_valid_v1"
VALID_WORKLOAD_SHA256 = "6a4658fee7d4f022a7886700c780a49dd3c2f81f4c6e2502163cabd19096113c"
VALID_OBSERVATIONS_PATH = Path(
    "truth/arecel/power7/authoritative-cardinality-observations-valid-v1.json"
)
VALID_AUDIT_PATH = Path("truth/arecel/power7/audit-valid-v1.json")
VALID_OBSERVATIONS_SHA256 = "2cf8e81f4ce7143c7085780deab1b6547e518ce7b34b2cfbfb69ebcc882e461c"
VALID_DATASET_CONTENT_IDENTITY = "a432183ed36b42032864fc8eb0b99f554553087eb54c60dd08179eaf6c68dab4"
ARECEL_UPSTREAM_COMMIT = "aa52da7768023270bad884232972e0b77ec6534a"
VALID_CANONICAL_WORKLOAD_SHA256 = "9f1b11d72bcefe9d76cd83b1f995a6d6f4e0922434975550e2d5783425152327"
SYSTEM_FREEZE_PATH = Path("paper/system-freeze-v2.json")
DESIGN_FORMAT = "rq1-workload-generalization-power7-design-v1"
RESULT_FORMAT = "rq1-workload-generalization-power7-v1"
PREFLIGHT_FORMAT = "rq1-workload-generalization-power7-preflight-v1"
CAMPAIGN_ATTEMPT_INDEX = 5
PRIOR_FAILED_ATTEMPT_PATH = Path(
    "experiments/arecel-power7/rq1-workload-generalization-v1/"
    "failed-attempts/attempt-004/failure-v1.json"
)
PRIOR_FAILED_ATTEMPT_DIGEST = "d809f9e15f4eb2bb33aa8a421b8e8c2ff690ad372eb245b0b555495d30ac638d"
PRIOR_FAILED_ATTEMPT_STATUS = "non-evidence-pre-execution-environment-failure"
PRIOR_FAILED_ATTEMPT_CLASS = "frozen-advisor-runtime-dependency-missing"
DESIGN_PATH = Path("experiments/arecel-power7/rq1-workload-generalization-v1/design-v1.json")
PER_QUERY_PATH = Path(
    "experiments/arecel-power7/rq1-workload-generalization-v1/"
    "pg16-advisor-valid-to-test-per-query-v1.jsonl"
)
DEPLOYMENT_PATH = Path(
    "experiments/arecel-power7/rq1-workload-generalization-v1/deployment-result-v1.json"
)
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
    """Resolve only the valid truth files needed by the design stage.

    The general split-aware truth-policy resolver is deliberately not used
    here: that policy also contains test-side metadata.  Reading the compact
    valid wire and its valid-only audit keeps the pre-seal path literal.
    """

    truth_path = root / VALID_OBSERVATIONS_PATH
    audit_path = root / VALID_AUDIT_PATH
    _require(truth_path.is_file(), f"missing valid Power7 truth wire: {VALID_OBSERVATIONS_PATH}")
    _require(audit_path.is_file(), f"missing valid Power7 truth audit: {VALID_AUDIT_PATH}")
    try:
        wire = read_json(truth_path)
        validate_observation_wire(wire, workload_id=VALID_WORKLOAD_ID)
        audit = read_json(audit_path)
    except (KeyError, TypeError, ValueError) as error:
        raise RQ1BValidationError(f"Power7 valid truth is malformed: {error}") from error
    _require(isinstance(audit, Mapping), "Power7 valid truth audit must be an object")
    _require(audit.get("source_split") == DESIGN_SPLIT, "Power7 valid truth audit split drift")
    _require(audit.get("benchmark_id") == POWER7, "Power7 valid truth benchmark drift")
    _require(audit.get("workload_id") == VALID_WORKLOAD_ID, "Power7 valid truth ID drift")
    _require(audit.get("query_count") == SAMPLE_ROWS, "Power7 valid truth count is not 10000")
    _require(
        audit.get("dataset_content_identity") == VALID_DATASET_CONTENT_IDENTITY,
        "Power7 valid truth dataset identity drift",
    )
    _require(
        audit.get("upstream_commit") == ARECEL_UPSTREAM_COMMIT,
        "Power7 valid truth upstream commit drift",
    )
    _require(
        audit.get("authority") == "sfu-db/AreCELearnedYet",
        "Power7 valid truth authority drift",
    )
    _require(
        audit.get("canonical_workload_sha256") == VALID_CANONICAL_WORKLOAD_SHA256,
        "Power7 valid truth canonical workload drift",
    )
    _require(
        audit.get("observation_sha256") == VALID_OBSERVATIONS_SHA256
        and sha256_file(truth_path) == VALID_OBSERVATIONS_SHA256,
        "Power7 valid truth observation digest drift",
    )
    return {
        "source_kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        "dataset_identity": VALID_DATASET_CONTENT_IDENTITY,
        "source_revision": ARECEL_UPSTREAM_COMMIT,
        "observations_sha256": VALID_OBSERVATIONS_SHA256,
        "observations_semantic_digest": semantic_digest(
            {key: item for key, item in wire.items() if key != "semantic_digest"}
        ),
        "workload_id": VALID_WORKLOAD_ID,
        "query_count": SAMPLE_ROWS,
        "policy_status": "preregistered",
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
    _require(identity["workload_id"] == VALID_WORKLOAD_ID, "Power7 valid workload ID drift")
    _require(identity["query_count"] == SAMPLE_ROWS, "Power7 valid workload count is not 10000")
    _require(identity["sha256"] == VALID_WORKLOAD_SHA256, "Power7 valid workload SHA drift")
    _require(
        metadata["dataset_content_identity"] == VALID_DATASET_CONTENT_IDENTITY,
        "Power7 dataset content identity drift",
    )
    _require(metadata["relation"] == "public.power7", "Power7 relation identity drift")
    _require(
        metadata["schema_contract"]["id"] == "arecel-power7-postgres-schema-v1",
        "Power7 schema contract drift",
    )
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
            "canonical_source_sha256": VALID_CANONICAL_WORKLOAD_SHA256,
        },
        "truth": truth,
        "workload_queries": workload["queries"],
    }


def _valid_truth_spec_design_safe(root: Path) -> dict[str, Any]:
    """Build the frozen Advisor truth-spec shape from valid-only files."""

    identity = _valid_truth_identity(root)
    return {
        "kind": identity["source_kind"],
        "collection_contract": identity["collection_contract"],
        "authority": "sfu-db/AreCELearnedYet",
        "dataset_identity": identity["dataset_identity"],
        "source_revision": identity["source_revision"],
        "observations_path": root / VALID_OBSERVATIONS_PATH,
        "observations_sha256": identity["observations_sha256"],
        "query_count": identity["query_count"],
        "sanity_check_count": 10,
        "policy_status": identity["policy_status"],
        "source_split": DESIGN_SPLIT,
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


def _design_source_spec(root: Path, data_root: Path | None = None) -> dict[str, Any]:
    """Return the compact Power7 valid-only source contract.

    This helper is intentionally independent of the mixed valid/test source
    audit and the RQ1a baseline.  It may run before the Recommendation seal.
    """

    inputs = _valid_design_inputs(root, data_root)
    dataset = inputs["dataset"]
    workload = inputs["workload"]
    truth = inputs["truth"]
    return {
        "dataset_id": POWER7,
        "benchmark_id": dataset["benchmark_id"],
        "dataset_content_identity": dataset["content_identity"],
        "relation": dataset["relation"],
        "schema_contract_id": dataset["schema_contract_id"],
        "rows": dataset["rows"],
        "design_workload": {
            "source_split": DESIGN_SPLIT,
            "workload_id": workload["workload_id"],
            "sha256": workload["sha256"],
            "query_count": workload["query_count"],
            "canonical_source_sha256": workload["canonical_source_sha256"],
        },
        "design_truth": {
            "source_split": DESIGN_SPLIT,
            "path": VALID_OBSERVATIONS_PATH.as_posix(),
            "observations_sha256": truth["observations_sha256"],
            "workload_id": truth["workload_id"],
            "query_count": truth["query_count"],
            "dataset_identity": truth["dataset_identity"],
        },
    }


def _opaque_evaluation_bindings() -> dict[str, dict[str, str]]:
    """Return evaluation references without opening evaluation artifacts."""

    return {
        "source_audit": _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "truth_policy": _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "strict_unseen": _binding(STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST),
        "rq1a_baseline": _binding(BASELINE_PATH, RQ1A_POWER7_DIGEST),
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
    selected = execution["selected_candidate_ids"]
    deployment = execution["deployment_ordered_candidate_ids"]
    _require(
        all(isinstance(item, str) and item for item in selected)
        and all(isinstance(item, str) and item for item in deployment),
        "Recommendation candidate IDs must be non-empty strings",
    )
    _require(len(selected) == len(set(selected)), "selected candidate IDs contain duplicates")
    _require(
        len(deployment) == len(set(deployment)),
        "deployment candidate IDs contain duplicates",
    )
    _require(
        set(selected) == set(deployment),
        "deployment order must preserve Recommendation membership",
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
        "source_audit": _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
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
    _require(
        value.get("source_audit") == _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "design source audit binding drift",
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
    _require(
        isinstance(dataset.get("content_identity"), str) and dataset["content_identity"],
        "design dataset content identity is missing",
    )
    _require(
        isinstance(dataset.get("relation"), str) and dataset["relation"],
        "design relation identity is missing",
    )
    stage = value.get("design_stage")
    _require(
        isinstance(stage, Mapping) and stage.get("input_split") == DESIGN_SPLIT,
        "design stage split drift",
    )
    _require(stage.get("design_stage_complete") is True, "design stage completion missing")
    _require(stage.get("recommendation_sealed") is True, "Recommendation seal missing")
    _require_execution_digests(stage)
    workload = stage.get("workload")
    truth = stage.get("truth")
    _require(isinstance(workload, Mapping), "design workload binding is missing")
    _require(isinstance(truth, Mapping), "design truth binding is missing")
    _require(
        workload.get("workload_id") == "arecel_power7_valid_v1", "design workload is not valid"
    )
    _require(workload.get("query_count") == SAMPLE_ROWS, "design valid workload count drift")
    _sha(workload.get("sha256"), "design valid workload SHA")
    _sha(
        workload.get("canonical_source_sha256"),
        "design canonical workload SHA",
    )
    _require(truth.get("workload_id") == "arecel_power7_valid_v1", "design truth is not valid")
    _require(truth.get("query_count") == SAMPLE_ROWS, "design valid truth count drift")
    _sha(truth.get("observations_sha256"), "design valid truth SHA")
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
    selected = stage.get("selected_candidate_ids")
    deployment = stage.get("deployment_ordered_candidate_ids")
    _require(isinstance(selected, list) and isinstance(deployment, list), "sealed lists missing")
    _require(len(selected) == len(set(selected)), "sealed membership contains duplicates")
    _require(len(deployment) == len(set(deployment)), "sealed deployment order contains duplicates")
    _require(set(selected) == set(deployment), "sealed membership/order set mismatch")
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
    advisor_arm = artifact["per_arm"]["pg16-advisor"]
    _require(
        advisor_arm.get("evaluation_mode") == "stock-full-data-deployment",
        "baseline advisor arm is not stock deployment evidence",
    )
    recommendation = advisor_arm.get("recommendation")
    _require(isinstance(recommendation, Mapping), "baseline S_test recommendation is missing")
    test_selected = recommendation.get("selected_candidate_ids")
    test_deployment = recommendation.get("deployment_ordered_candidate_ids")
    _require(isinstance(test_selected, list), "baseline S_test membership is missing")
    _require(isinstance(test_deployment, list), "baseline S_test deployment order is missing")
    _require(
        len(test_selected) == len(set(test_selected))
        and len(test_deployment) == len(set(test_deployment))
        and set(test_selected) == set(test_deployment),
        "baseline S_test Recommendation membership/order contract mismatch",
    )
    result["s_test_candidate_ids"] = list(test_selected)
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
    design_source = _design_source_spec(root)
    body: dict[str, Any] = {
        "format_version": PREFLIGHT_FORMAT,
        "campaign_attempt_index": CAMPAIGN_ATTEMPT_INDEX,
        "prior_failed_attempt": {
            **_binding(PRIOR_FAILED_ATTEMPT_PATH, PRIOR_FAILED_ATTEMPT_DIGEST),
            "status": PRIOR_FAILED_ATTEMPT_STATUS,
            "failure_class": PRIOR_FAILED_ATTEMPT_CLASS,
            "evidence_eligible": False,
        },
        "status": "ready-to-run",
        "formal_execution": "not-started",
        "research_commit_sha": producer_sha,
        "protocol": _binding(PROTOCOL_V2_PATH, PROTOCOL_DIGEST),
        "truth_policy": _binding(TRUTH_POLICY_PATH, TRUTH_POLICY_DIGEST),
        "source_audit": _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "strict_unseen_membership": _binding(STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST),
        "system_freeze": _system_binding(root),
        "dataset": design_source,
        "evaluation_bindings": _opaque_evaluation_bindings(),
        "advisor_execution_policy": {
            "mode": "frozen-checkout-runtime-launcher",
            "source_checkout_sha": FROZEN_ADVISOR_SHA,
            "launcher_source": "verified advisor_root/src",
            "path_lookup_allowed": False,
        },
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
        "preseal_evaluation_content_unread": True,
    }
    body["semantic_digest"] = semantic_digest(body)
    return body


def validate_power7_rq1b_preflight(
    value: Mapping[str, Any], *, research_root: Path
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RQ1BValidationError("RQ1b preflight must be an object")
    _require(value.get("format_version") == PREFLIGHT_FORMAT, "unsupported RQ1b preflight")
    _require(
        value.get("campaign_attempt_index") == CAMPAIGN_ATTEMPT_INDEX,
        "RQ1b campaign attempt index drift",
    )
    _require(
        value.get("prior_failed_attempt")
        == {
            **_binding(PRIOR_FAILED_ATTEMPT_PATH, PRIOR_FAILED_ATTEMPT_DIGEST),
            "status": PRIOR_FAILED_ATTEMPT_STATUS,
            "failure_class": PRIOR_FAILED_ATTEMPT_CLASS,
            "evidence_eligible": False,
        },
        "RQ1b prior failed-attempt binding drift",
    )
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
        value.get("dataset") == _design_source_spec(research_root.resolve()),
        "preflight Power7 design source spec drift",
    )
    _require(
        value.get("evaluation_bindings") == _opaque_evaluation_bindings(),
        "preflight evaluation binding drift",
    )
    _require(
        value.get("advisor_execution_policy")
        == {
            "mode": "frozen-checkout-runtime-launcher",
            "source_checkout_sha": FROZEN_ADVISOR_SHA,
            "launcher_source": "verified advisor_root/src",
            "path_lookup_allowed": False,
        },
        "preflight Advisor execution policy drift",
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
    _require(
        value.get("preseal_evaluation_content_unread") is True,
        "preflight pre-seal evaluation access marker drift",
    )
    serialized = json.dumps(value, sort_keys=True)
    for forbidden in (
        "arecel_power7_test_",
        "s_test_candidate_ids",
        "strict_unseen_test_query_ids",
        "seen_in_valid_count",
        "strict_unseen_count",
        "pg16-default-per-query",
        "pg16-target10000-per-query",
    ):
        _require(
            forbidden not in serialized,
            f"preflight contains evaluation-only content: {forbidden}",
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
    for relative in (DESIGN_PATH, DEPLOYMENT_PATH, PER_QUERY_PATH, RESULT_PATH):
        _require(not (root / relative).exists(), f"RQ1b formal output already exists: {relative}")
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
    _require(
        evaluation_inputs.get("source_audit")
        == _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "result source audit binding drift",
    )
    for field in ("test_workload", "test_truth", "stock_postgresql", "advisor", "deployment"):
        _require(
            isinstance(evaluation_inputs.get(field), Mapping), f"result {field} binding missing"
        )
    per_query_artifact = evaluation_inputs.get("per_query_artifact")
    _require(isinstance(per_query_artifact, Mapping), "result per-query artifact binding missing")
    _require(per_query_artifact.get("query_count") == SAMPLE_ROWS, "result per-query count drift")
    _sha(per_query_artifact.get("sha256"), "result per-query artifact SHA")
    _sha(
        evaluation_inputs["deployment"].get("semantic_digest"),
        "result deployment artifact digest",
    )
    _require(
        evaluation_inputs["test_workload"].get("workload_id") == "arecel_power7_test_v1"
        and evaluation_inputs["test_workload"].get("query_count") == SAMPLE_ROWS,
        "result test workload identity drift",
    )
    _sha(evaluation_inputs["test_workload"].get("sha256"), "result test workload SHA")
    _require(
        evaluation_inputs["test_truth"].get("workload_id") == "arecel_power7_test_v1"
        and evaluation_inputs["test_truth"].get("query_count") == SAMPLE_ROWS,
        "result test truth identity drift",
    )
    _sha(evaluation_inputs["test_truth"].get("observations_sha256"), "result test truth SHA")
    _require(
        evaluation_inputs["advisor"].get("source_commit_sha") == FROZEN_ADVISOR_SHA
        and evaluation_inputs["advisor"].get("source_checkout_verified") is True,
        "result Advisor source binding drift",
    )
    _require(
        evaluation_inputs["stock_postgresql"].get("source_commit_sha") == FROZEN_STOCK_POSTGRES_SHA
        and evaluation_inputs["stock_postgresql"].get("postgres_version") == "16.14",
        "result stock PostgreSQL binding drift",
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
            "test_workload": dict(evaluation_inputs["test_workload"]),
            "test_truth": dict(evaluation_inputs["test_truth"]),
            "stock_postgresql": dict(evaluation_inputs["stock_postgresql"]),
            "advisor": dict(evaluation_inputs["advisor"]),
            "deployment": dict(evaluation_inputs["deployment"]),
            "per_query_artifact": dict(per_query_artifact),
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
    _require(
        value.get("source_audit") == _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "result source audit binding drift",
    )
    for field in ("test_workload", "test_truth", "stock_postgresql", "advisor", "deployment"):
        _require(isinstance(evaluation.get(field), Mapping), f"result {field} binding missing")
    per_query_artifact = evaluation.get("per_query_artifact")
    _require(isinstance(per_query_artifact, Mapping), "result per-query artifact binding missing")
    _require(per_query_artifact.get("query_count") == SAMPLE_ROWS, "result per-query count drift")
    _sha(per_query_artifact.get("sha256"), "result per-query artifact SHA")
    _sha(evaluation["deployment"].get("semantic_digest"), "result deployment artifact digest")
    _require(
        evaluation["test_workload"].get("workload_id") == "arecel_power7_test_v1"
        and evaluation["test_workload"].get("query_count") == SAMPLE_ROWS,
        "result test workload identity drift",
    )
    _sha(evaluation["test_workload"].get("sha256"), "result test workload SHA")
    _require(
        evaluation["test_truth"].get("workload_id") == "arecel_power7_test_v1"
        and evaluation["test_truth"].get("query_count") == SAMPLE_ROWS,
        "result test truth identity drift",
    )
    _sha(evaluation["test_truth"].get("observations_sha256"), "result test truth SHA")
    _require(
        evaluation["advisor"].get("source_commit_sha") == FROZEN_ADVISOR_SHA
        and evaluation["advisor"].get("source_checkout_verified") is True,
        "result Advisor source binding drift",
    )
    _require(
        evaluation["stock_postgresql"].get("source_commit_sha") == FROZEN_STOCK_POSTGRES_SHA
        and evaluation["stock_postgresql"].get("postgres_version") == "16.14",
        "result stock PostgreSQL binding drift",
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


def write_power7_rq1b_preflight(
    *, research_root: Path, output: Path | None = None
) -> dict[str, Any]:
    """Create the canonical preflight without starting any live system."""

    root = research_root.resolve()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    value = build_power7_rq1b_preflight(
        research_root=root,
        producer_sha=head,
        output=output or root / PREFLIGHT_PATH,
    )
    validate_power7_rq1b_preflight(value, research_root=root)
    destination = _path(root, output or PREFLIGHT_PATH)
    write_json(destination, value)
    return value


def _run_live_command(command: Sequence[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode:
        raise RQ1BValidationError(
            f"frozen Advisor command failed ({completed.returncode}): {command[0]}"
        )


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                records.append(json.loads(line))
    return records


def _write_jsonl(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(dict(record), sort_keys=True) + "\n")


def _execution_from_live_canonical(value: Mapping[str, Any]) -> dict[str, Any]:
    execution = dict(value.get("execution", {}))
    execution.pop("runtime_artifact_directory", None)
    _require(value.get("status") == "complete", "valid-only Advisor design did not complete")
    _require(execution.get("design_stage_complete") is True, "live design did not complete")
    _require(execution.get("recommendation_sealed") is True, "live Recommendation was not sealed")
    return execution


def prepare_frozen_advisor_launcher(*, advisor_root: Path, runtime_root: Path) -> dict[str, Any]:
    """Create and probe a runtime-local launcher for the frozen Advisor checkout.

    The formal RQ1b path must not resolve ``extstats-advisor`` through PATH:
    that name is a packaging convenience, not the scientific source identity.
    This launcher runs the current research interpreter while putting the
    verified frozen checkout's ``src`` directory first on ``sys.path``.
    """

    checkout = advisor_root.resolve()
    _require(
        verify_git_sha(checkout, FROZEN_ADVISOR_SHA) == FROZEN_ADVISOR_SHA,
        "frozen Advisor checkout SHA drift",
    )
    source_root = checkout / "src"
    package_root = source_root / "extstats_advisor"
    _require(source_root.is_dir(), "frozen Advisor source root is missing")
    _require((package_root / "cli.py").is_file(), "frozen Advisor CLI source is missing")

    runtime_input = Path(runtime_root)
    _require(not runtime_input.is_symlink(), "RQ1b runtime root must not be a symlink")
    runtime = runtime_input.resolve()
    _require(runtime != Path("/"), "RQ1b runtime root is unsafe")
    runtime.mkdir(parents=True, exist_ok=True)
    _require(runtime.is_dir() and not runtime.is_symlink(), "RQ1b runtime root is not a directory")

    launcher = runtime / "frozen-extstats-advisor"
    _require(not launcher.exists(), "frozen Advisor launcher output already exists")
    _require(not launcher.is_symlink(), "frozen Advisor launcher must not be a symlink")

    interpreter = Path(sys.executable).resolve()
    _require(
        interpreter.is_absolute() and interpreter.is_file(), "research Python executable is invalid"
    )
    source_text = (
        f"#!{interpreter}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(source_root)!r})\n"
        "from extstats_advisor.cli import main\n"
        "raise SystemExit(main())\n"
    )
    launcher.write_text(source_text, encoding="utf-8")
    launcher.chmod(0o700)
    _require(launcher.is_file() and not launcher.is_symlink(), "frozen Advisor launcher is invalid")

    import_probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import extstats_advisor; print(extstats_advisor.__file__)"
            ),
            str(source_root),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    _require(
        import_probe.returncode == 0,
        f"frozen Advisor import probe failed: {import_probe.stderr.strip()}",
    )
    imported_text = next(
        (line.strip() for line in reversed(import_probe.stdout.splitlines()) if line.strip()),
        "",
    )
    _require(imported_text, "frozen Advisor import probe returned no module path")
    imported = Path(imported_text).resolve()
    try:
        imported.relative_to(package_root.resolve())
    except ValueError as error:
        raise RQ1BValidationError(
            f"Advisor import resolved outside frozen checkout: {imported}"
        ) from error

    version_probe = subprocess.run(
        [str(launcher), "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    _require(
        version_probe.returncode == 0,
        f"frozen Advisor --version probe failed: {version_probe.stderr.strip()}",
    )
    return {
        "launcher_path": str(launcher),
        "python_executable": str(interpreter),
        "advisor_source_root": str(source_root),
        "imported_module": str(imported),
        "probe_stdout": version_probe.stdout.strip(),
        "probe_stderr": version_probe.stderr.strip(),
    }


def _managed_dsn_endpoint(dsn: str, *, role: str) -> dict[str, str]:
    """Return the endpoint fields that bind a DSN to one managed lab role."""

    try:
        from psycopg.conninfo import conninfo_to_dict

        fields = conninfo_to_dict(dsn)
    except Exception as exc:  # convert parser failures to controlled validation
        raise RQ1BValidationError(f"invalid {role} managed-lab DSN") from exc
    endpoint = {
        "host": str(fields.get("host", "")),
        "port": str(fields.get("port", "")),
        "dbname": str(fields.get("dbname", "")),
    }
    _require(all(endpoint.values()), f"{role} DSN must specify managed host, port, and database")
    return endpoint


def _validate_managed_lab_dsns(*, stock_dsn: str, planner_dsn: str) -> dict[str, Any]:
    """Reject DSNs that do not address the repository-owned lab roles exactly."""

    from .postgres_lab import role_spec

    expected: dict[str, dict[str, str]] = {}
    actual = {
        "stock": _managed_dsn_endpoint(stock_dsn, role="stock"),
        "patched": _managed_dsn_endpoint(planner_dsn, role="patched"),
    }
    for role in ("stock", "patched"):
        spec = role_spec(role)
        expected[role] = {
            "host": str(spec.socket),
            "port": str(spec.port),
            "dbname": spec.database,
        }
        _require(
            actual[role] == expected[role],
            f"{role} DSN does not match the managed PostgreSQL lab role",
        )
    return {"expected": expected, "actual": actual}


def _verify_managed_lab_status(role: str, status: Mapping[str, Any], expected_sha: str) -> None:
    from .postgres_lab import role_spec

    spec = role_spec(role)
    _require(status.get("running") is True, f"managed {role} PostgreSQL role is not running")
    _require(status.get("socket_exists") is True, f"managed {role} PostgreSQL socket is absent")
    _require(status.get("socket") == str(spec.socket), f"managed {role} socket drift")
    _require(status.get("port") == spec.port, f"managed {role} port drift")
    _require(status.get("database") == spec.database, f"managed {role} database drift")
    identity = status.get("identity")
    _require(isinstance(identity, Mapping), f"managed {role} identity is missing")
    _require(
        identity.get("source_commit_sha") == expected_sha,
        f"managed {role} source SHA drift",
    )
    version = str(status.get("server_version", ""))
    _require("PostgreSQL 16.14" in version, f"managed {role} PostgreSQL version drift")


def prepare_power7_rq1b_formal_labs(
    *,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
) -> dict[str, Any]:
    """Reinitialize and verify both managed lab roles before design starts.

    This orchestration-layer lifecycle is deliberately separate from the
    historical canonical runner.  Each role is reinitialized exactly once;
    failures are propagated to the caller for the outer cleanup path.
    """

    pins = verify_frozen_systems_v2(
        advisor_root.resolve(), patched_postgres_root.resolve(), stock_postgres_root.resolve()
    )
    _require(pins["advisor_commit_sha"] == FROZEN_ADVISOR_SHA, "live Advisor SHA drift")
    _require(
        pins["patched_postgres_commit_sha"] == FROZEN_PATCHED_POSTGRES_SHA,
        "live patched PostgreSQL SHA drift",
    )
    _require(
        pins["stock_postgres_commit_sha"] == FROZEN_STOCK_POSTGRES_SHA,
        "live stock PostgreSQL SHA drift",
    )

    from .postgres_lab import doctor_role, reinit_role, status_role

    reinit_role("stock")
    stock_status = status_role("stock")
    _verify_managed_lab_status("stock", stock_status, FROZEN_STOCK_POSTGRES_SHA)

    reinit_role("patched")
    patched_status = status_role("patched")
    _verify_managed_lab_status("patched", patched_status, FROZEN_PATCHED_POSTGRES_SHA)

    # The doctor reuses the repository's existing patched-backend capability
    # probe rather than inventing a socket-only substitute.
    diagnoses = {role: doctor_role(role) for role in ("stock", "patched")}
    for role, diagnosis in diagnoses.items():
        _require(diagnosis.get("ok") is True, f"managed {role} PostgreSQL doctor failed")
    patched_capability = diagnoses["patched"].get("patched_backend", {})
    _require(patched_capability.get("ok") is True, "patched PostgreSQL capability probe failed")
    _require(
        patched_capability.get("reference_source_commit") == FROZEN_PATCHED_POSTGRES_SHA,
        "patched PostgreSQL capability source SHA drift",
    )
    return {
        "status": "ready",
        "stock": stock_status,
        "patched": patched_status,
        "patched_capability": patched_capability,
    }


def execute_power7_rq1b_design_live(
    *,
    research_root: Path,
    producer_sha: str,
    production_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    output_root: Path,
    design_output: Path | None = None,
    data_root: Path | None = None,
    reset_disposable: bool = True,
    advisor_command: str = "extstats-advisor",
    seed_identifier: int = 123,
) -> dict[str, Any]:
    """Execute the frozen Advisor pipeline on Power7's valid split only.

    This is the real future live seam.  It is intentionally never invoked by
    offline validation commands; tests inject no PostgreSQL and no Advisor.
    """

    root = research_root.resolve()
    _commit(producer_sha, "RQ1b design producer SHA")
    _require(production_dsn and planner_dsn, "RQ1b design requires production and planner DSNs")
    design_destination = _path(root, design_output or DESIGN_PATH)
    _require(not design_destination.exists(), "RQ1b design output already exists")
    pins = verify_frozen_systems_v2(
        advisor_root.resolve(), patched_postgres_root.resolve(), stock_postgres_root.resolve()
    )
    _require(pins["advisor_commit_sha"] == FROZEN_ADVISOR_SHA, "live Advisor SHA drift")
    _require(
        pins["patched_postgres_commit_sha"] == FROZEN_PATCHED_POSTGRES_SHA,
        "live patched PostgreSQL SHA drift",
    )
    _require(
        pins["stock_postgres_commit_sha"] == FROZEN_STOCK_POSTGRES_SHA,
        "live stock PostgreSQL SHA drift",
    )

    from .canonical_runner import _run_canonical
    from .datasets import power7
    from .postgres.loader import load_power7

    truth = _valid_truth_spec_design_safe(root)
    result = _run_canonical(
        dataset=power7,
        loader=load_power7,
        full_format_version="rq1b-power7-valid-full-data-target100-v1",
        compact_format_version="rq1b-power7-valid-design-v1",
        production_dsn=production_dsn,
        planner_dsn=planner_dsn,
        advisor_root=advisor_root.resolve(),
        patched_postgres_root=patched_postgres_root.resolve(),
        stock_postgres_root=stock_postgres_root.resolve(),
        output_root=output_root.resolve(),
        sample_rows=SAMPLE_ROWS,
        sample_seed=SAMPLE_SEED,
        statistics_target=STATISTICS_TARGET,
        candidate_limit=K_S,
        search_wall_clock_seconds=SEARCH_BUDGET_SECONDS,
        data_root=data_root,
        reset_disposable=reset_disposable,
        advisor_command=advisor_command,
        authoritative_truth=truth,
        seed_identifier=seed_identifier,
        system_freeze_v2=True,
        workload_split=DESIGN_SPLIT,
        records_loader=power7.load_records,
        run_truth_sanity_check=False,
        historical_evidence=False,
        research_identity_override={
            "research_repository": "1951123/extstats-advisor-research",
            "research_commit_sha": producer_sha,
        },
    )
    execution = _execution_from_live_canonical(result)
    artifact = build_design_artifact(
        research_root=root,
        producer_sha=producer_sha,
        execution=execution,
        data_root=data_root,
    )
    write_json(design_destination, artifact)
    validate_design_artifact(read_json(design_destination), expected_producer=producer_sha)
    return {
        "status": "complete",
        "design_artifact": artifact,
        "design_path": design_destination,
        "runtime_directory": Path(result["run_directory"]),
    }


def _evaluate_power7_once(
    *,
    stock_dsn: str,
    workload: Sequence[Mapping[str, Any]],
    truths: Mapping[str, int],
    output: Path,
) -> list[dict[str, Any]]:
    import psycopg

    started = time.monotonic()
    records: list[dict[str, Any]] = []
    with psycopg.connect(
        stock_dsn,
        application_name="extstats-research-rq1b-power7",
        autocommit=True,
    ) as connection:
        for query in workload:
            plan = connection.execute(f"EXPLAIN (FORMAT JSON) {query['sql']}").fetchone()[0]
            if isinstance(plan, str):
                plan = json.loads(plan)
            estimate = int(plan[0]["Plan"]["Plan Rows"])
            query_id = str(query["query_id"])
            truth = int(truths[query_id])
            records.append(
                {
                    "query_id": query_id,
                    "estimate": estimate,
                    "truth": truth,
                    "weight": float(query.get("weight", 1.0)),
                    "qerror": qerror(estimate, truth),
                }
            )
    _require(len(records) == SAMPLE_ROWS, "Power7 Advisor evaluation did not produce 10000 rows")
    _write_jsonl(output, records)
    output_capture_seconds = time.monotonic() - started
    _require(output_capture_seconds >= 0, "invalid Power7 evaluation timer")
    return records


def _stop_formal_roles() -> bool:
    from .postgres_lab import stop_role

    try:
        stop_role("stock")
        stop_role("patched")
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _cleanup_live_runtime(root: Path, target: Path | None, cleanup_result: bool) -> bool:
    if target is None:
        return cleanup_result
    runtime_target = target.resolve()
    if runtime_target.is_symlink() or runtime_target == root:
        return False
    if runtime_target.exists():
        try:
            shutil.rmtree(runtime_target)
        except OSError:
            return False
    return cleanup_result


def resolve_power7_rq1b_evaluation_inputs_after_seal(
    *,
    research_root: Path,
    producer_sha: str,
    design_artifact: Mapping[str, Any],
    design_runtime_directory: Path,
    data_root: Path | None = None,
) -> dict[str, Any]:
    """Resolve test-side inputs only after re-validating a persisted seal."""

    # This must remain the first operation: all evaluation content is behind
    # the canonical design-artifact boundary.
    validate_design_artifact(design_artifact, expected_producer=producer_sha)
    root = research_root.resolve()
    from .datasets import power7

    baseline = _baseline_binding(root)
    source = _source_spec(root)
    test_spec = authoritative_truth_spec_for_split(POWER7, EVALUATION_SPLIT, root)
    test_workload_path = design_runtime_directory.resolve() / "rq1b-test-workload.json"
    identity = power7.extract_workload(test_workload_path, data_root, split=EVALUATION_SPLIT)
    _require(identity["workload_id"] == source["test_workload_id"], "test workload ID drift")
    _require(identity["sha256"] == source["test_workload_sha256"], "test workload hash drift")
    workload = read_json(test_workload_path)
    truth_wire = read_json(Path(test_spec["observations_path"]))
    _require(
        truth_wire.get("workload_id") == source["test_workload_id"], "test truth workload drift"
    )
    _require(
        test_spec["observations_sha256"] == source["test_observations_sha256"],
        "test truth SHA drift",
    )
    truths = {row["query_id"]: int(row["cardinality"]) for row in truth_wire["truths"]}
    membership = _load_exact(root, STRICT_UNSEEN_PATH, STRICT_UNSEEN_DIGEST)
    baseline_records = {
        arm_id: _load_jsonl(root / binding["per_query_path"])
        for arm_id, binding in baseline["arms"].items()
    }
    return {
        "baseline": baseline,
        "source": source,
        "membership": membership,
        "baseline_records": baseline_records,
        "dataset_id": POWER7,
        "evaluation_split": EVALUATION_SPLIT,
        "strict_unseen_membership_digest": STRICT_UNSEEN_DIGEST,
        "strict_unseen_membership": membership,
        "source_audit": _binding(SOURCE_AUDIT_V2_PATH, SOURCE_AUDIT_DIGEST),
        "test_workload": {
            "workload_id": identity["workload_id"],
            "sha256": identity["sha256"],
            "query_count": identity["query_count"],
        },
        "test_truth": {
            "observations_sha256": test_spec["observations_sha256"],
            "workload_id": truth_wire["workload_id"],
            "query_count": len(truth_wire["truths"]),
        },
        "stock_postgresql": {
            "source_commit_sha": FROZEN_STOCK_POSTGRES_SHA,
            "postgres_version": "16.14",
            "evaluation_mode": "stock-full-data-deployment",
            "ordinary_statistics_target": STATISTICS_TARGET,
        },
        "advisor": {
            "source_commit_sha": FROZEN_ADVISOR_SHA,
        },
        "s_test_candidate_ids": baseline.get("s_test_candidate_ids", []),
        "workload": workload["queries"],
        "truths": truths,
    }


def execute_power7_rq1b_evaluation_live(
    *,
    research_root: Path,
    producer_sha: str,
    design_artifact: Mapping[str, Any],
    design_runtime_directory: Path,
    stock_dsn: str,
    advisor_root: Path,
    data_root: Path | None = None,
    result_output: Path | None = None,
    per_query_output: Path | None = None,
    deployment_output: Path | None = None,
    advisor_command: str = "extstats-advisor",
    stock_postgres_root: Path | None = None,
    runtime_cleanup_root: Path | None = None,
    cleanup: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Deploy a sealed valid design and evaluate the test workload once."""

    root = research_root.resolve()
    validate_design_artifact(design_artifact, expected_producer=producer_sha)
    _require(
        verify_git_sha(advisor_root.resolve(), FROZEN_ADVISOR_SHA) == FROZEN_ADVISOR_SHA,
        "live Advisor SHA drift",
    )
    if stock_postgres_root is not None:
        _require(
            verify_git_sha(stock_postgres_root.resolve(), FROZEN_STOCK_POSTGRES_SHA)
            == FROZEN_STOCK_POSTGRES_SHA,
            "live stock PostgreSQL SHA drift",
        )
    design_runtime = design_runtime_directory.resolve()
    required = {
        "snapshot": design_runtime / "advisor-snapshot",
        "candidate_universe": design_runtime / "candidate-universe.json",
        "native_repository": design_runtime / "native-stats-repository",
        "ground_truth": design_runtime / "ground-truth-v1.json",
        "singleton_profile": design_runtime / "singleton-profile.json",
        "optimization_plan": design_runtime / "optimization-plan.json",
        "search_result": design_runtime / "search-result.json",
        "recommendation": design_runtime / "recommendation.json",
    }
    for name, path in required.items():
        _require(path.exists(), f"sealed design runtime artifact is missing: {name}")

    from .postgres.loader import load_power7

    evaluation_inputs = resolve_power7_rq1b_evaluation_inputs_after_seal(
        research_root=root,
        producer_sha=producer_sha,
        design_artifact=design_artifact,
        design_runtime_directory=design_runtime,
        data_root=data_root,
    )
    baseline_records = evaluation_inputs["baseline_records"]
    per_query_destination = _path(root, per_query_output or PER_QUERY_PATH)
    deployment_destination = _path(root, deployment_output or DEPLOYMENT_PATH)
    result_destination = _path(root, result_output or RESULT_PATH)
    for destination in (per_query_destination, deployment_destination, result_destination):
        _require(not destination.exists(), f"RQ1b evaluation output already exists: {destination}")

    def resolver() -> dict[str, Any]:
        value = dict(evaluation_inputs)
        value["advisor"] = {
            "source_commit_sha": FROZEN_ADVISOR_SHA,
            "command": advisor_command,
            "source_checkout_verified": True,
        }
        value["baseline_records"] = baseline_records
        return value

    def evaluator(inputs: dict[str, Any], sealed: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
        from .postgres_lab import reinit_role

        try:
            reinit_role("stock")
            load = load_power7(
                stock_dsn,
                data_root=data_root,
                reset_disposable=True,
                statistics_target=STATISTICS_TARGET,
                seed_identifier=123,
            )
            _require(
                load.get("physical_extended_statistics_count") == 0,
                "stock state has extstats before deployment",
            )
            _run_live_command(
                [
                    advisor_command,
                    "deployment",
                    "apply",
                    "postgres",
                    *[
                        str(required[name])
                        for name in (
                            "snapshot",
                            "candidate_universe",
                            "native_repository",
                            "ground_truth",
                            "singleton_profile",
                            "optimization_plan",
                            "search_result",
                            "recommendation",
                        )
                    ],
                    "--dsn",
                    stock_dsn,
                    "--output",
                    str(deployment_destination),
                ]
            )
            _run_live_command(
                [
                    advisor_command,
                    "deployment",
                    "validate",
                    str(deployment_destination),
                    str(required["recommendation"]),
                    "--snapshot",
                    str(required["snapshot"]),
                    "--candidate-universe",
                    str(required["candidate_universe"]),
                    "--native-repository",
                    str(required["native_repository"]),
                    "--ground-truth",
                    str(required["ground_truth"]),
                    "--singleton-profile",
                    str(required["singleton_profile"]),
                    "--optimization-plan",
                    str(required["optimization_plan"]),
                    "--search-result",
                    str(required["search_result"]),
                ]
            )
            deployment = read_json(deployment_destination)
            expected_order = sealed["design_stage"]["deployment_ordered_candidate_ids"]
            _require(
                deployment.get("deployment_ordered_candidate_ids") == expected_order,
                "deployment order differs from sealed Recommendation",
            )
            inputs["deployment"] = {
                "logical_path": DEPLOYMENT_PATH.as_posix(),
                "semantic_digest": deployment.get("semantic_digest"),
                "stock_postgresql_sha": FROZEN_STOCK_POSTGRES_SHA,
            }
            records = _evaluate_power7_once(
                stock_dsn=stock_dsn,
                workload=inputs["workload"],
                truths=inputs["truths"],
                output=per_query_destination,
            )
            inputs["per_query_artifact"] = {
                "logical_path": PER_QUERY_PATH.as_posix(),
                "sha256": sha256_file(per_query_destination),
                "query_count": len(records),
            }
            return records
        finally:
            cleanup_result = cleanup() if cleanup is not None else _stop_formal_roles()
            cleanup_result = _cleanup_live_runtime(root, runtime_cleanup_root, cleanup_result)
            _require(cleanup_result is True, "RQ1b live cleanup failed")

    result = run_power7_rq1b_evaluation(
        design_artifact=design_artifact,
        evaluation_resolver=resolver,
        evaluator=evaluator,
        baseline_records=baseline_records,
        cleanup_passed=True,
    )
    publish_result(result=result, output=result_destination)
    return {"status": "success", "result": result, "result_path": result_destination}


def run_power7_rq1b_formal(
    *,
    research_root: Path,
    preflight_path: Path,
    stock_dsn: str,
    planner_dsn: str,
    advisor_root: Path,
    patched_postgres_root: Path,
    stock_postgres_root: Path,
    data_root: Path | None = None,
    advisor_command: str = "extstats-advisor",
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """Future formal command; never called by offline validation."""

    root = research_root.resolve()
    preflight = read_json(_path(root, preflight_path))
    validate_power7_rq1b_preflight(preflight, research_root=root)
    verify_power7_formal_tree(
        research_root=root, preflight_path=preflight_path, preflight=preflight
    )
    _validate_managed_lab_dsns(stock_dsn=stock_dsn, planner_dsn=planner_dsn)
    producer = str(preflight["research_commit_sha"])
    runtime_base = _path(root, runtime_root or root / ".runtime/rq1b-power7")
    runtime = runtime_base / "design"
    try:
        # ``advisor_command`` is retained for API compatibility with older
        # callers, but the formal RQ1b path never trusts an arbitrary command
        # or PATH entry as the scientific executable identity.
        launcher = prepare_frozen_advisor_launcher(
            advisor_root=advisor_root,
            runtime_root=runtime_base,
        )
        resolved_advisor_command = launcher["launcher_path"]
        prepare_power7_rq1b_formal_labs(
            advisor_root=advisor_root,
            patched_postgres_root=patched_postgres_root,
            stock_postgres_root=stock_postgres_root,
        )
        design = execute_power7_rq1b_design_live(
            research_root=root,
            producer_sha=producer,
            production_dsn=stock_dsn,
            planner_dsn=planner_dsn,
            advisor_root=advisor_root,
            patched_postgres_root=patched_postgres_root,
            stock_postgres_root=stock_postgres_root,
            output_root=runtime,
            data_root=data_root,
            advisor_command=resolved_advisor_command,
        )
        sealed_design = read_json(root / DESIGN_PATH)
        validate_design_artifact(sealed_design, expected_producer=producer)
        return execute_power7_rq1b_evaluation_live(
            research_root=root,
            producer_sha=producer,
            design_artifact=sealed_design,
            design_runtime_directory=design["runtime_directory"],
            stock_dsn=stock_dsn,
            advisor_root=advisor_root,
            data_root=data_root,
            advisor_command=resolved_advisor_command,
            stock_postgres_root=stock_postgres_root,
            runtime_cleanup_root=runtime_base,
        )
    finally:
        _cleanup_live_runtime(root, runtime_base, _stop_formal_roles())


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
    "DEPLOYMENT_PATH",
    "DESIGN_FORMAT",
    "DESIGN_PATH",
    "EVALUATION_SPLIT",
    "K_S",
    "PER_QUERY_PATH",
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
    "execute_power7_rq1b_design_live",
    "execute_power7_rq1b_evaluation_live",
    "paired_outcomes",
    "prepare_evaluation_stage",
    "prepare_frozen_advisor_launcher",
    "prepare_power7_rq1b_formal_labs",
    "publish_result",
    "resolve_power7_rq1b_evaluation_inputs_after_seal",
    "run_power7_rq1b_design",
    "run_power7_rq1b_evaluation",
    "run_power7_rq1b_formal",
    "strict_unseen_filter",
    "summarize_qerrors",
    "validate_design_artifact",
    "validate_power7_rq1b_preflight",
    "validate_power7_rq1b_result",
    "validate_test_evaluation_records",
    "verify_power7_formal_tree",
    "write_power7_rq1b_preflight",
]
