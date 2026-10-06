"""Canonicalize the historical Census13 RQ1 result onto external truth.

The transformation is deliberately offline.  It reuses the immutable planner
estimates from the historical canary and changes only the bound truth vector
and derived q-error fields after the full Census13 equivalence gate passes.
"""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

from extstats_advisor.utility import QErrorLoss

from .arecel_truth import observation_records, validate_truth_policy
from .provenance import read_json, semantic_digest, sha256_file, write_json
from .rq1_canary import build_rq1_artifact, summarize_per_query, validate_rq1_artifact
from .system_freeze import DEFAULT_SYSTEM_FREEZE_PATH, load_system_freeze

REBIND_FORMAT = "rq1-truth-rebind-v1"
DATASET_ID = "arecel-census13"


def _current_research_sha(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _diagnostic(output: Path, details: dict[str, Any]) -> None:
    write_json(
        output.parent / "rq1-truth-rebind-diagnostic-v1.json",
        {"format_version": REBIND_FORMAT, "status": "failed", **details},
    )


def canonicalize_census13(
    *,
    historical_artifact: Path,
    observations_path: Path,
    equivalence_artifact: Path,
    policy_path: Path,
    system_freeze_path: Path = DEFAULT_SYSTEM_FREEZE_PATH,
    output: Path,
    research_commit_sha: str | None = None,
) -> dict[str, Any]:
    """Produce a canonical external-truth RQ1 artifact without PostgreSQL work."""

    historical = read_json(historical_artifact)
    validate_rq1_artifact(historical)
    if historical["dataset"]["dataset_id"] != DATASET_ID:
        raise ValueError("truth rebinding requires the Census13 RQ1 artifact")
    if historical["truth"]["source_kind"] != "production-exact-execution":
        raise ValueError("truth rebinding requires the historical production-exact artifact")

    equivalence = read_json(equivalence_artifact)
    if equivalence.get("format_version") != "arecel-truth-equivalence-v1":
        raise ValueError("unsupported Census13 equivalence artifact")
    if equivalence.get("semantic_digest") != semantic_digest(_payload(equivalence)):
        raise ValueError("Census13 equivalence artifact semantic digest mismatch")
    expected_equivalence = {
        "dataset_id": DATASET_ID,
        "query_count": 10_000,
        "matched": 10_000,
        "mismatched": 0,
        "missing": 0,
        "extra": 0,
    }
    if any(equivalence.get(key) != value for key, value in expected_equivalence.items()):
        raise ValueError("Census13 full equivalence gate is not 10,000/10,000")
    if (
        equivalence.get("production_exact_ground_truth_semantic_digest")
        != historical["truth"]["semantic_digest"]
    ):
        raise ValueError("equivalence artifact is not bound to the historical GroundTruthSet")

    policy = read_json(policy_path)
    validate_truth_policy(policy, research_root=policy_path.parents[1])
    policy_entry = next(item for item in policy["datasets"] if item["dataset_id"] == DATASET_ID)
    if policy_entry["status"] != "validated-full-equivalence":
        raise ValueError("Census13 truth rebinding requires validated-full-equivalence policy")
    if policy_entry["equivalence_evidence"]["artifact"] != str(
        equivalence_artifact.relative_to(policy_path.parents[1])
    ):
        raise ValueError("Census13 policy does not name the supplied equivalence artifact")
    system_freeze = load_system_freeze(system_freeze_path)
    observations = observation_records(observations_path)
    observations_sha = sha256_file(observations_path)
    audit_path = observations_path.parent / "audit-v1.json"
    audit = read_json(audit_path)
    if audit.get("observation_sha256") != observations_sha:
        raise ValueError("Census13 observation audit does not match supplied observations")
    if equivalence.get("external_observations_sha256") != observations_sha:
        raise ValueError("equivalence artifact is not bound to supplied observations")
    if audit.get("query_count") != len(observations) or len(observations) != 10_000:
        raise ValueError("Census13 external observations do not contain exactly 10,000 queries")

    old_truths: dict[str, int] | None = None
    old_qerrors: dict[str, dict[str, float]] = {}
    for arm_id in historical["arms"]:
        rows = historical["per_arm"][arm_id]["per_query"]
        arm_truths = {row["query_id"]: int(row["truth"]) for row in rows}
        if old_truths is None:
            old_truths = arm_truths
        elif arm_truths != old_truths:
            raise ValueError(f"historical {arm_id} truth vector differs from another arm")
        old_qerrors[arm_id] = {row["query_id"]: float(row["qerror"]) for row in rows}
    if old_truths != observations:
        _diagnostic(
            output,
            {
                "reason": "external truth differs from historical production exact truth",
                "matched": len(set(old_truths or {}) & set(observations)),
                "mismatched": sum(
                    1
                    for query_id in set(old_truths or {}) & set(observations)
                    if old_truths[query_id] != observations[query_id]
                ),
                "missing": sorted(set(old_truths or {}) - set(observations)),
                "extra": sorted(set(observations) - set(old_truths or {})),
            },
        )
        raise ValueError("external Census13 truth differs from historical truth")

    external_truth = {
        "source_kind": "authoritative-external-exact",
        "collection_contract": "authoritative-external-exact-cardinality-v1",
        "authority": policy_entry["authority"],
        "dataset_identity": policy_entry["dataset_identity"],
        "source_revision": policy_entry["source_revision"],
        "source_artifact_sha256": observations_sha,
        "observations_sha256": observations_sha,
        "observations_semantic_digest": audit["observation_semantic_digest"],
        "policy_status": policy_entry["status"],
        "workload_id": historical["workload"]["workload_id"],
        "query_count": len(observations),
    }
    loss = QErrorLoss()
    arms = copy.deepcopy(historical["per_arm"])
    output.parent.mkdir(parents=True, exist_ok=True)
    for arm_id in historical["arms"]:
        old_rows = arms[arm_id]["per_query"]
        new_rows = []
        for row in old_rows:
            estimate = int(row["estimate"])
            truth = observations[row["query_id"]]
            new_qerror = float(loss.loss(estimate, truth))
            if new_qerror != old_qerrors[arm_id][row["query_id"]]:
                _diagnostic(
                    output,
                    {
                        "reason": "q-error changed after exact truth rebinding",
                        "arm_id": arm_id,
                        "query_id": row["query_id"],
                        "old_qerror": old_qerrors[arm_id][row["query_id"]],
                        "new_qerror": new_qerror,
                    },
                )
                raise ValueError("Census13 q-error serialization is not exactly reproducible")
            new_rows.append({**row, "truth": truth, "qerror": new_qerror})
        raw_path = output.parent / f"{arm_id}-per-query-v1.jsonl"
        with raw_path.open("w", encoding="utf-8") as stream:
            for row in new_rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        arms[arm_id]["per_query"] = new_rows
        arms[arm_id]["truth_identity"] = copy.deepcopy(external_truth)
        arms[arm_id]["per_query_artifact"] = {
            "logical_path": str(raw_path.relative_to(policy_path.parents[1])),
            "sha256": sha256_file(raw_path),
        }
        if summarize_per_query(new_rows) != historical["per_arm"][arm_id]["summary"]:
            _diagnostic(output, {"reason": "aggregate metrics changed", "arm_id": arm_id})
            raise ValueError("Census13 aggregate metrics changed after exact truth rebinding")

    arms["pg16-advisor"]["deployment_result"] = {
        "verified": historical["cleanup"].get("advisor_deployment_verified") is True,
        "semantic_digest": historical["per_arm"]["pg16-advisor"]
        .get("recommendation", {})
        .get("deployment_result_semantic_digest"),
        "source": "historical matched-comparison deployment evidence",
    }

    historical_digest = semantic_digest(historical)
    artifact = build_rq1_artifact(
        research_commit_sha=research_commit_sha or _current_research_sha(policy_path.parents[1]),
        system_freeze=system_freeze,
        dataset=historical["dataset"],
        workload=historical["workload"],
        truth=external_truth,
        arms=arms,
        dataset_progress={
            "arecel-census13": "complete",
            "arecel-forest10": "planned",
            "arecel-power7": "planned",
            "arecel-dmv11": "planned",
        },
        experiment_status="dataset-complete",
        cleanup=historical["cleanup"],
        provenance={
            "transformation_format": REBIND_FORMAT,
            "historical_matched_comparison_semantic_digest": historical_digest,
            "historical_matched_comparison_sha256": sha256_file(historical_artifact),
            "historical_production_exact_ground_truth_semantic_digest": historical["truth"][
                "semantic_digest"
            ],
            "authoritative_observations_sha256": observations_sha,
            "authoritative_observations_semantic_digest": audit["observation_semantic_digest"],
            "equivalence_artifact_semantic_digest": equivalence["semantic_digest"],
            "truth_policy": str(policy_path.relative_to(policy_path.parents[1])),
            "truth_policy_status": policy_entry["status"],
            "planner_estimates_reused": True,
            "planner_reexecuted": False,
            "database_workload_reexecuted": False,
            "exact_counts_reexecuted": False,
            "canonical_statement": (
                "Planner estimates were reused from the immutable historical canary; "
                "truth/evaluation was canonically rebound only because full 10,000-query "
                "equivalence had already been established."
            ),
        },
    )
    write_json(output, artifact)
    return {
        "status": "complete",
        "format_version": REBIND_FORMAT,
        "artifact": str(output),
        "semantic_digest": artifact["semantic_digest"],
        "historical_matched_comparison_semantic_digest": historical_digest,
        "metrics_unchanged": True,
    }


def validate_rebound_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    result = validate_rq1_artifact(artifact)
    if artifact["dataset"]["dataset_id"] != DATASET_ID:
        raise ValueError("truth-rebound artifact must be Census13")
    if artifact["truth"]["source_kind"] != "authoritative-external-exact":
        raise ValueError("truth-rebound artifact must use external truth")
    provenance = artifact.get("provenance", {})
    required = {
        "transformation_format",
        "historical_matched_comparison_semantic_digest",
        "historical_production_exact_ground_truth_semantic_digest",
        "authoritative_observations_sha256",
        "equivalence_artifact_semantic_digest",
    }
    if provenance.get("transformation_format") != REBIND_FORMAT:
        raise ValueError("artifact is not an rq1-truth-rebind-v1 result")
    missing = sorted(required - set(provenance))
    if missing:
        raise ValueError(f"truth-rebound artifact lacks provenance: {missing}")
    if provenance.get("planner_reexecuted") is not False:
        raise ValueError("truth-rebound artifact claims planner re-execution")
    if artifact["dataset_progress"].get(DATASET_ID) != "complete":
        raise ValueError("Census13 truth-rebound dataset progress is not complete")
    return {**result, "transformation_format": REBIND_FORMAT}


__all__ = ["REBIND_FORMAT", "canonicalize_census13", "validate_rebound_artifact"]
