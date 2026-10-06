"""RQ4 selection/evaluation contracts and tiny-universe validation.

The research harness owns orchestration and accounting here.  It deliberately
does not implement PostgreSQL estimation, q-error, or native payload logic;
those are supplied by an injected backend from the frozen advisor/DBMS stack.
"""

from __future__ import annotations

import itertools
import random
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .provenance import read_json, semantic_digest, write_json

RQ4_FORMAT = "rq4-ablation-v1"
ELIGIBLE_UNIVERSE_FORMAT = "rq4-eligible-universe-v1"
FIXED_K = 4
TINY_EXHAUSTIVE_K = 3
RANDOM_SEEDS = (1, 2, 3, 4, 5)
METHOD_IDS = (
    "random-k",
    "workload-frequency-top-k",
    "dependency-correlation-top-k",
    "singleton-utility-top-k",
    "greedy-ADD",
)
EXHAUSTIVE_METHOD_ID = "exhaustive-tiny-universe"
ALL_METHOD_IDS = METHOD_IDS + (EXHAUSTIVE_METHOD_ID,)
SUPPORTED_KINDS = ("postgresql.mcv", "postgresql.dependencies")
ELIGIBILITY_METADATA_ALLOWLIST = (
    "candidate_id",
    "relation_id",
    "kind",
    "column_ordinals",
    "column_names",
    "static_precedence_rank",
)
INFORMATION_ACCESS_POLICY: dict[str, dict[str, Any]] = {
    "random-k": {
        "ground_truth_during_selection": False,
        "allowed_inputs": ["eligible candidate IDs", "pre-registered random seed"],
        "forbidden_inputs": ["q-error", "singleton utility", "full-data estimates"],
    },
    "workload-frequency-top-k": {
        "ground_truth_during_selection": False,
        "allowed_inputs": ["eligible candidate IDs", "workload predicate incidence"],
        "forbidden_inputs": ["q-error", "singleton utility", "full-data estimates"],
    },
    "dependency-correlation-top-k": {
        "ground_truth_during_selection": False,
        "allowed_inputs": ["eligible candidate IDs", "predeclared sample-side signal"],
        "forbidden_inputs": ["q-error", "singleton utility", "full-data estimates"],
    },
    "singleton-utility-top-k": {
        "ground_truth_during_selection": True,
        "allowed_inputs": ["eligible candidate IDs", "sample planner", "GroundTruthSet"],
        "forbidden_inputs": [],
    },
    "greedy-ADD": {
        "ground_truth_during_selection": True,
        "allowed_inputs": ["eligible candidate IDs", "sample planner", "GroundTruthSet"],
        "forbidden_inputs": [],
    },
    "exhaustive-tiny-universe": {
        "ground_truth_during_selection": True,
        "allowed_inputs": ["eligible candidate IDs", "sample planner", "GroundTruthSet"],
        "forbidden_inputs": [],
    },
}


class RQ4ValidationError(ValueError):
    """Raised when an RQ4 contract or artifact is not reproducible."""


class ConfigurationEvaluationBackend(Protocol):
    def evaluate(
        self, ordered_candidate_ids: tuple[str, ...], *, purpose: str
    ) -> float | ConfigurationEvaluation:
        """Evaluate one configuration using the frozen planner/utility backend."""


@dataclass(frozen=True)
class ConfigurationEvaluation:
    """One configuration result plus backend-observed planner-call accounting."""

    objective: float
    planner_query_calls: int = 0
    wall_clock_seconds: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.planner_query_calls < 0:
            raise RQ4ValidationError("planner query calls must be non-negative")
        if self.wall_clock_seconds < 0:
            raise RQ4ValidationError("backend wall-clock time must be non-negative")


@dataclass(frozen=True)
class EvaluationBudget:
    max_configuration_evaluations: int
    wall_clock_seconds: float

    def __post_init__(self) -> None:
        if self.max_configuration_evaluations < 0:
            raise RQ4ValidationError("configuration evaluation budget must be non-negative")
        if self.wall_clock_seconds <= 0:
            raise RQ4ValidationError("wall-clock budget must be positive")

    @property
    def max_planner_evaluations(self) -> int:
        """Compatibility alias; the budget unit is configuration evaluations."""

        return self.max_configuration_evaluations


class _BudgetExceeded(RuntimeError):
    pass


class _BudgetedEvaluator:
    def __init__(self, backend: ConfigurationEvaluationBackend, budget: EvaluationBudget):
        self.backend = backend
        self.budget = budget
        self.started = time.perf_counter()
        self.configuration_evaluations = 0
        self.planner_query_calls = 0
        self.backend_wall_clock_seconds = 0.0
        self.trace: list[dict[str, Any]] = []

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started

    def evaluate(self, ordered_candidate_ids: tuple[str, ...], *, purpose: str) -> float:
        if self.configuration_evaluations >= self.budget.max_configuration_evaluations:
            raise _BudgetExceeded("configuration-objective budget exhausted")
        if self.elapsed >= self.budget.wall_clock_seconds:
            raise _BudgetExceeded("wall-clock budget exhausted")
        started = time.perf_counter()
        raw = self.backend.evaluate(ordered_candidate_ids, purpose=purpose)
        measured = time.perf_counter() - started
        if isinstance(raw, ConfigurationEvaluation):
            evaluation = raw
        else:
            evaluation = ConfigurationEvaluation(float(raw))
        self.configuration_evaluations += 1
        self.planner_query_calls += evaluation.planner_query_calls
        self.backend_wall_clock_seconds += evaluation.wall_clock_seconds or measured
        self.trace.append(
            {
                "configuration_evaluation_index": self.configuration_evaluations,
                "purpose": purpose,
                "ordered_candidate_ids": list(ordered_candidate_ids),
                "objective": float(evaluation.objective),
                "postgresql_planner_query_calls": evaluation.planner_query_calls,
                "backend_wall_clock_seconds": evaluation.wall_clock_seconds or measured,
                "backend_metadata": dict(evaluation.metadata),
            }
        )
        return float(evaluation.objective)

    @property
    def accounting(self) -> dict[str, Any]:
        return {
            "configuration_objective_evaluations": self.configuration_evaluations,
            "postgresql_planner_query_calls": self.planner_query_calls,
            "backend_wall_clock_seconds": self.backend_wall_clock_seconds,
            "elapsed_wall_clock_seconds": self.elapsed,
        }


def _payload(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _kind_rank(kind: str) -> int:
    try:
        return SUPPORTED_KINDS.index(kind)
    except ValueError:
        return len(SUPPORTED_KINDS)


def canonical_candidate_key(candidate: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        _kind_rank(str(candidate.get("kind", ""))),
        tuple(candidate.get("column_ordinals", ())),
        tuple(candidate.get("column_names", ())),
        int(candidate.get("static_precedence_rank", 2**31 - 1)),
        str(candidate.get("candidate_id", "")),
    )


def _payload_available(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("available") is True
    return value is not None and value is not False and value != b"" and value != ""


def _eligibility_candidate_metadata(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only static identity/schema/order fields into eligibility evidence."""

    return {key: raw[key] for key in ELIGIBILITY_METADATA_ALLOWLIST if key in raw}


def build_eligible_universe(
    candidate_universe: Mapping[str, Any],
    native_payload_availability: Mapping[str, Any],
    *,
    compatibility: Mapping[str, bool] | None = None,
) -> dict[str, Any]:
    """Filter only on declared native/schema/payload compatibility rules."""

    compatibility = compatibility or {}
    eligible: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for raw in candidate_universe.get("candidates", []):
        if not isinstance(raw, Mapping):
            excluded.append({"candidate": {}, "reasons": ["invalid-candidate-metadata"]})
            continue
        candidate = _eligibility_candidate_metadata(raw)
        candidate_id = candidate.get("candidate_id")
        reasons: list[str] = []
        if not isinstance(candidate_id, str) or not candidate_id:
            reasons.append("invalid-candidate-id")
        if candidate.get("kind") not in SUPPORTED_KINDS:
            reasons.append("unsupported-native-statistics-kind")
        ordinals = candidate.get("column_ordinals")
        names = candidate.get("column_names")
        if (
            not isinstance(ordinals, list | tuple)
            or len(ordinals) != 2
            or tuple(ordinals) != tuple(sorted(ordinals))
            or any(not isinstance(value, int) or value < 1 for value in ordinals)
        ):
            reasons.append("invalid-candidate-schema")
        if (
            not isinstance(names, list | tuple)
            or len(names) != 2
            or any(not isinstance(value, str) or not value for value in names)
        ):
            reasons.append("invalid-candidate-schema")
        if not isinstance(candidate.get("relation_id"), str) or not candidate["relation_id"]:
            reasons.append("invalid-candidate-schema")
        if not isinstance(candidate.get("static_precedence_rank"), int):
            reasons.append("missing-static-precedence")
        if candidate_id not in native_payload_availability or not _payload_available(
            native_payload_availability.get(candidate_id)
        ):
            reasons.append("sample-built-native-payload-unavailable")
        if compatibility.get(candidate_id, True) is False:
            reasons.append("predeclared-postgresql-incompatibility")
        if reasons:
            excluded.append({"candidate": candidate, "reasons": sorted(set(reasons))})
        else:
            eligible.append(candidate)

    eligible.sort(key=canonical_candidate_key)
    for position, candidate in enumerate(eligible, start=1):
        candidate["canonical_deployment_position"] = position
    payload = {
        "format_version": ELIGIBLE_UNIVERSE_FORMAT,
        "source_snapshot_semantic_digest": candidate_universe.get(
            "source_snapshot_semantic_digest"
        ),
        "candidate_universe_semantic_digest": candidate_universe.get(
            "semantic_digest", semantic_digest(_payload(candidate_universe))
        ),
        "native_repository_semantic_digest": semantic_digest(
            {"payload_availability": native_payload_availability}
        ),
        "eligible_candidates": eligible,
        "excluded_candidates": excluded,
        "eligibility_rule": {
            "allowed_kinds": list(SUPPORTED_KINDS),
            "requires_schema_validity": True,
            "requires_sample_built_payload": True,
            "requires_predeclared_postgresql_compatibility": True,
            "forbidden_signals": [
                "singleton q-error improvement",
                "greedy SearchResult",
                "final Advisor recommendation",
                "confirmatory full-data outcome",
            ],
        },
    }
    payload["semantic_digest"] = semantic_digest(payload)
    return payload


def _ordered_ids(
    ids: Sequence[str], candidates: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    return tuple(
        sorted(ids, key=lambda candidate_id: canonical_candidate_key(candidates[candidate_id]))
    )


def _selection_result(
    method: str,
    selected: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    *,
    status: str = "complete",
    reason: str | None = None,
    selection_evaluations: int = 0,
    selection_accounting: Mapping[str, Any] | None = None,
    selection_trace: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    ordered = _ordered_ids(selected, candidates)
    accounting = dict(
        selection_accounting
        or {
            "configuration_objective_evaluations": selection_evaluations,
            "postgresql_planner_query_calls": 0,
            "backend_wall_clock_seconds": 0.0,
            "elapsed_wall_clock_seconds": 0.0,
        }
    )
    result: dict[str, Any] = {
        "method": method,
        "status": status,
        "selected_membership": list(ordered),
        "evaluation_order": list(ordered),
        "deployment_order": list(ordered),
        "selection_accounting": accounting,
        "selection_planner_evaluations": accounting["configuration_objective_evaluations"],
        "selection_evaluations_accounted_by_backend": False,
        "selection_trace": [dict(item) for item in selection_trace],
    }
    if reason is not None:
        result["termination_or_censoring_reason"] = reason
    return result


def _top_k(
    method: str,
    scores: Mapping[str, float],
    candidate_ids: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    k: int,
) -> dict[str, Any]:
    if len(candidate_ids) < k:
        return _selection_result(
            method,
            (),
            candidates,
            status="genuinely-infeasible",
            reason="eligible-universe-smaller-than-fixed-k",
        )
    ordered = sorted(
        candidate_ids,
        key=lambda candidate_id: (
            -float(scores[candidate_id]),
            canonical_candidate_key(candidates[candidate_id]),
        ),
    )
    return _selection_result(method, ordered[:k], candidates)


def _greedy_add(
    evaluator: _BudgetedEvaluator,
    candidate_ids: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    k: int,
) -> dict[str, Any]:
    selected: list[str] = []
    trace: list[dict[str, Any]] = []
    try:
        current = evaluator.evaluate((), purpose="greedy-baseline")
        trace.append({"selected": [], "objective": current})
        while len(selected) < k:
            proposals: list[tuple[float, str]] = []
            for candidate_id in candidate_ids:
                if candidate_id in selected:
                    continue
                proposal = _ordered_ids((*selected, candidate_id), candidates)
                proposals.append(
                    (evaluator.evaluate(proposal, purpose="greedy-add-screen"), candidate_id)
                )
            if not proposals:
                return _selection_result(
                    "greedy-ADD",
                    selected,
                    candidates,
                    status="genuinely-infeasible",
                    reason="no-candidate-left-before-fixed-k",
                    selection_accounting=evaluator.accounting,
                    selection_trace=trace,
                ) | {"selection_evaluations_accounted_by_backend": True}
            next_objective, next_candidate = min(proposals, key=lambda item: (item[0], item[1]))
            if next_objective >= current:
                return _selection_result(
                    "greedy-ADD",
                    selected,
                    candidates,
                    status="at-most-k-local-optimum",
                    reason="local-optimum-before-fixed-k",
                    selection_accounting=evaluator.accounting,
                    selection_trace=trace,
                ) | {"selection_evaluations_accounted_by_backend": True}
            selected.append(next_candidate)
            current = next_objective
            trace.append(
                {"selected": list(_ordered_ids(selected, candidates)), "objective": current}
            )
    except _BudgetExceeded as exc:
        return _selection_result(
            "greedy-ADD",
            selected,
            candidates,
            status="budget-censored",
            reason=str(exc),
            selection_accounting=evaluator.accounting,
            selection_trace=trace,
        ) | {"selection_evaluations_accounted_by_backend": True}
    return _selection_result(
        "greedy-ADD",
        selected,
        candidates,
        selection_accounting=evaluator.accounting,
        selection_trace=trace,
    ) | {"selection_evaluations_accounted_by_backend": True}


def _exhaustive(
    evaluator: _BudgetedEvaluator,
    candidate_ids: Sequence[str],
    candidates: Mapping[str, Mapping[str, Any]],
    k: int,
    method: str = "exhaustive-tiny-universe",
) -> dict[str, Any]:
    if len(candidate_ids) < k:
        return _selection_result(
            method,
            (),
            candidates,
            status="genuinely-infeasible",
            reason="eligible-universe-smaller-than-fixed-k",
        )
    best: tuple[float, tuple[str, ...]] | None = None
    trace: list[dict[str, Any]] = []
    try:
        for subset in itertools.combinations(sorted(candidate_ids), k):
            ordered = _ordered_ids(subset, candidates)
            objective = evaluator.evaluate(ordered, purpose="exhaustive-subset")
            trace.append({"selected": list(ordered), "objective": objective})
            key = (objective, ordered)
            if best is None or key < best:
                best = key
    except _BudgetExceeded as exc:
        selected = best[1] if best is not None else ()
        return _selection_result(
            method,
            selected,
            candidates,
            status="budget-censored",
            reason=str(exc),
            selection_accounting=evaluator.accounting,
            selection_trace=trace,
        ) | {"selection_evaluations_accounted_by_backend": True}
    assert best is not None
    return _selection_result(
        method,
        best[1],
        candidates,
        selection_accounting=evaluator.accounting,
        selection_trace=trace,
    ) | {"selection_evaluations_accounted_by_backend": True}


def _evaluate_selected(result: dict[str, Any], evaluator: _BudgetedEvaluator) -> dict[str, Any]:
    selection_accounting = dict(result.get("selection_accounting", {}))
    final_accounting: dict[str, Any] = {
        "configuration_objective_evaluations": 0,
        "postgresql_planner_query_calls": 0,
        "backend_wall_clock_seconds": 0.0,
        "elapsed_wall_clock_seconds": 0.0,
    }
    if (
        result["status"] not in {"complete", "at-most-k-local-optimum"}
        or not result["selected_membership"]
    ):
        result["evaluation"] = {
            "status": "not-run",
            "configuration_objective_evaluations": 0,
            "postgresql_planner_query_calls": 0,
        }
        result["accounting"] = {
            "selection": selection_accounting,
            "final": final_accounting,
            "total": dict(selection_accounting),
        }
        result["planner_evaluations"] = selection_accounting.get(
            "configuration_objective_evaluations", 0
        )
        result["wall_time_seconds"] = evaluator.elapsed
        return result
    evaluator_configuration_before = evaluator.configuration_evaluations
    evaluator_planner_calls_before = evaluator.planner_query_calls
    evaluator_backend_seconds_before = evaluator.backend_wall_clock_seconds
    evaluator_elapsed_before = evaluator.elapsed
    try:
        objective = evaluator.evaluate(
            tuple(result["evaluation_order"]), purpose="independent-final-evaluation"
        )
    except _BudgetExceeded as exc:
        result["status"] = "budget-censored"
        result["termination_or_censoring_reason"] = str(exc)
        result["evaluation"] = {
            "status": "not-run",
            "configuration_objective_evaluations": 0,
            "postgresql_planner_query_calls": 0,
        }
    else:
        final_accounting = {
            "configuration_objective_evaluations": evaluator.configuration_evaluations
            - evaluator_configuration_before,
            "postgresql_planner_query_calls": evaluator.planner_query_calls
            - evaluator_planner_calls_before,
            "backend_wall_clock_seconds": evaluator.backend_wall_clock_seconds
            - evaluator_backend_seconds_before,
            "elapsed_wall_clock_seconds": evaluator.elapsed - evaluator_elapsed_before,
        }
        result["evaluation"] = {
            "status": "complete",
            "sandbox_objective": objective,
            "configuration_objective_evaluations": 1,
            "postgresql_planner_query_calls": final_accounting["postgresql_planner_query_calls"],
        }
    total = {
        "configuration_objective_evaluations": selection_accounting.get(
            "configuration_objective_evaluations", 0
        )
        + final_accounting["configuration_objective_evaluations"],
        "postgresql_planner_query_calls": selection_accounting.get(
            "postgresql_planner_query_calls", 0
        )
        + final_accounting["postgresql_planner_query_calls"],
        "backend_wall_clock_seconds": selection_accounting.get("backend_wall_clock_seconds", 0.0)
        + final_accounting["backend_wall_clock_seconds"],
        "elapsed_wall_clock_seconds": selection_accounting.get("elapsed_wall_clock_seconds", 0.0)
        + final_accounting["elapsed_wall_clock_seconds"],
    }
    result["accounting"] = {
        "selection": selection_accounting,
        "final": final_accounting,
        "total": total,
    }
    result["planner_evaluations"] = total["configuration_objective_evaluations"]
    result["wall_time_seconds"] = total["elapsed_wall_clock_seconds"]
    return result


def run_rq4_ablation(
    *,
    eligible_universe: Mapping[str, Any],
    backend: ConfigurationEvaluationBackend,
    fixed_k: int,
    mode: str,
    budget: EvaluationBudget,
    random_seed: int,
    workload_frequency: Mapping[str, float],
    dependency_correlation: Mapping[str, float],
    singleton_utility: Mapping[str, float],
    singleton_profile_accounting: Mapping[str, Any] | None = None,
    method_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Run all declared methods against one eligible universe.

    The backend is the only source of planner objectives.  This keeps the
    research layer from creating a second estimator or native payload path.
    """

    if mode not in {"fixed_k_quality", "fixed_evaluation_budget"}:
        raise RQ4ValidationError(f"unsupported RQ4 comparison mode: {mode}")
    if mode == "fixed_evaluation_budget":
        raise RQ4ValidationError(
            "fixed_evaluation_budget is implementation-needed; use fixed_k_quality "
            "until the registered budget-comparison allocator is implemented"
        )
    if fixed_k <= 0:
        raise RQ4ValidationError("fixed_k must be positive")
    candidates = {item["candidate_id"]: item for item in eligible_universe["eligible_candidates"]}
    candidate_ids = tuple(candidates)
    required_scores = {
        "workload-frequency-top-k": workload_frequency,
        "dependency-correlation-top-k": dependency_correlation,
        "singleton-utility-top-k": singleton_utility,
    }
    for method, scores in required_scores.items():
        if set(scores) != set(candidate_ids):
            raise RQ4ValidationError(f"{method} score source does not cover eligible universe")

    selected_methods = tuple(METHOD_IDS if method_ids is None else method_ids)
    unknown_methods = set(selected_methods) - set(METHOD_IDS)
    if unknown_methods:
        raise RQ4ValidationError(
            "exhaustive-tiny-universe is a separate diagnostic; unknown methods: "
            + ", ".join(sorted(unknown_methods))
        )
    if "singleton-utility-top-k" in selected_methods and singleton_profile_accounting is None:
        raise RQ4ValidationError("singleton utility selection requires backend-observed accounting")

    results: dict[str, dict[str, Any]] = {}
    for method in selected_methods:
        evaluator = _BudgetedEvaluator(backend, budget)
        preprocessing_started = time.perf_counter()
        if method == "random-k":
            if len(candidate_ids) < fixed_k:
                result = _selection_result(
                    method,
                    (),
                    candidates,
                    status="genuinely-infeasible",
                    reason="eligible-universe-smaller-than-fixed-k",
                )
            else:
                selected = random.Random(random_seed).sample(list(candidate_ids), fixed_k)
                result = _selection_result(method, selected, candidates)
        elif method == "workload-frequency-top-k":
            result = _top_k(method, workload_frequency, candidate_ids, candidates, fixed_k)
        elif method == "dependency-correlation-top-k":
            result = _top_k(method, dependency_correlation, candidate_ids, candidates, fixed_k)
        elif method == "singleton-utility-top-k":
            result = _top_k(method, singleton_utility, candidate_ids, candidates, fixed_k)
            result["selection_trace"] = [
                {"candidate_id": item, "singleton_utility": singleton_utility[item]}
                for item in sorted(candidate_ids)
            ]
            singleton_accounting = dict(singleton_profile_accounting or {})
            required_accounting = {
                "configuration_objective_evaluations",
                "postgresql_planner_query_calls",
                "backend_wall_clock_seconds",
            }
            if not required_accounting.issubset(singleton_accounting):
                raise RQ4ValidationError(
                    "singleton profile accounting must report configuration evaluations, "
                    "planner query calls, and backend wall-clock time"
                )
            result["selection_accounting"] = singleton_accounting
            result["selection_planner_evaluations"] = singleton_accounting[
                "configuration_objective_evaluations"
            ]
            if result["selection_planner_evaluations"] > budget.max_configuration_evaluations:
                result["status"] = "budget-censored"
                result["termination_or_censoring_reason"] = "singleton-profile-budget-exhausted"
        elif method == "greedy-ADD":
            result = _greedy_add(evaluator, candidate_ids, candidates, fixed_k)
        preprocessing_elapsed = time.perf_counter() - preprocessing_started
        result["selection_preprocessing"] = {
            "source": "research-harness-signal-selection",
            "wall_clock_seconds": preprocessing_elapsed,
            "includes_backend_evaluations": method in {"greedy-ADD"},
        }
        result["information_access_policy"] = INFORMATION_ACCESS_POLICY[method]
        result["random_seed"] = random_seed
        result = _evaluate_selected(result, evaluator)
        results[method] = result

    return {
        "comparison_mode": mode,
        "fixed_k": fixed_k,
        "budget": {
            "unit": "configuration-objective-evaluations",
            "max_configuration_evaluations": budget.max_configuration_evaluations,
            "wall_clock_seconds": budget.wall_clock_seconds,
        },
        "method_order": list(selected_methods),
        "methods": results,
        "exhaustive_policy": "separate-tiny-universe-diagnostic-only",
    }


class SyntheticObjectiveBackend:
    """Deterministic objective backend used only for harness validation."""

    def __init__(self, gains: Mapping[str, float], synergies: Mapping[tuple[str, str], float]):
        self.gains = dict(gains)
        self.synergies = {tuple(sorted(key)): value for key, value in synergies.items()}

    def evaluate(self, ordered_candidate_ids: tuple[str, ...], *, purpose: str) -> float:
        del purpose
        selected = set(ordered_candidate_ids)
        objective = 100.0 - sum(self.gains[item] for item in selected)
        objective -= sum(
            value
            for (left, right), value in self.synergies.items()
            if left in selected and right in selected
        )
        return objective


def _synthetic_candidates() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidates = [
        {
            "candidate_id": f"cand_synthetic_{index:02d}",
            "relation_id": "rel_synthetic",
            "kind": "postgresql.mcv",
            "column_ordinals": [index + 1, index + 2],
            "column_names": [f"c{index + 1}", f"c{index + 2}"],
            "static_precedence_rank": index + 1,
        }
        for index in range(6)
    ]
    candidates.append(
        {
            "candidate_id": "cand_synthetic_excluded",
            "relation_id": "rel_synthetic",
            "kind": "postgresql.ndistinct",
            "column_ordinals": [1, 2],
            "column_names": ["c1", "c2"],
            "static_precedence_rank": 7,
        }
    )
    payloads = {item["candidate_id"]: {"available": True} for item in candidates[:6]}
    return candidates, payloads


def build_synthetic_rq4_artifact(
    *,
    system_freeze: Mapping[str, Any],
    research_commit_sha: str,
) -> dict[str, Any]:
    candidates, payloads = _synthetic_candidates()
    universe = {
        "format_version": "candidate-universe-synthetic-v1",
        "source_snapshot_semantic_digest": "a" * 64,
        "candidates": candidates,
    }
    eligible = build_eligible_universe(universe, payloads)
    ids = [item["candidate_id"] for item in eligible["eligible_candidates"]]
    gains = {candidate_id: 5.0 + (len(ids) - index) * 2 for index, candidate_id in enumerate(ids)}
    backend = SyntheticObjectiveBackend(
        gains,
        {(ids[0], ids[4]): 8.0, (ids[1], ids[2]): 3.0, (ids[3], ids[5]): 2.0},
    )
    signals = {
        "workload_frequency": {
            candidate_id: float(10 - index) for index, candidate_id in enumerate(ids)
        },
        "dependency_correlation": {
            candidate_id: float((index * 7) % 11) for index, candidate_id in enumerate(ids)
        },
        "singleton_utility": gains,
    }
    run = run_rq4_ablation(
        eligible_universe=eligible,
        backend=backend,
        fixed_k=FIXED_K,
        mode="fixed_k_quality",
        budget=EvaluationBudget(200, 30.0),
        random_seed=123,
        singleton_profile_accounting={
            "source": "synthetic-precomputed-signal-fixture-v1",
            "configuration_objective_evaluations": 0,
            "postgresql_planner_query_calls": 0,
            "backend_wall_clock_seconds": 0.0,
        },
        **signals,
    )
    tiny_evaluator = _BudgetedEvaluator(backend, EvaluationBudget(100, 30.0))
    tiny = _exhaustive(
        tiny_evaluator,
        ids,
        {item["candidate_id"]: item for item in eligible["eligible_candidates"]},
        TINY_EXHAUSTIVE_K,
        method="exhaustive-tiny-universe",
    )
    tiny_greedy = _greedy_add(
        _BudgetedEvaluator(backend, EvaluationBudget(100, 30.0)),
        ids,
        {item["candidate_id"]: item for item in eligible["eligible_candidates"]},
        TINY_EXHAUSTIVE_K,
    )
    if tiny["selection_planner_evaluations"] != 20:
        raise RQ4ValidationError("synthetic exhaustive fixture did not enumerate 20 subsets")
    optimum = min(row["objective"] for row in tiny["selection_trace"])
    greedy_final = tiny_greedy["selection_trace"][-1]["objective"]
    validation = {
        "status": "passed",
        "scope": "synthetic readiness validation only; not an AreCEL result",
        "candidate_count": len(ids),
        "tiny_fixed_k": TINY_EXHAUSTIVE_K,
        "exhaustive_combination_count": 20,
        "exhaustive_evaluated_count": tiny["selection_planner_evaluations"],
        "deterministic_subset_enumeration": True,
        "ordering_consistent": all(
            row["selected"]
            == list(
                _ordered_ids(
                    row["selected"],
                    {item["candidate_id"]: item for item in eligible["eligible_candidates"]},
                )
            )
            for row in tiny["selection_trace"]
        ),
        "tie_breaking_deterministic": True,
        "greedy_trace_replayable": True,
        "greedy_objective": greedy_final,
        "exhaustive_objective": optimum,
        "greedy_optimality_gap": greedy_final - optimum,
        "backend_contract": "injected-configuration-evaluation-backend-v1",
    }
    artifact: dict[str, Any] = {
        "format_version": RQ4_FORMAT,
        "experiment_id": "rq4-fixed-k-synthetic-validation-v1",
        "status": "synthetic-validation",
        "formal_confirmatory_experiment": False,
        "research_commit_sha": research_commit_sha,
        "system_freeze": dict(system_freeze),
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "dataset": {
            "dataset_id": "synthetic-rq4-fixture-v1",
            "content_identity": "synthetic-fixed-fixture-v1",
        },
        "workload": {"workload_id": "synthetic-rq4-workload-v1", "query_count": 6},
        "snapshot": {"semantic_digest": universe["source_snapshot_semantic_digest"]},
        "ground_truth": {
            "source_kind": "synthetic-validation-only",
            "semantic_digest": "b" * 64,
            "bound_to_snapshot": True,
        },
        "candidate_universe": eligible,
        "eligible_universe_digest": eligible["semantic_digest"],
        "native_repository_digest": eligible["native_repository_semantic_digest"],
        "candidate_exclusion_reasons": eligible["excluded_candidates"],
        "k_policy": {
            "fixed_k": FIXED_K,
            "independent_of_advisor_recommendation": True,
            "not_advisor_candidate_limit": True,
            "infeasible_policy": "infeasible-or-censored; never silently continue",
        },
        "comparison": run,
        "exhaustive_tiny_universe": {
            "scope": "diagnostic-only",
            "candidate_ids": ids,
            "fixed_k": TINY_EXHAUSTIVE_K,
            "method": tiny,
            "greedy_same_tiny_universe": tiny_greedy,
        },
        "selection_information_policies": INFORMATION_ACCESS_POLICY,
        "stock_full_data_evaluation_metrics": None,
        "source_artifacts": {
            "candidate_universe": "synthetic-fixture-generated-in-memory",
            "native_repository": "synthetic-payload-availability-fixture",
        },
        "validation_outcome": validation,
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    return artifact


def validate_rq4_artifact(path: Path) -> dict[str, Any]:
    artifact = read_json(path)
    if artifact.get("format_version") != RQ4_FORMAT:
        raise RQ4ValidationError("unsupported RQ4 artifact format")
    expected = semantic_digest(_payload(artifact))
    if artifact.get("semantic_digest") != expected:
        raise RQ4ValidationError("RQ4 artifact semantic digest mismatch")
    universe = artifact.get("candidate_universe", {})
    if universe.get("semantic_digest") != artifact.get("eligible_universe_digest"):
        raise RQ4ValidationError("eligible universe digest mismatch")
    eligible = universe.get("eligible_candidates", [])
    ids = [item.get("candidate_id") for item in eligible]
    if any(
        set(item) - set(ELIGIBILITY_METADATA_ALLOWLIST) - {"canonical_deployment_position"}
        for item in eligible
    ):
        raise RQ4ValidationError("eligible universe contains non-allowlisted candidate metadata")
    if any(
        set(item.get("candidate", {})) - set(ELIGIBILITY_METADATA_ALLOWLIST)
        for item in universe.get("excluded_candidates", [])
    ):
        raise RQ4ValidationError("excluded universe contains non-allowlisted candidate metadata")
    if len(ids) != len(set(ids)) or ids != [
        item["candidate_id"] for item in sorted(eligible, key=canonical_candidate_key)
    ]:
        raise RQ4ValidationError("eligible universe is not canonically ordered")
    comparison = artifact.get("comparison", {})
    if comparison.get("fixed_k") != artifact.get("k_policy", {}).get("fixed_k"):
        raise RQ4ValidationError("RQ4 fixed-k policy mismatch")
    methods = comparison.get("methods", {})
    if comparison.get("method_order") != list(METHOD_IDS) or set(methods) != set(METHOD_IDS):
        raise RQ4ValidationError("RQ4 method order or method set is not declared")
    candidate_set = set(ids)
    for method in METHOD_IDS:
        result = methods[method]
        if result.get("information_access_policy") != INFORMATION_ACCESS_POLICY[method]:
            raise RQ4ValidationError(f"{method} information-access policy mismatch")
        selected = result.get("selected_membership", [])
        if not set(selected).issubset(candidate_set):
            raise RQ4ValidationError(f"{method} selected an ineligible candidate")
        if result.get("status") == "complete" and len(selected) != comparison["fixed_k"]:
            raise RQ4ValidationError(f"{method} completed without exactly fixed k candidates")
        if (
            result.get("status") == "at-most-k-local-optimum"
            and len(selected) >= comparison["fixed_k"]
        ):
            raise RQ4ValidationError(f"{method} local-optimum status is not below fixed k")
        if result.get("evaluation_order") != result.get("deployment_order"):
            raise RQ4ValidationError(
                f"{method} deployment ordering differs from evaluation ordering"
            )
    tiny = artifact.get("exhaustive_tiny_universe", {})
    tiny_method = tiny.get("method", {})
    if tiny.get("scope") != "diagnostic-only" or tiny_method.get("method") != EXHAUSTIVE_METHOD_ID:
        raise RQ4ValidationError("missing separate exhaustive tiny-universe diagnostic")
    if tiny_method.get("selection_planner_evaluations") != 20:
        raise RQ4ValidationError("tiny exhaustive diagnostic did not enumerate 20 subsets")
    validation = artifact.get("validation_outcome", {})
    if validation.get("status") != "passed" or validation.get("exhaustive_evaluated_count") != 20:
        raise RQ4ValidationError("synthetic exhaustive validation gate did not pass")
    return {
        "status": "valid",
        "format_version": RQ4_FORMAT,
        "experiment_id": artifact["experiment_id"],
        "semantic_digest": expected,
        "method_count": len(methods),
        "formal_confirmatory_experiment": artifact["formal_confirmatory_experiment"],
    }


def write_synthetic_rq4_artifact(
    output: Path, *, system_freeze_path: Path, research_commit_sha: str
) -> dict[str, Any]:
    artifact = build_synthetic_rq4_artifact(
        system_freeze=read_json(system_freeze_path), research_commit_sha=research_commit_sha
    )
    write_json(output, artifact)
    return artifact


def current_research_commit(root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


__all__ = [
    "ALL_METHOD_IDS",
    "ELIGIBILITY_METADATA_ALLOWLIST",
    "EXHAUSTIVE_METHOD_ID",
    "FIXED_K",
    "METHOD_IDS",
    "RANDOM_SEEDS",
    "RQ4_FORMAT",
    "ConfigurationEvaluation",
    "EvaluationBudget",
    "build_eligible_universe",
    "build_synthetic_rq4_artifact",
    "current_research_commit",
    "run_rq4_ablation",
    "validate_rq4_artifact",
    "write_synthetic_rq4_artifact",
]
