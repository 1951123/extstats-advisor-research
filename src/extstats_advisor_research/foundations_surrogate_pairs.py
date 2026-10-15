"""Out-of-sample validation of a frozen singleton multiplicative surrogate."""

from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .foundations_landscape import FoundationsLandscapeInput
from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _run_oracle_configurations,
    _validate_bound_inputs,
)
from .foundations_surrogate_milp import (
    weighted_log_error,
    weighted_qerror,
    write_surrogate_result_json,
)
from .postgres.experiment_database import ExperimentDatabase

PAIR_VALIDATION_EXPERIMENT_ID = "foundations-disjoint-surrogate-pair-validation-v1"
FROZEN_SURROGATE_ARTIFACT_SHA256 = (
    "bfc4ec9e02d6e7f668382ef51448b44e384d3151e5b0f2f0ff215b48d7636a0e"
)
PAIR_SINGLETON_CHANGE_TOLERANCE = 1e-12
PAIR_WARNING_THRESHOLD = 1e-3
SURROGATE_CANDIDATES = (
    "cand_da7591327792c9bc98b427d7",
    "cand_4de3778bb8674287b6dd5b29",
    "cand_51488597ac9ebd3cb59d2ff1",
    "cand_a393d81ec58b047e1e129db0",
    "cand_f2d14d17235b34bf39a631f5",
)


class PairValidationError(ValueError):
    """Raised when frozen-model or pair-validation inputs are invalid."""


def _finite_positive(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PairValidationError(f"{label} must be numeric")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise PairValidationError(f"{label} must be finite and strictly positive")
    return value


def _quantiles(values: Sequence[float]) -> dict[str, float | None]:
    if not values:
        return {"mean": None, "median": None, "p90": None, "p95": None, "max": None}
    ordered = sorted(values)

    def percentile(fraction: float) -> float:
        position = (len(ordered) - 1) * fraction
        lower = math.floor(position)
        upper = math.ceil(position)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)

    return {
        "mean": sum(ordered) / len(ordered),
        "median": percentile(0.5),
        "p90": percentile(0.9),
        "p95": percentile(0.95),
        "max": ordered[-1],
    }


class FrozenSurrogateModel:
    """Raw empty/singleton observations and bindings from the sealed artifact."""

    def __init__(
        self,
        *,
        artifact_path: Path,
        artifact_sha256: str,
        source_bindings: Mapping[str, str],
        artifact_bindings: Mapping[str, str],
        candidate_ids: tuple[str, ...],
        query_ids: tuple[str, ...],
        global_precedence: tuple[str, ...],
        candidate_metadata: tuple[Mapping[str, Any], ...],
        empty_estimates: Mapping[str, float],
        singleton_estimates: Mapping[str, Mapping[str, float]],
    ) -> None:
        self.artifact_path = artifact_path
        self.artifact_sha256 = artifact_sha256
        self.source_bindings = dict(source_bindings)
        self.artifact_bindings = dict(artifact_bindings)
        self.candidate_ids = candidate_ids
        self.query_ids = query_ids
        self.global_precedence = global_precedence
        self.candidate_metadata = candidate_metadata
        self.empty_estimates = dict(empty_estimates)
        self.singleton_estimates = {
            candidate_id: dict(estimates) for candidate_id, estimates in singleton_estimates.items()
        }

    def predict_pair(self, first: str, second: str) -> dict[str, float]:
        try:
            first_index = self.candidate_ids.index(first)
            second_index = self.candidate_ids.index(second)
        except ValueError as exc:
            raise PairValidationError("pair must contain two known candidate IDs") from exc
        if first_index >= second_index:
            raise PairValidationError("pair must contain two ordered known candidate IDs")
        return {
            query_id: math.exp(
                math.log(self.singleton_estimates[first][query_id])
                + math.log(self.singleton_estimates[second][query_id])
                - math.log(self.empty_estimates[query_id])
            )
            for query_id in self.query_ids
        }


def load_frozen_surrogate_model(
    path: Path,
    *,
    expected_sha256: str = FROZEN_SURROGATE_ARTIFACT_SHA256,
    expected_snapshot_digest: str,
    expected_candidate_universe_digest: str,
    expected_ground_truth_digest: str,
    expected_candidate_ids: Sequence[str],
    expected_query_ids: Sequence[str],
) -> FrozenSurrogateModel:
    """Load and validate the prior singleton-fitted artifact byte-for-byte."""

    path = Path(path)
    raw = path.read_bytes()
    actual_sha256 = hashlib.sha256(raw).hexdigest()
    if actual_sha256 != expected_sha256:
        raise PairValidationError(
            f"frozen surrogate SHA256 mismatch: expected {expected_sha256}, got {actual_sha256}"
        )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PairValidationError("frozen surrogate is not valid JSON") from exc
    if payload.get("experiment_id") != "foundations-disjoint-surrogate-milp-v1":
        raise PairValidationError("unexpected frozen surrogate experiment identity")
    if payload.get("lifecycle_completed") is not True:
        raise PairValidationError("frozen surrogate was not completed")
    bindings = payload.get("artifact_bindings")
    expected_bindings = {
        "snapshot_semantic_digest": expected_snapshot_digest,
        "candidate_universe_semantic_digest": expected_candidate_universe_digest,
        "ground_truth_semantic_digest": expected_ground_truth_digest,
    }
    if bindings != expected_bindings:
        raise PairValidationError("frozen surrogate artifact bindings do not match")
    candidate_ids = tuple(payload.get("candidate_ids", ()))
    query_ids = tuple(payload.get("query_ids", ()))
    if candidate_ids != tuple(expected_candidate_ids):
        raise PairValidationError("frozen surrogate candidate IDs do not match")
    if query_ids != tuple(expected_query_ids):
        raise PairValidationError("frozen surrogate query IDs do not match")
    precedence = tuple(payload.get("global_precedence", ()))
    if len(precedence) != 57 or len(set(precedence)) != 57:
        raise PairValidationError(
            "frozen surrogate precedence is not a complete 57-candidate order"
        )
    candidate_metadata = tuple(payload.get("candidate_metadata", ()))
    if tuple(item.get("candidate_id") for item in candidate_metadata) != candidate_ids:
        raise PairValidationError("frozen surrogate candidate metadata is incomplete or reordered")
    observations = payload.get("observations")
    if not isinstance(observations, dict) or set(observations) != {"0", "1", "2", "4", "8", "16"}:
        raise PairValidationError(
            "frozen surrogate lacks the empty and five singleton observations"
        )

    def observation_estimates(mask: str) -> dict[str, float]:
        estimates = observations[mask].get("estimates")
        if not isinstance(estimates, dict) or tuple(estimates) != query_ids:
            raise PairValidationError(f"observation {mask} has incomplete query estimates")
        return {
            query_id: _finite_positive(estimates[query_id], f"observation {mask}[{query_id}]")
            for query_id in query_ids
        }

    empty = observation_estimates("0")
    singleton = {
        candidate_id: observation_estimates(str(1 << index))
        for index, candidate_id in enumerate(candidate_ids)
    }
    coefficients = payload.get("coefficients", {})
    if coefficients.get("empty_estimates") != empty:
        raise PairValidationError(
            "frozen coefficient empty estimates do not match raw observations"
        )
    return FrozenSurrogateModel(
        artifact_path=path,
        artifact_sha256=actual_sha256,
        source_bindings=payload.get("source_bindings", {}),
        artifact_bindings=bindings,
        candidate_ids=candidate_ids,
        query_ids=query_ids,
        global_precedence=precedence,
        candidate_metadata=candidate_metadata,
        empty_estimates=empty,
        singleton_estimates=singleton,
    )


def enumerate_surrogate_pairs(candidate_ids: Sequence[str]) -> tuple[tuple[str, str], ...]:
    """Return every unordered pair in the frozen candidate order."""

    candidate_ids = tuple(candidate_ids)
    if len(candidate_ids) != 5 or len(set(candidate_ids)) != 5:
        raise PairValidationError("pair validation requires exactly five unique candidates")
    return tuple(itertools.combinations(candidate_ids, 2))


def _metadata_columns(metadata: Mapping[str, Any]) -> frozenset[tuple[str, int]]:
    relation_id = metadata.get("relation_id")
    ordinals = metadata.get("column_ordinals")
    if not isinstance(relation_id, str) or not isinstance(ordinals, list) or not ordinals:
        raise PairValidationError("candidate metadata lacks relation and column identity")
    return frozenset((relation_id, int(ordinal)) for ordinal in ordinals)


def validate_pairwise_disjointness(model: FrozenSurrogateModel) -> None:
    metadata = {item["candidate_id"]: item for item in model.candidate_metadata}
    for first, second in enumerate_surrogate_pairs(model.candidate_ids):
        if _metadata_columns(metadata[first]) & _metadata_columns(metadata[second]):
            raise PairValidationError(f"candidate pair is not column-disjoint: {first}, {second}")


def build_pair_predictions(
    model: FrozenSurrogateModel,
) -> dict[tuple[str, str], dict[str, float]]:
    """Compute and validate all 320 predictions before any pair Oracle call."""

    validate_pairwise_disjointness(model)
    pairs = enumerate_surrogate_pairs(model.candidate_ids)
    predictions = {pair: model.predict_pair(*pair) for pair in pairs}
    if sum(len(values) for values in predictions.values()) != 320:
        raise PairValidationError("pair prediction count is not 10 x 32")
    for pair, values in predictions.items():
        for query_id, value in values.items():
            _finite_positive(value, f"prediction[{pair}][{query_id}]")
    return predictions


def _classification(
    empty: float,
    first: float,
    second: float,
    *,
    tolerance: float,
) -> str:
    first_changed = abs(first - empty) > tolerance
    second_changed = abs(second - empty) > tolerance
    if first_changed and second_changed:
        return "two-sided-informative"
    if first_changed or second_changed:
        return "one-sided"
    return "non-informative"


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    valid = [record for record in records if record["valid"]]
    by_class: dict[str, list[float]] = {}
    for record in valid:
        by_class.setdefault(record["classification"], []).append(record["absolute_log_residual"])
    return {
        "total": len(records),
        "valid": len(valid),
        "invalid": len(records) - len(valid),
        "warning_count": sum(
            record["valid"] and record["absolute_log_residual"] > PAIR_WARNING_THRESHOLD
            for record in records
        ),
        "all_valid_residuals": _quantiles([record["absolute_log_residual"] for record in valid]),
        "by_class": {
            classification: {"count": len(values), "residuals": _quantiles(values)}
            for classification, values in sorted(by_class.items())
        },
    }


def analyze_pair_evaluations(
    *,
    model: FrozenSurrogateModel,
    predictions: Mapping[tuple[str, str], Mapping[str, float]],
    evaluations: Sequence[Any],
    truths: Mapping[str, float],
    weights: Mapping[str, float],
    singleton_change_tolerance: float = PAIR_SINGLETON_CHANGE_TOLERANCE,
) -> dict[str, Any]:
    pairs = enumerate_surrogate_pairs(model.candidate_ids)
    if len(evaluations) != len(pairs):
        raise PairValidationError("pair evaluation count does not match the ten frozen pairs")
    records: list[dict[str, Any]] = []
    per_pair: list[dict[str, Any]] = []
    for pair, evaluation in zip(pairs, evaluations, strict=True):
        first, second = pair
        actual = dict(evaluation.estimates or {})
        pair_records: list[dict[str, Any]] = []
        for query_id in model.query_ids:
            empty = model.empty_estimates[query_id]
            first_estimate = model.singleton_estimates[first][query_id]
            second_estimate = model.singleton_estimates[second][query_id]
            predicted = float(predictions[pair][query_id])
            raw_actual = actual.get(query_id)
            classification = _classification(
                empty,
                first_estimate,
                second_estimate,
                tolerance=singleton_change_tolerance,
            )
            record: dict[str, Any] = {
                "pair": list(pair),
                "query_id": query_id,
                "classification": classification,
                "empty_estimate": empty,
                "first_singleton_estimate": first_estimate,
                "second_singleton_estimate": second_estimate,
                "predicted_estimate": predicted,
                "actual_estimate": raw_actual,
                "valid": False,
                "signed_log_residual": None,
                "absolute_log_residual": None,
                "signed_relative_multiplicative_deviation": None,
                "absolute_relative_multiplicative_deviation": None,
            }
            try:
                observed = _finite_positive(raw_actual, f"actual[{pair}][{query_id}]")
                _finite_positive(predicted, f"predicted[{pair}][{query_id}]")
            except PairValidationError:
                record["classification"] = "invalid"
            else:
                residual = math.log(observed) - math.log(predicted)
                record.update(
                    {
                        "actual_estimate": observed,
                        "valid": True,
                        "signed_log_residual": residual,
                        "absolute_log_residual": abs(residual),
                        "signed_relative_multiplicative_deviation": math.expm1(residual),
                        "absolute_relative_multiplicative_deviation": abs(math.expm1(residual)),
                    }
                )
            pair_records.append(record)
            records.append(record)
        actual_estimates = {
            record["query_id"]: record["actual_estimate"]
            for record in pair_records
            if record["valid"]
        }
        predicted_estimates = dict(predictions[pair])
        complete = len(actual_estimates) == len(model.query_ids)
        per_pair.append(
            {
                "pair": list(pair),
                "membership": list(pair),
                "activation_membership": list(evaluation.diagnostics.membership),
                "status": evaluation.diagnostics.status,
                "diagnostics": evaluation.diagnostics.as_dict(),
                "valid_observation_count": len(actual_estimates),
                "predicted_log_error": (
                    weighted_log_error(predicted_estimates, truths, weights) if complete else None
                ),
                "actual_log_error": (
                    weighted_log_error(actual_estimates, truths, weights) if complete else None
                ),
                "predicted_qerror": (
                    weighted_qerror(predicted_estimates, truths, weights) if complete else None
                ),
                "actual_qerror": (
                    weighted_qerror(actual_estimates, truths, weights) if complete else None
                ),
                "records": pair_records,
                "summary": _summary(pair_records),
            }
        )
    return {
        "singleton_change_tolerance": singleton_change_tolerance,
        "warning_threshold": PAIR_WARNING_THRESHOLD,
        "pair_count": len(pairs),
        "query_count": len(model.query_ids),
        "record_count": len(records),
        "summary": _summary(records),
        "pairs": per_pair,
    }


def run_foundations_surrogate_pair_validation(
    inputs: FoundationsLandscapeInput,
    frozen_model_path: Path,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Any = ExperimentDatabase,
) -> dict[str, Any]:
    """Run one fresh-database, ten-pair validation using a frozen model."""

    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    model = load_frozen_surrogate_model(
        frozen_model_path,
        expected_snapshot_digest=inputs.expected_snapshot_digest,
        expected_candidate_universe_digest=inputs.expected_candidate_universe_digest,
        expected_ground_truth_digest=inputs.expected_ground_truth_digest,
        expected_candidate_ids=SURROGATE_CANDIDATES,
        expected_query_ids=inputs.query_ids,
    )
    if tuple(inputs.global_precedence) != model.global_precedence:
        raise PairValidationError("runtime precedence differs from the frozen model precedence")
    _snapshot, universe, ground_truth = _validate_bound_inputs(inputs, services)
    universe_ids = tuple(candidate.candidate_id for candidate in universe.candidates)
    if set(model.global_precedence) != set(universe_ids):
        raise PairValidationError("frozen model precedence does not match the live universe")
    if model.candidate_ids != tuple(item["candidate_id"] for item in model.candidate_metadata):
        raise PairValidationError("frozen model candidate metadata mismatch")
    model_metadata = {item["candidate_id"]: item for item in model.candidate_metadata}
    live_metadata = {candidate.candidate_id: candidate for candidate in universe.candidates}
    for candidate_id in model.candidate_ids:
        live = live_metadata.get(candidate_id)
        frozen = model_metadata[candidate_id]
        if (
            live is None
            or live.relation_id != frozen["relation_id"]
            or live.kind != frozen["kind"]
            or tuple(live.column_ordinals) != tuple(frozen["column_ordinals"])
            or tuple(live.column_names) != tuple(frozen["column_names"])
        ):
            raise PairValidationError(f"live candidate metadata mismatch for {candidate_id}")
    profile_weights = {
        profile.query_id: float(profile.weight) for profile in universe.query_profiles
    }
    truths = {
        truth.query_id: float(truth.cardinality)
        for truth in ground_truth.truths
        if truth.query_id in model.query_ids
    }
    weights = {query_id: profile_weights[query_id] for query_id in model.query_ids}
    if set(truths) != set(model.query_ids) or set(weights) != set(model.query_ids):
        raise PairValidationError("live truth or workload weights do not cover the frozen queries")
    predictions = build_pair_predictions(model)
    pairs = enumerate_surrogate_pairs(model.candidate_ids)
    run = _run_oracle_configurations(
        inputs,
        pairs,
        services=services,
        lifecycle_factory=lifecycle_factory,
        precise=True,
    )
    analysis = analyze_pair_evaluations(
        model=model,
        predictions=predictions,
        evaluations=run.evaluations,
        truths=truths,
        weights=weights,
    )
    return {
        "experiment_id": PAIR_VALIDATION_EXPERIMENT_ID,
        "source_bindings": {
            "research_commit_sha": inputs.research_commit_sha,
            "advisor_commit_sha": inputs.expected_advisor_sha,
            "patched_postgres_commit_sha": inputs.patched_postgres_commit_sha,
        },
        "frozen_model": {
            "path": str(model.artifact_path),
            "sha256": model.artifact_sha256,
            "source_bindings": model.source_bindings,
        },
        "artifact_bindings": dict(model.artifact_bindings),
        "dataset_id": inputs.dataset_id,
        "relation_id": inputs.relation_id,
        "relation_schema": inputs.relation_schema,
        "relation_name": inputs.relation_name,
        "workload_id": inputs.workload_id,
        "sample_rows": inputs.sample_rows,
        "statistics_target": inputs.statistics_target,
        "global_precedence": list(model.global_precedence),
        "candidate_ids": list(model.candidate_ids),
        "candidate_metadata": list(model.candidate_metadata),
        "query_ids": list(model.query_ids),
        "database_name": run.database_name,
        "sandbox_verification": dict(run.sandbox_verification),
        "primary_planner_query_attempts": sum(
            item.diagnostics.planner_query_attempts for item in run.evaluations
        ),
        "primary_planner_query_successes": sum(
            item.diagnostics.planner_query_successes for item in run.evaluations
        ),
        "predictions_computed_before_observations": True,
        "analysis": analysis,
        "lifecycle_completed": True,
    }


def write_surrogate_pair_result_json(payload: Mapping[str, Any], destination: Path) -> None:
    """Publish the pair-validation result without overwriting prior evidence."""

    write_surrogate_result_json(payload, destination)
