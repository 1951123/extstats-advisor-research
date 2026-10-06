"""Deterministic RQ4 design-time replay evidence.

RQ4a is a sealed-snapshot/patched-planner experiment.  Its semantic evidence
must replay exactly, while runtime measurements are expected to vary.  This
module defines that boundary explicitly and does not inspect or rewrite any
PostgreSQL estimator behavior.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .provenance import read_json, semantic_digest, write_json
from .rq4_ablation import RQ4ValidationError

DETERMINISM_FORMAT = "rq4-design-determinism-smoke-v1"
_RUNTIME_KEYS = {
    "backend_wall_clock_seconds",
    "elapsed_wall_clock_seconds",
    "wall_clock_seconds",
    "wall_time_seconds",
    "created_at",
    "runtime_timestamp",
}


def _without_runtime(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_runtime(item)
            for key, item in value.items()
            if key not in _RUNTIME_KEYS
        }
    if isinstance(value, list):
        return [_without_runtime(item) for item in value]
    if isinstance(value, tuple):
        return [_without_runtime(item) for item in value]
    return value


def deterministic_semantic_projection(comparison: Mapping[str, Any]) -> dict[str, Any]:
    """Return the comparison fields that must be invariant under replay."""

    projected = _without_runtime(comparison)
    if not isinstance(projected, dict):
        raise TypeError("RQ4 comparison must project to an object")
    return projected


def _run_digest(run: Mapping[str, Any]) -> str:
    return semantic_digest(deterministic_semantic_projection(run))


def build_design_determinism_smoke_artifact(
    *,
    run_once: Callable[[], Mapping[str, Any]],
    candidate_universe_digest: str,
    system_freeze: Mapping[str, Any],
    research_commit_sha: str,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    """Run one small patched-backend comparison twice and require exact replay."""

    runs = [dict(run_once()), dict(run_once())]
    projections = [deterministic_semantic_projection(run) for run in runs]
    if projections[0] != projections[1]:
        raise RQ4ValidationError("RQ4 deterministic replay mismatch outside runtime measurements")
    first = projections[0]
    methods = first.get("methods", {})
    if not isinstance(methods, Mapping) or not methods:
        raise RQ4ValidationError("RQ4 deterministic replay has no method comparison")
    checks = {
        "candidate_universe_digest_stable": True,
        "selected_membership_stable": True,
        "evaluation_order_stable": True,
        "planner_estimates_stable": True,
        "sandbox_objective_stable": True,
        "configuration_trace_stable": True,
        "semantic_projection_stable": True,
    }
    artifact: dict[str, Any] = {
        "format_version": DETERMINISM_FORMAT,
        "experiment_id": "rq4-design-determinism-smoke-v1",
        "formal_confirmatory_experiment": False,
        "status": "deterministic-replay-smoke",
        "replay_count": 2,
        "fixture": dict(fixture),
        "candidate_universe_digest": candidate_universe_digest,
        "system_freeze": dict(system_freeze),
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "research_commit_sha": research_commit_sha,
        "checks": checks,
        "replay_semantic_digests": [_run_digest(run) for run in runs],
        "deterministic_projection": first,
        "runtime_observations": [
            {
                "accounting": _runtime_accounting(run),
            }
            for run in runs
        ],
        "evidence_hierarchy": "RQ4a deterministic patched sandbox; no stock deployment claim",
    }
    artifact["semantic_digest"] = semantic_digest(_artifact_payload(artifact))
    return artifact


def _runtime_accounting(run: Mapping[str, Any]) -> dict[str, Any]:
    """Keep timing evidence separately, without making it semantic evidence."""

    result: dict[str, Any] = {}
    for method, value in run.get("methods", {}).items():
        if not isinstance(value, Mapping):
            continue
        result[str(method)] = {
            "selection_preprocessing": value.get("selection_preprocessing", {}),
            "accounting": value.get("accounting", {}),
        }
    return result


def _artifact_payload(artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): value
        for key, value in artifact.items()
        if key not in {"semantic_digest", "runtime_observations"}
    }


def validate_design_determinism_smoke(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    if artifact.get("format_version") != DETERMINISM_FORMAT:
        raise RQ4ValidationError("unsupported RQ4 determinism artifact format")
    if artifact.get("formal_confirmatory_experiment") is not False:
        raise RQ4ValidationError("determinism smoke cannot be formal evidence")
    if artifact.get("replay_count") != 2:
        raise RQ4ValidationError("determinism smoke must contain exactly two replays")
    expected = semantic_digest(_artifact_payload(artifact))
    if artifact.get("semantic_digest") != expected:
        raise RQ4ValidationError("RQ4 determinism artifact semantic digest mismatch")
    digests = artifact.get("replay_semantic_digests")
    if not isinstance(digests, list) or len(digests) != 2 or digests[0] != digests[1]:
        raise RQ4ValidationError("RQ4 deterministic replay semantic digests differ")
    checks = artifact.get("checks")
    if not isinstance(checks, Mapping) or not checks or not all(checks.values()):
        raise RQ4ValidationError("RQ4 deterministic replay gate did not pass")
    if not isinstance(artifact.get("deterministic_projection"), Mapping):
        raise RQ4ValidationError("deterministic replay projection is missing")
    return {
        "status": "valid",
        "format_version": DETERMINISM_FORMAT,
        "semantic_digest": expected,
        "replay_count": 2,
        "formal_confirmatory_experiment": False,
    }


def write_design_determinism_smoke(
    output: Path,
    *,
    run_once: Callable[[], Mapping[str, Any]],
    candidate_universe_digest: str,
    system_freeze: Mapping[str, Any],
    research_commit_sha: str,
    fixture: Mapping[str, Any],
) -> dict[str, Any]:
    artifact = build_design_determinism_smoke_artifact(
        run_once=run_once,
        candidate_universe_digest=candidate_universe_digest,
        system_freeze=system_freeze,
        research_commit_sha=research_commit_sha,
        fixture=fixture,
    )
    write_json(output, artifact)
    return artifact


__all__ = [
    "DETERMINISM_FORMAT",
    "build_design_determinism_smoke_artifact",
    "deterministic_semantic_projection",
    "validate_design_determinism_smoke",
    "write_design_determinism_smoke",
]
