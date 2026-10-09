"""Offline synthesis of the four published RQ1b dataset children.

This module is deliberately downstream of the formal artifacts.  It never
opens a database, invokes Advisor, or regenerates a workload.  Every metric
in the derived artifact is recomputed from the published per-query records
and the immutable RQ1a baseline records.
"""

from __future__ import annotations

import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .paper_baseline import percentile, qerror
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq1_canary import validate_rq1_artifact
from .rq1_workload_generalization import (
    SOURCE_AUDIT_V2_PATH,
    STRICT_UNSEEN_PATH,
    validate_source_audit_v2,
    validate_strict_unseen_membership,
)
from .rq1_workload_generalization_live import (
    CENSUS13_SPEC,
    DMV11_SPEC,
    FOREST10_SPEC,
    POWER7_SPEC,
    SAMPLE_ROWS,
    STRICT_UNSEEN_DIGEST,
    B,
    RQ1BDatasetSpec,
    RQ1BValidationError,
    _baseline_binding,
    validate_design_artifact,
    validate_rq1b_preflight,
    validate_rq1b_result,
)

SUMMARY_FORMAT = "rq1-workload-generalization-cross-dataset-v1"
DATASET_ORDER = ("arecel-census13", "arecel-forest10", "arecel-power7", "arecel-dmv11")
RESULT_DIGESTS = {
    "arecel-census13": "9cb84a6eaa1f35f3761f90853a519de36be6448f9eafe3db389c5e27acffb41b",
    "arecel-forest10": "106809bb0ad45ee6aa0ac0a238eb22299a17e0f4d396e883ef68718d8234d8dd",
    "arecel-power7": "1af0a8d65a78f6c6c19e63bcf2a8f1f8ab39f6a9a24dc0f00a8266e44508e9a3",
    "arecel-dmv11": "f7aabb8ac7839457cb49bd3dd21e57e482a9967141ef45174293b5c06422b53c",
}
SPECS = {spec.dataset_id: spec for spec in (CENSUS13_SPEC, FOREST10_SPEC, POWER7_SPEC, DMV11_SPEC)}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RQ1BValidationError(message)


def _without_digest(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _artifact_ref(root: Path, path: Path, value: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _relative(root, path),
        "byte_sha256": sha256_file(path),
        "byte_size": path.stat().st_size,
    }
    if value is not None and isinstance(value.get("semantic_digest"), str):
        result["semantic_digest"] = value["semantic_digest"]
    return result


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise RQ1BValidationError(f"invalid JSONL at {path}:{line_number}") from exc
    return rows


def _expected_ids(spec: RQ1BDatasetSpec) -> list[str]:
    stem = spec.test_workload_id.removesuffix("_v1")
    return [f"{stem}_{index:06d}" for index in range(SAMPLE_ROWS)]


def _validate_records(
    records: Sequence[Mapping[str, Any]], spec: RQ1BDatasetSpec
) -> list[dict[str, Any]]:
    expected = _expected_ids(spec)
    _require(len(records) == SAMPLE_ROWS, f"{spec.dataset_id} must contain 10000 records")
    normalized: list[dict[str, Any]] = []
    for expected_id, row in zip(expected, records, strict=True):
        _require(row.get("query_id") == expected_id, f"{spec.dataset_id} query order drift")
        estimate = row.get("estimate")
        truth = row.get("truth")
        _require(
            isinstance(estimate, (int, float)) and not isinstance(estimate, bool),
            "invalid estimate",
        )
        _require(isinstance(truth, (int, float)) and not isinstance(truth, bool), "invalid truth")
        expected_qerror = qerror(float(estimate), float(truth))
        _require(
            float(row.get("qerror", -1.0)) == expected_qerror, f"q-error drift for {expected_id}"
        )
        normalized.append(dict(row))
    return normalized


def validate_observation_alignment(
    advisor: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Sequence[Mapping[str, Any]]],
    spec: RQ1BDatasetSpec,
) -> dict[str, list[dict[str, Any]]]:
    """Validate all three arms without accessing a live system."""

    advisor_rows = _validate_records(advisor, spec)
    baseline_rows = {arm: _validate_records(rows, spec) for arm, rows in baseline.items()}
    _require(set(baseline_rows) == {"pg16-default", "pg16-target10000"}, "baseline arms drift")
    advisor_truth = {row["query_id"]: row["truth"] for row in advisor_rows}
    for arm, rows in baseline_rows.items():
        _require(
            {row["query_id"]: row["truth"] for row in rows} == advisor_truth,
            f"{spec.dataset_id} truth mapping differs in {arm}",
        )
    return {"advisor": advisor_rows, **baseline_rows}


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [float(row["qerror"]) for row in rows]
    _require(values, "cannot summarize empty records")
    return {
        "query_count": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "p99": percentile(values, 0.99),
        "max": max(values),
    }


def _paired(
    reference: Sequence[Mapping[str, Any]], advisor: Sequence[Mapping[str, Any]]
) -> dict[str, int]:
    _require(
        [row["query_id"] for row in reference] == [row["query_id"] for row in advisor],
        "paired query identity drift",
    )
    counts = {"improved": 0, "unchanged": 0, "worsened": 0}
    for left, right in zip(reference, advisor, strict=True):
        if float(right["qerror"]) < float(left["qerror"]):
            counts["improved"] += 1
        elif float(right["qerror"]) > float(left["qerror"]):
            counts["worsened"] += 1
        else:
            counts["unchanged"] += 1
    return counts


def _tail_concentration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = sorted((float(row["qerror"]) for row in rows), reverse=True)
    total = sum(values)
    result: dict[str, Any] = {"query_count": len(values)}
    for label, fraction in (("top_1_percent", 0.01), ("top_10_percent", 0.10)):
        count = max(1, math.ceil(len(values) * fraction))
        result[label] = {
            "query_count": count,
            "fraction_of_total_qerror": sum(values[:count]) / total if total else 0.0,
        }
    return result


def _comparison(reference: Mapping[str, Any], advisor: Mapping[str, Any]) -> dict[str, Any]:
    change = (advisor["mean"] - reference["mean"]) / reference["mean"]
    return {
        "baseline": dict(reference),
        "s_valid": dict(advisor),
        "relative_mean_change": change,
        "absolute_mean_change": advisor["mean"] - reference["mean"],
    }


def _strict_ids(root: Path, spec: RQ1BDatasetSpec) -> tuple[set[str], dict[str, Any]]:
    path = root / STRICT_UNSEEN_PATH
    membership = read_json(path)
    validate_strict_unseen_membership(path, root)
    _require(
        membership.get("semantic_digest") == STRICT_UNSEEN_DIGEST, "strict membership digest drift"
    )
    rows = [row for row in membership["datasets"] if row.get("dataset_id") == spec.dataset_id]
    _require(len(rows) == 1, f"strict membership row missing for {spec.dataset_id}")
    row = rows[0]
    ids = set(row["strict_unseen_test_query_ids"])
    _require(
        len(ids) == row["strict_unseen_count"],
        f"strict membership duplicate IDs for {spec.dataset_id}",
    )
    return ids, row


def _source_row(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    path = root / SOURCE_AUDIT_V2_PATH
    audit = read_json(path)
    validate_source_audit_v2(path, root)
    rows = [row for row in audit["datasets"] if row.get("dataset_id") == spec.dataset_id]
    _require(len(rows) == 1, f"source audit row missing for {spec.dataset_id}")
    row = rows[0]
    _require(
        row["valid_query_count"] == SAMPLE_ROWS and row["test_query_count"] == SAMPLE_ROWS,
        "source audit count drift",
    )
    return row


def _registry_attempt(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    registry = read_json(root / "paper/paper-experiment-v1.json")
    experiment = next(
        item
        for item in registry["experiments"]
        if item.get("experiment_id") == "rq1-held-out-workload-generalization"
    )
    prefix = spec.cli_name
    attempts = experiment[f"{prefix}_formal_attempts"]
    _require(len(attempts) >= 1, f"{spec.dataset_id} has no published formal attempt")
    attempt = attempts[-1]
    _require(attempt.get("status") == "complete", f"{spec.dataset_id} attempt is not complete")
    _require(
        attempt.get("evidence_eligible") is True, f"{spec.dataset_id} is not evidence eligible"
    )
    _require(
        attempt.get("formal_invocation_count") == 1, f"{spec.dataset_id} invocation count drift"
    )
    return attempt


def _dataset(root: Path, spec: RQ1BDatasetSpec) -> dict[str, Any]:
    out = root / spec.output_root
    paths = {
        "preflight": out / "rq1b-preflight-v1.json",
        "design": out / "design-v1.json",
        "deployment": out / "deployment-result-v1.json",
        "per_query": out / "pg16-advisor-valid-to-test-per-query-v1.jsonl",
        "result": out / "result-v1.json",
    }
    for path in paths.values():
        _require(path.is_file(), f"missing published RQ1b artifact: {path}")
    preflight = read_json(paths["preflight"])
    design = read_json(paths["design"])
    result = read_json(paths["result"])
    validate_rq1b_preflight(preflight, research_root=root, spec=spec)
    producer = result.get("producer", {}).get("research_commit_sha")
    validate_design_artifact(design, expected_producer=producer, _spec=spec)
    validate_rq1b_result(result, research_root=root, spec=spec)
    _require(
        result.get("semantic_digest") == RESULT_DIGESTS[spec.dataset_id],
        f"{spec.dataset_id} result digest drift",
    )
    _require(
        result.get("cleanup", {}).get("passed") is True, f"{spec.dataset_id} cleanup did not pass"
    )
    registry_attempt = _registry_attempt(root, spec)
    _require(
        registry_attempt.get("result_semantic_digest") == result["semantic_digest"],
        "registry/result digest drift",
    )
    _require(registry_attempt.get("producer_sha") == producer, "registry/result producer drift")

    baseline_path = root / spec.baseline_path
    baseline = read_json(baseline_path)
    validate_rq1_artifact(baseline)
    binding = _baseline_binding(root, spec)
    _require(
        binding["semantic_digest"] == spec.baseline_semantic_digest, "baseline digest binding drift"
    )

    advisor = _jsonl(paths["per_query"])
    baselines = {
        arm: baseline["per_arm"][arm]["per_query"] for arm in ("pg16-default", "pg16-target10000")
    }
    arms = validate_observation_alignment(advisor, baselines, spec)
    strict_ids, strict_row = _strict_ids(root, spec)
    _require(
        strict_ids <= {row["query_id"] for row in arms["advisor"]},
        "strict membership references unknown query",
    )
    strict_arms = {
        arm: [row for row in rows if row["query_id"] in strict_ids] for arm, rows in arms.items()
    }
    _require(
        len(strict_arms["advisor"]) == strict_row["strict_unseen_count"],
        "strict subset count drift",
    )

    full = {
        arm: _comparison(_metrics(arms[arm]), _metrics(arms["advisor"]))
        for arm in ("pg16-default", "pg16-target10000")
    }
    strict = {
        arm: _comparison(_metrics(strict_arms[arm]), _metrics(strict_arms["advisor"]))
        for arm in ("pg16-default", "pg16-target10000")
    }
    full["pg16-default"]["paired"] = _paired(arms["pg16-default"], arms["advisor"])
    full["pg16-target10000"]["paired"] = _paired(arms["pg16-target10000"], arms["advisor"])
    strict["pg16-default"]["paired"] = _paired(strict_arms["pg16-default"], strict_arms["advisor"])
    strict["pg16-target10000"]["paired"] = _paired(
        strict_arms["pg16-target10000"], strict_arms["advisor"]
    )

    design_stage = design["design_stage"]
    recommendation = baseline["per_arm"]["pg16-advisor"]["recommendation"]
    s_valid = list(design_stage["deployment_ordered_candidate_ids"])
    s_test = list(recommendation["deployment_ordered_candidate_ids"])
    s_valid_set, s_test_set = set(s_valid), set(s_test)
    _require(len(s_valid) <= B and len(s_valid) == len(s_valid_set), "S_valid membership drift")
    source = _source_row(root, spec)
    paths_ref = {
        name: _artifact_ref(root, path, None if path.suffix == ".jsonl" else read_json(path))
        for name, path in paths.items()
    }
    paths_ref["per_query"].pop("semantic_digest", None)
    return {
        "dataset_id": spec.dataset_id,
        "scientific_producer_sha": producer,
        "formal_attempt_index": preflight["campaign_attempt_index"],
        "registry_attempt": {
            "status": registry_attempt["status"],
            "evidence_eligible": registry_attempt["evidence_eligible"],
            "formal_invocation_count": registry_attempt["formal_invocation_count"],
        },
        "published_result_digest": result["semantic_digest"],
        "artifact_bindings": paths_ref,
        "baseline": {
            "path": _relative(root, baseline_path),
            "semantic_digest": baseline["semantic_digest"],
            "statistics_policy": {
                arm: baseline["per_arm"][arm]["statistics_policy"]
                for arm in ("pg16-default", "pg16-target10000")
            },
        },
        "design": {
            "candidate_universe_count": None,
            "candidate_universe_count_status": "not-recorded-in-published-design-artifact",
            "search_termination": design_stage.get("termination_reason"),
            "search_elapsed_seconds": design_stage.get("search_elapsed_seconds"),
            "baseline_objective": design_stage.get("search_baseline_objective"),
            "final_objective": design_stage.get("search_final_objective"),
            "selected_k": len(s_valid),
            "s_valid_candidate_ids": s_valid,
            "recommendation_digest": design_stage["recommendation_digest"],
        },
        "source_audit": {
            "dataset_content_identity": source["dataset_content_identity"],
            "valid_workload_id": source["valid_workload_id"],
            "valid_workload_sha256": source["valid_workload_sha256"],
            "test_workload_id": source["test_workload_id"],
            "test_workload_sha256": source["test_workload_sha256"],
            "valid_query_count": source["valid_query_count"],
            "test_query_count": source["test_query_count"],
            "valid_unique_query_hash_count": source["valid_unique_query_hash_count"],
            "test_unique_query_hash_count": source["test_unique_query_hash_count"],
            "valid_test_unique_hash_overlap_count": source["valid_test_unique_hash_overlap_count"],
        },
        "full_test": full,
        "strict_unseen": {
            "query_count": len(strict_ids),
            "fraction": len(strict_ids) / SAMPLE_ROWS,
            "seen_in_valid_count": strict_row["seen_in_valid_count"],
            "metrics": strict,
        },
        "recommendation_comparison": {
            "s_valid_count": len(s_valid),
            "s_test_count": len(s_test),
            "s_valid_candidate_ids": s_valid,
            "s_test_candidate_ids": s_test,
            "intersection_count": len(s_valid_set & s_test_set),
            "union_count": len(s_valid_set | s_test_set),
            "jaccard": len(s_valid_set & s_test_set) / len(s_valid_set | s_test_set),
            "comparison_level": "identifier-only",
            "physical_definition_comparison": {
                "status": "not-derived",
                "reason": "immutable artifacts do not establish cross-run candidate identity equivalence",
            },
        },
        "descriptive_inputs": {
            "relative_mean_change_vs_pg16_default": full["pg16-default"]["relative_mean_change"],
            "relative_mean_change_vs_pg16_target10000": full["pg16-target10000"][
                "relative_mean_change"
            ],
            "strict_unseen_minus_full_s_valid_mean": {
                "pg16-default": strict["pg16-default"]["s_valid"]["mean"]
                - full["pg16-default"]["s_valid"]["mean"],
                "pg16-target10000": strict["pg16-target10000"]["s_valid"]["mean"]
                - full["pg16-target10000"]["s_valid"]["mean"],
            },
            "paired_regression_fraction_vs_default": full["pg16-default"]["paired"]["worsened"]
            / SAMPLE_ROWS,
            "tail_behavior": {
                "default": _tail_concentration(arms["pg16-default"]),
                "s_valid": _tail_concentration(arms["advisor"]),
            },
            "all_source_validation_gates_passed": True,
        },
        "validation": {
            "preflight": True,
            "design": True,
            "deployment": True,
            "per_query": True,
            "result": True,
            "cleanup_passed": True,
            "evidence_eligible": True,
        },
    }


def default_source_paths(root: Path) -> tuple[Path, ...]:
    return tuple(
        root / f"experiments/{dataset}/rq1-workload-generalization-v1/result-v1.json"
        for dataset in DATASET_ORDER
    )


def _assemble(root: Path, *, synthesis_producer_sha: str) -> dict[str, Any]:
    source_audit_path = root / SOURCE_AUDIT_V2_PATH
    audit = read_json(source_audit_path)
    validate_source_audit_v2(source_audit_path, root)
    strict_path = root / STRICT_UNSEEN_PATH
    validate_strict_unseen_membership(strict_path, root)
    rows = [_dataset(root, SPECS[dataset]) for dataset in DATASET_ORDER]
    return {
        "format_version": SUMMARY_FORMAT,
        "summary_id": SUMMARY_FORMAT,
        "status": "complete",
        "rq": "RQ1b",
        "scope": "four-dataset held-out workload generalization",
        "derived_offline": True,
        "scientific_reexecution": False,
        "synthesis_producer_sha": synthesis_producer_sha,
        "global_provenance": {
            "protocol": {
                "path": "paper/rq1-workload-generalization-protocol-v2.json",
                "semantic_digest": "ffa5451d5212a09c4eb4713cb514945119d89a0ad6254b034a81ab7a3a88c602",
            },
            "source_audit": {
                "path": SOURCE_AUDIT_V2_PATH.as_posix(),
                "semantic_digest": audit["semantic_digest"],
            },
            "truth_policy": {
                "path": "paper/rq1-workload-generalization-truth-policy-v1.json",
                "semantic_digest": "e880f0f5b6cebd468fbadcd235408ba3a4b07ee9313f4705d11cf8f4fe83d36a",
            },
            "strict_unseen": {
                "path": STRICT_UNSEEN_PATH.as_posix(),
                "semantic_digest": read_json(strict_path)["semantic_digest"],
            },
            "advisor_sha": "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3",
            "patched_postgres_sha": "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6",
            "stock_postgres_sha": "0d1c00c624fa7367d4a895f44381887757289682",
            "postgres_version": "16.14",
        },
        "source_result_artifacts": [row["artifact_bindings"]["result"] for row in rows],
        "datasets": rows,
    }


def build_cross_dataset_synthesis(root: Path, output: Path | None = None) -> dict[str, Any]:
    root = root.resolve()
    producer = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    value = _assemble(root, synthesis_producer_sha=producer)
    value["semantic_digest"] = semantic_digest(_without_digest(value))
    if output is not None:
        write_json(output, value)
    return value


def validate_cross_dataset_synthesis(
    path: Path, research_root: Path | None = None
) -> dict[str, Any]:
    root = (research_root or Path(__file__).resolve().parents[2]).resolve()
    value = read_json(path)
    _require(value.get("format_version") == SUMMARY_FORMAT, "unsupported RQ1b synthesis format")
    _require(
        value.get("semantic_digest") == semantic_digest(_without_digest(value)),
        "synthesis digest mismatch",
    )
    # Reject obvious identity/reference mutations before the intentionally
    # expensive child-level recomputation.  The complete, unmutated artifact
    # still goes through _assemble below; this only makes fail-closed mutation
    # checks deterministic and avoids doing four full 10k-row validations for
    # a summary whose dataset identity is already invalid.
    datasets = value.get("datasets")
    _require(isinstance(datasets, list), "synthesis does not match recomputed evidence")
    _require(
        [row.get("dataset_id") for row in datasets if isinstance(row, Mapping)]
        == list(DATASET_ORDER),
        "synthesis does not match recomputed evidence",
    )
    _require(
        all(
            isinstance(row, Mapping)
            and row.get("published_result_digest") == RESULT_DIGESTS[row["dataset_id"]]
            for row in datasets
        ),
        "synthesis does not match recomputed evidence",
    )
    expected = _assemble(root, synthesis_producer_sha=value.get("synthesis_producer_sha"))
    _require(_without_digest(value) == expected, "synthesis does not match recomputed evidence")
    return {
        "status": "valid",
        "format_version": SUMMARY_FORMAT,
        "semantic_digest": value["semantic_digest"],
    }


__all__ = [
    "DATASET_ORDER",
    "SUMMARY_FORMAT",
    "build_cross_dataset_synthesis",
    "default_source_paths",
    "validate_cross_dataset_synthesis",
    "validate_observation_alignment",
]
