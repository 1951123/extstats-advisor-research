"""Validation for the scoped System Freeze v2 readiness and manifest."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .provenance import read_json, reject_credentials, semantic_digest
from .system_freeze import BUILD_CONTRACT, CONFIGURE_FEATURES, POSTGRES_VERSION

SYSTEM_FREEZE_V2_FORMAT = "system-freeze-v2"
READINESS_FORMAT = "system-freeze-v2-readiness-review-v1"
FROZEN_ADVISOR_SHA = "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3"
FROZEN_PATCHED_POSTGRES_SHA = "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
FROZEN_STOCK_POSTGRES_SHA = "0d1c00c624fa7367d4a895f44381887757289682"
RESEARCH_REVIEW_SOURCE_SHA = "34c188f8c73e47f0bbccf2ea96f2194169d7a607"
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FREEZE_PATH = RESEARCH_ROOT / "paper" / "system-freeze-v2.json"
DEFAULT_READINESS_PATH = RESEARCH_ROOT / "paper" / "system-freeze-v2-readiness-review-v1.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")

MANDATORY_GATES = (
    "power7_singleton",
    "forest10_singleton",
    "incremental_greedy_live",
    "B_budget_and_termination",
    "deadline_and_cache",
    "artifact_compatibility",
    "code_provenance",
    "CI",
)
OUT_OF_SCOPE_DATASETS = ("census13_singleton", "dmv11_singleton")
EXECUTION_OPTIMIZATIONS = (
    "incidence-indexed incremental singleton profiling",
    "incidence-indexed incremental Greedy ADD",
    "baseline/affected-query estimate caching",
    "bounded proposal cache",
)
ALGORITHMIC_CONTROLS = (
    "maximum statistics count B",
    "canonical v2 default B=K_s",
    "explicit budget termination semantics",
)


def _commit(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _COMMIT.fullmatch(value):
        raise ValueError(f"{label} must be a 40-character commit SHA")


def _sha(value: Any, label: str) -> None:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError(f"{label} must be a SHA-256 digest")


def _require_identity(value: Any, label: str, repository: str, commit_sha: str) -> None:
    if not isinstance(value, dict):
        raise TypeError(f"{label} identity must be an object")
    if value.get("repository") != repository or value.get("commit_sha") != commit_sha:
        raise ValueError(f"{label} identity mismatch")
    _commit(value.get("commit_sha"), f"{label}.commit_sha")


def _require_build_identity(value: Any, label: str, repository: str, commit_sha: str) -> None:
    _require_identity(value, label, repository, commit_sha)
    if value.get("postgres_version") != POSTGRES_VERSION:
        raise ValueError(f"{label} PostgreSQL version mismatch")
    contract = value.get("required_build_contract")
    if not isinstance(contract, dict):
        raise TypeError(f"{label} lacks required_build_contract")
    for field, expected in BUILD_CONTRACT.items():
        if contract.get(field) != expected:
            raise ValueError(f"{label} {field} contract mismatch")


def _validate_digest_field(value: Any, label: str, *, computed_from: dict[str, Any]) -> None:
    _sha(value, label)
    if value != semantic_digest(computed_from):
        raise ValueError(f"{label} does not match the manifest body")


def validate_system_freeze_v2(value: Any) -> dict[str, Any]:
    """Validate the current v2 SUT and algorithm contract."""

    if not isinstance(value, dict):
        raise TypeError("system freeze v2 must be an object")
    if value.get("format_version") != SYSTEM_FREEZE_V2_FORMAT:
        raise ValueError("unsupported system freeze v2 format")
    if value.get("status") != "frozen":
        raise ValueError("system freeze v2 is not marked frozen")
    _require_identity(
        value.get("advisor"),
        "advisor",
        "1951123/extstats-advisor",
        FROZEN_ADVISOR_SHA,
    )
    _require_build_identity(
        value.get("patched_postgresql"),
        "patched_postgresql",
        "1951123/postgresql-pgextadv",
        FROZEN_PATCHED_POSTGRES_SHA,
    )
    _require_build_identity(
        value.get("stock_postgresql"),
        "stock_postgresql",
        "1951123/postgresql-src",
        FROZEN_STOCK_POSTGRES_SHA,
    )

    lab = value.get("postgres_lab")
    if not isinstance(lab, dict):
        raise TypeError("system freeze v2 is missing postgres_lab")
    if lab.get("identity_format") != "postgres-lab-instance-v1":
        raise ValueError("system freeze v2 has the wrong postgres-lab identity format")
    for field, expected in {
        "configure_features": CONFIGURE_FEATURES,
        "compiler_policy": {"CC": "cc", "CFLAGS": ""},
        "locale": "C.utf8",
        "encoding": "UTF8",
    }.items():
        if lab.get(field) != expected:
            raise ValueError(f"system freeze v2 postgres_lab {field} mismatch")
    if lab.get("fingerprint_requirements") != {
        "build_identity_source": "postgres-lab identity.json",
        "source_commit_sha_pinned": True,
        "ordinary_stats_fingerprint_required_per_artifact": True,
        "runtime_paths_are_not_semantic_identity": True,
    }:
        raise ValueError("system freeze v2 fingerprint requirements mismatch")

    algorithm = value.get("advisor_algorithm")
    if not isinstance(algorithm, dict):
        raise TypeError("system freeze v2 is missing advisor_algorithm")
    if algorithm.get("execution_optimizations") != list(EXECUTION_OPTIMIZATIONS):
        raise ValueError("system freeze v2 execution optimization identity mismatch")
    if algorithm.get("new_algorithmic_controls") != list(ALGORITHMIC_CONTROLS):
        raise ValueError("system freeze v2 algorithmic control identity mismatch")
    parameters = algorithm.get("canonical_parameters")
    if parameters != {
        "sample_rows": 10000,
        "sample_seed": 42,
        "statistics_target": 100,
        "K_s": 8,
        "B": 8,
        "T_seconds": 300,
    }:
        raise ValueError("system freeze v2 canonical parameters mismatch")
    if (
        algorithm.get("B_semantics")
        != "maximum selected candidate definitions; physical object count is separate"
    ):
        raise ValueError("system freeze v2 B semantics mismatch")
    if algorithm.get("strict_improvement") is not True:
        raise ValueError("system freeze v2 strict-improvement contract is missing")
    if (
        algorithm.get("partial_round_policy")
        != "discard incomplete round and retain last committed incumbent"
    ):
        raise ValueError("system freeze v2 partial-round policy mismatch")

    scope = value.get("validation_scope")
    if not isinstance(scope, dict):
        raise TypeError("system freeze v2 is missing validation_scope")
    if scope.get("singleton_equivalence_claim") != (
        "Power7 and Forest10 confirmatory evidence only; not exhaustive cross-dataset validation"
    ):
        raise ValueError("system freeze v2 singleton claim scope mismatch")
    for dataset in ("census13", "dmv11"):
        record = scope.get(dataset)
        if not isinstance(record, dict):
            raise TypeError(f"system freeze v2 is missing {dataset} scope record")
        if record.get("equivalence_status") != "not-executed":
            raise ValueError(f"system freeze v2 {dataset} must remain not-executed")
        if record.get("reason") != "scoped-out-by-v2-validation-policy":
            raise ValueError(f"system freeze v2 {dataset} scope reason mismatch")

    evidence = value.get("compatibility_evidence")
    if (
        not isinstance(evidence, dict)
        or not evidence.get("power7_singleton")
        or not evidence.get("forest10_singleton")
        or not evidence.get("incremental_greedy_live")
    ):
        raise ValueError("system freeze v2 compatibility evidence is incomplete")
    if value.get("v1_compatibility", {}).get("historical_artifacts_remain_v1") is not True:
        raise ValueError("system freeze v2 does not preserve v1 artifact provenance")
    if value.get("change_policy") != (
        "Any production semantic change to the advisor or either PostgreSQL build requires a new system freeze; historical v1 artifacts retain their v1 provenance."
    ):
        raise ValueError("system freeze v2 change policy mismatch")

    body = dict(value)
    digest = body.pop("semantic_digest", None)
    _validate_digest_field(digest, "system freeze v2 semantic_digest", computed_from=body)
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": SYSTEM_FREEZE_V2_FORMAT,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "stock_postgres_commit_sha": FROZEN_STOCK_POSTGRES_SHA,
        "semantic_digest": digest,
    }


def load_system_freeze_v2(path: Path = DEFAULT_FREEZE_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_system_freeze_v2(value)
    return value


def validate_readiness_review(value: Any) -> dict[str, Any]:
    """Validate that every mandatory v2 readiness gate has auditable evidence."""

    if not isinstance(value, dict):
        raise TypeError("readiness review must be an object")
    if value.get("format_version") != READINESS_FORMAT:
        raise ValueError("unsupported system freeze v2 readiness format")
    if value.get("status") != "passed":
        raise ValueError("system freeze v2 readiness review is not passed")
    for field, expected in {
        "advisor_sha": FROZEN_ADVISOR_SHA,
        "research_review_source_sha": RESEARCH_REVIEW_SOURCE_SHA,
        "patched_postgresql_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "stock_postgresql_sha": FROZEN_STOCK_POSTGRES_SHA,
    }.items():
        if value.get(field) != expected:
            raise ValueError(f"readiness review {field} mismatch")
    for field in (
        "advisor_sha",
        "research_review_source_sha",
        "patched_postgresql_sha",
        "stock_postgresql_sha",
    ):
        _commit(value[field], field)

    mandatory = value.get("mandatory_gates")
    if not isinstance(mandatory, dict) or set(mandatory) != set(MANDATORY_GATES):
        raise ValueError("readiness review mandatory gate set is incomplete")
    for name in MANDATORY_GATES:
        gate = mandatory[name]
        if not isinstance(gate, dict) or gate.get("status") != "passed":
            raise ValueError(f"mandatory gate is not passed: {name}")
        evidence = gate.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"mandatory gate has no evidence: {name}")
        for item in evidence:
            if not isinstance(item, dict) or not item.get("path") or not item.get("claim"):
                raise ValueError(f"mandatory gate evidence is malformed: {name}")
            if "digest" in item:
                _sha(item["digest"], f"{name} evidence digest")

    out_of_scope = value.get("out_of_scope_validation")
    if not isinstance(out_of_scope, dict):
        raise TypeError("readiness review is missing out_of_scope_validation")
    for name in OUT_OF_SCOPE_DATASETS:
        item = out_of_scope.get(name)
        if not isinstance(item, dict):
            raise TypeError(f"readiness review is missing {name} scope record")
        if item.get("equivalence_status") != "not-executed":
            raise ValueError(f"{name} is not explicitly out of scope")
        if item.get("reason") != "scoped-out-by-v2-validation-policy":
            raise ValueError(f"{name} scope reason is not frozen")
    if value.get("blockers") != []:
        raise ValueError("readiness review has unresolved blockers")
    if value.get("freeze_decision") != "create-system-freeze-v2":
        raise ValueError("readiness review does not approve the conditional freeze")
    if not value.get("known_limitations"):
        raise ValueError("readiness review must retain known limitations")
    provenance = mandatory["code_provenance"]
    if not isinstance(provenance, dict):
        raise TypeError("readiness review is missing code_provenance")
    if provenance.get("production_semantics_changed") is not False:
        raise ValueError("readiness review reports a production semantic change")
    if provenance.get("changed_paths") != ["tests/integration/test_postgres_native_stats.py"]:
        raise ValueError("readiness review code diff is broader than the test-only successor")
    if provenance.get("test_only_successor_relationship") is not True:
        raise ValueError("readiness review lacks the test-only successor relationship")

    ci = mandatory["CI"].get("ci_runs")
    if (
        not isinstance(ci, list)
        or not ci
        or any(item.get("conclusion") != "success" for item in ci if isinstance(item, dict))
    ):
        raise ValueError("readiness review CI evidence is incomplete")

    body = dict(value)
    digest = body.pop("artifact_digest", None)
    _validate_digest_field(digest, "readiness review artifact_digest", computed_from=body)
    reject_credentials(value)
    return {
        "status": "valid",
        "format_version": READINESS_FORMAT,
        "artifact_digest": digest,
        "mandatory_gate_count": len(MANDATORY_GATES),
    }


def load_readiness_review(path: Path = DEFAULT_READINESS_PATH) -> dict[str, Any]:
    value = read_json(path)
    validate_readiness_review(value)
    return value


def validate_readiness_evidence(
    path: Path = DEFAULT_READINESS_PATH,
    *,
    research_root: Path = RESEARCH_ROOT,
) -> dict[str, Any]:
    """Validate the local evidence named by the readiness review.

    This is intentionally artifact-only validation.  It loads committed
    manifests and semantic evidence; it does not start PostgreSQL or rerun a
    benchmark.
    """

    review = load_readiness_review(path)
    from .incremental_search_hardening import validate_artifact as validate_hardening
    from .singleton_equivalence import (
        validate_artifact as validate_singleton,
    )
    from .singleton_equivalence import (
        validate_historical_incremental_artifact,
    )
    from .system_freeze import load_system_freeze, validate_system_freeze

    power_path = research_root / (
        "experiments/arecel-power7/singleton-equivalence/"
        "advisor-singleton-incremental-equivalence-v1.json"
    )
    forest_path = research_root / (
        "experiments/arecel-forest10/singleton-equivalence/"
        "advisor-singleton-incremental-historical-equivalence-v2.json"
    )
    hardening_path = research_root / (
        "experiments/rq4/integration-smoke/advisor-greedy-incremental-hardening-v2.json"
    )
    old_greedy_path = research_root / (
        "experiments/rq4/integration-smoke/advisor-greedy-incremental-equivalence-v1.json"
    )
    power = validate_singleton(power_path)
    forest = validate_historical_incremental_artifact(forest_path)
    hardening = validate_hardening(hardening_path)
    old_greedy = _validate_artifact_digest(old_greedy_path)
    validate_system_freeze(load_system_freeze())

    power_value = read_json(power_path)
    forest_value = read_json(forest_path)
    hardening_value = read_json(hardening_path)
    if power_value["equivalence"]["audit"]["candidate_count"] != 42:
        raise ValueError("Power7 singleton evidence candidate count changed")
    if power_value["incremental"]["runtime_metadata"]["planner_query_estimate_count"] != 169042:
        raise ValueError("Power7 singleton evidence call count changed")
    if (
        power_value["incremental"]["runtime_metadata"][
            "full_workload_reference_planner_query_estimate_count"
        ]
        != 430000
    ):
        raise ValueError("Power7 singleton reference call count changed")
    if len(forest_value["equivalence"]["audit"]["records"]) != 57:
        raise ValueError("Forest10 singleton evidence candidate count changed")
    if forest_value["incremental"]["runtime_metadata"]["planner_query_estimate_count"] != 220640:
        raise ValueError("Forest10 singleton evidence call count changed")
    if (
        forest_value["incremental"]["runtime_metadata"][
            "full_workload_reference_planner_query_estimate_count"
        ]
        != 580000
    ):
        raise ValueError("Forest10 singleton reference call count changed")
    if hardening_value["advisor"]["commit_sha"] != ("ff6ec2dceaa95e4491d1f707dda123be6b71dd86"):
        raise ValueError("hardening evidence producing Advisor SHA changed")
    if old_greedy["format_version"] != "advisor-greedy-incremental-equivalence-v1":
        raise ValueError("historical Greedy evidence format changed")

    historical_paths = [
        research_root
        / "experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json",
        research_root
        / "experiments/arecel-forest10/rq1-confirmatory/rq1-matched-comparison-v1.json",
        research_root / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json",
        research_root / "experiments/arecel-dmv11/rq1-confirmatory/rq1-matched-comparison-v1.json",
        research_root / "experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json",
        research_root / "experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json",
    ]
    for historical_path in historical_paths:
        _validate_artifact_digest(historical_path)
        value = read_json(historical_path)
        if value.get("system_freeze", {}).get("format_version") != "system-freeze-v1":
            raise ValueError(f"historical artifact was reassigned to v2: {historical_path}")

    return {
        "status": "valid",
        "readiness": review["artifact_digest"],
        "power7": power["artifact_digest"],
        "forest10": forest["artifact_digest"],
        "incremental_greedy": hardening["artifact_digest"],
        "historical_greedy": old_greedy["artifact_digest"],
    }


def _validate_artifact_digest(path: Path) -> dict[str, Any]:
    value = read_json(path)
    body = dict(value)
    key = "artifact_digest" if "artifact_digest" in body else "semantic_digest"
    digest = body.pop(key, None)
    if not isinstance(digest, str) or digest != semantic_digest(body):
        raise ValueError(f"artifact digest mismatch: {path}")
    return {"format_version": value.get("format_version"), key: digest}


__all__ = [
    "DEFAULT_FREEZE_PATH",
    "DEFAULT_READINESS_PATH",
    "FROZEN_ADVISOR_SHA",
    "FROZEN_PATCHED_POSTGRES_SHA",
    "FROZEN_STOCK_POSTGRES_SHA",
    "READINESS_FORMAT",
    "SYSTEM_FREEZE_V2_FORMAT",
    "load_readiness_review",
    "load_system_freeze_v2",
    "validate_readiness_evidence",
    "validate_readiness_review",
    "validate_system_freeze_v2",
]
