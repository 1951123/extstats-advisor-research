"""Validation for the resolved RQ4 native-payload-size baseline contract."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest
from .rq4_ablation import HISTORICAL_METHOD_ALIASES, canonical_method_id

FORMAT_VERSION = "rq4-dependency-baseline-definition-resolution-v1"
CANONICAL_METHOD_ID = "native-payload-size-top-k"
HISTORICAL_METHOD_ID = "dependency-correlation-top-k"
SOURCE_BLOCKER = "experiments/rq4-dependency-baseline-definition-blocker-v1.json"
SOURCE_BLOCKER_DIGEST = "5780f498f88df57a8663893228203b26c0a576f87e08d37dc1562a0157f332ba"
REJECTED_ALTERNATIVES = (
    "Pearson",
    "mutual information",
    "Cramer's V",
    "unspecified dependency coefficient",
)


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def validate_resolution_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    if artifact.get("format_version") != FORMAT_VERSION:
        raise ValueError("unsupported RQ4 baseline resolution format")
    if artifact.get("status") != "resolved":
        raise ValueError("RQ4 baseline resolution is not resolved")
    if artifact.get("semantic_digest") != semantic_digest(_payload(artifact)):
        raise ValueError("RQ4 baseline resolution semantic digest mismatch")

    source = artifact.get("source_blocker", {})
    if source.get("path") != SOURCE_BLOCKER or source.get("digest") != SOURCE_BLOCKER_DIGEST:
        raise ValueError("RQ4 baseline resolution does not bind the audited blocker")
    root = Path(__file__).resolve().parents[2]
    blocker = read_json(root / SOURCE_BLOCKER)
    if blocker.get("status") != "blocked":
        raise ValueError("source blocker was rewritten instead of preserved")
    if blocker.get("semantic_digest") != SOURCE_BLOCKER_DIGEST:
        raise ValueError("source blocker digest changed")

    decision = artifact.get("decision", {})
    if decision.get("canonical_method_id") != CANONICAL_METHOD_ID:
        raise ValueError("wrong canonical RQ4 method ID")
    if decision.get("score_definition") != (
        "score(c) = byte size of the sample-built native PostgreSQL extended-statistics payload"
    ):
        raise ValueError("wrong payload-size score definition")
    if decision.get("ranking_direction") != "descending payload_size; larger payload first":
        raise ValueError("wrong payload-size ranking direction")
    if decision.get("tie_breaking") != "canonical static candidate precedence, then candidate ID":
        raise ValueError("wrong payload-size tie-breaking")
    access = decision.get("information_access", {})
    if access.get("ground_truth_during_selection") is not False:
        raise ValueError("payload-size heuristic may not use GroundTruthSet")
    if access.get("planner_utility_during_selection") is not False:
        raise ValueError("payload-size heuristic may not use planner utility")
    if access.get("allowed_inputs") != [
        "eligible candidate ID",
        "NativeStatsRepository payload_size",
        "static candidate precedence",
    ]:
        raise ValueError("payload-size information-access policy mismatch")
    if access.get("forbidden_inputs") != [
        "GroundTruthSet",
        "q-error",
        "singleton utility",
        "SearchResult",
        "Greedy result",
        "final Recommendation",
        "full-data outcome",
        "RQ1/RQ2 outcome",
    ]:
        raise ValueError("payload-size forbidden-input policy mismatch")

    alias = artifact.get("historical_alias", {})
    if alias.get("old_method_id") != HISTORICAL_METHOD_ID:
        raise ValueError("historical alias ID mismatch")
    if alias.get("canonical_method_id") != CANONICAL_METHOD_ID:
        raise ValueError("historical alias target mismatch")
    if HISTORICAL_METHOD_ALIASES.get(HISTORICAL_METHOD_ID) != CANONICAL_METHOD_ID:
        raise ValueError("code alias mapping mismatch")
    if alias.get("old_artifacts_immutable") is not True:
        raise ValueError("historical artifacts are not marked immutable")
    if artifact.get("post_hoc_metric_selection") is not False:
        raise ValueError("resolution is incorrectly marked post-hoc metric selection")
    if tuple(artifact.get("explicitly_rejected_alternatives", ())) != REJECTED_ALTERNATIVES:
        raise ValueError("rejected alternative list mismatch")
    if artifact.get("formal_execution_permitted") is not True:
        raise ValueError("formal execution was not enabled by resolution")
    if artifact.get("reason") != (
        "Resolve the semantic naming mismatch without changing the algorithm used by the historical Forest10 experiment or the existing readiness smoke."
    ):
        raise ValueError("resolution reason is not explicit")
    if artifact.get("no_prior_formal_results_before_freeze") is not True:
        raise ValueError("resolution does not record the pre-freeze result boundary")
    if not isinstance(artifact.get("research_sha"), str) or len(artifact["research_sha"]) != 40:
        raise ValueError("resolution research SHA is missing")
    return {
        "status": "valid",
        "format_version": FORMAT_VERSION,
        "semantic_digest": artifact["semantic_digest"],
        "canonical_method_id": CANONICAL_METHOD_ID,
        "historical_method_id": HISTORICAL_METHOD_ID,
        "formal_execution_permitted": True,
    }


__all__ = [
    "CANONICAL_METHOD_ID",
    "FORMAT_VERSION",
    "HISTORICAL_METHOD_ID",
    "SOURCE_BLOCKER",
    "SOURCE_BLOCKER_DIGEST",
    "canonical_method_id",
    "validate_resolution_artifact",
]
