from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_oracle import OracleDiagnostics, OracleEvaluation
from extstats_advisor_research.foundations_surrogate_pairs import (
    FrozenSurrogateModel,
    PairValidationError,
    analyze_pair_evaluations,
    build_pair_predictions,
    enumerate_surrogate_pairs,
    load_frozen_surrogate_model,
    validate_pairwise_disjointness,
)

CANDIDATES = tuple(f"c{i}" for i in range(5))
QUERIES = tuple(f"q{i:02d}" for i in range(32))


def _model() -> FrozenSurrogateModel:
    metadata = tuple(
        {
            "candidate_id": candidate_id,
            "relation_id": "r",
            "column_ordinals": [2 * index + 1, 2 * index + 2],
            "column_names": [f"c{2 * index + 1}", f"c{2 * index + 2}"],
            "kind": "postgresql.dependencies",
        }
        for index, candidate_id in enumerate(CANDIDATES)
    )
    empty = {query_id: 10.0 for query_id in QUERIES}
    singletons = {
        candidate_id: {query_id: 10.0 + index for query_id in QUERIES}
        for index, candidate_id in enumerate(CANDIDATES)
    }
    return FrozenSurrogateModel(
        artifact_path=Path("frozen.json"),
        artifact_sha256="sha",
        source_bindings={},
        artifact_bindings={},
        candidate_ids=CANDIDATES,
        query_ids=QUERIES,
        global_precedence=(*CANDIDATES, *(f"other{i}" for i in range(52))),
        candidate_metadata=metadata,
        empty_estimates=empty,
        singleton_estimates=singletons,
    )


def _evaluation(model: FrozenSurrogateModel, pair: tuple[str, str], estimates=None):
    return OracleEvaluation(
        objective=1.0,
        utility_result=None,
        diagnostics=OracleDiagnostics(
            configuration_id="cfg",
            membership=pair,
            status="complete",
            elapsed_ns=1,
            oracle_invocations=1,
            planner_query_attempts=len(model.query_ids),
            planner_query_successes=len(model.query_ids),
            cache_hit=False,
        ),
        estimates=estimates or {query_id: 10.0 for query_id in model.query_ids},
        formatted_estimates={query_id: 10 for query_id in model.query_ids},
    )


def test_all_ten_pairs_are_deterministic_and_disjoint() -> None:
    model = _model()
    pairs = enumerate_surrogate_pairs(model.candidate_ids)
    assert len(pairs) == 10
    assert pairs[0] == ("c0", "c1")
    assert len(set(pairs)) == 10
    validate_pairwise_disjointness(model)
    assert len(build_pair_predictions(model)) == 10
    assert sum(len(value) for value in build_pair_predictions(model).values()) == 320


def test_overlapping_pair_is_rejected() -> None:
    model = _model()
    metadata = list(model.candidate_metadata)
    metadata[1] = {**metadata[1], "column_ordinals": [2, 4]}
    overlapping = FrozenSurrogateModel(
        artifact_path=model.artifact_path,
        artifact_sha256=model.artifact_sha256,
        source_bindings=model.source_bindings,
        artifact_bindings=model.artifact_bindings,
        candidate_ids=model.candidate_ids,
        query_ids=model.query_ids,
        global_precedence=model.global_precedence,
        candidate_metadata=tuple(metadata),
        empty_estimates=model.empty_estimates,
        singleton_estimates=model.singleton_estimates,
    )
    with pytest.raises(PairValidationError, match="not column-disjoint"):
        validate_pairwise_disjointness(overlapping)


def test_predictions_use_frozen_singletons_and_log_space_composition() -> None:
    model = _model()
    predictions = build_pair_predictions(model)
    assert predictions[("c0", "c1")]["q00"] == pytest.approx(10.0 * 11.0 / 10.0)
    assert predictions[("c3", "c4")]["q31"] == pytest.approx(13.0 * 14.0 / 10.0)


def test_invalid_actual_estimate_is_preserved_without_being_counted_valid() -> None:
    model = _model()
    predictions = build_pair_predictions(model)
    pair = enumerate_surrogate_pairs(model.candidate_ids)[0]
    estimates = {query_id: 10.0 for query_id in model.query_ids}
    estimates["q00"] = 0.0
    evaluations = [
        _evaluation(model, current, estimates if current == pair else None)
        for current in enumerate_surrogate_pairs(model.candidate_ids)
    ]
    result = analyze_pair_evaluations(
        model=model,
        predictions=predictions,
        evaluations=evaluations,
        truths={query_id: 10.0 for query_id in model.query_ids},
        weights={query_id: 1.0 for query_id in model.query_ids},
    )
    first = result["pairs"][0]
    assert first["summary"]["invalid"] == 1
    assert first["records"][0]["valid"] is False
    assert first["actual_log_error"] is None


def test_analysis_preserves_activation_order_and_exact_prediction_residuals() -> None:
    model = _model()
    predictions = build_pair_predictions(model)
    pairs = enumerate_surrogate_pairs(model.candidate_ids)
    evaluations = [_evaluation(model, pair, predictions[pair]) for pair in pairs]
    result = analyze_pair_evaluations(
        model=model,
        predictions=predictions,
        evaluations=evaluations,
        truths={query_id: 10.0 for query_id in model.query_ids},
        weights={query_id: 1.0 for query_id in model.query_ids},
    )
    assert result["record_count"] == 320
    assert result["summary"]["valid"] == 320
    assert result["summary"]["warning_count"] == 0
    assert result["pairs"][0]["activation_membership"] == ["c0", "c1"]
    assert result["pairs"][0]["predicted_log_error"] == pytest.approx(
        result["pairs"][0]["actual_log_error"]
    )


def test_loader_validates_artifact_hash_and_raw_observations(tmp_path: Path) -> None:
    model = _model()
    observations = {}
    for mask, estimates in [
        (0, model.empty_estimates),
        *((1 << i, model.singleton_estimates[c]) for i, c in enumerate(CANDIDATES)),
    ]:
        observations[str(mask)] = {"estimates": dict(estimates)}
    payload = {
        "experiment_id": "foundations-disjoint-surrogate-milp-v1",
        "lifecycle_completed": True,
        "artifact_bindings": {
            "snapshot_semantic_digest": "s",
            "candidate_universe_semantic_digest": "u",
            "ground_truth_semantic_digest": "g",
        },
        "source_bindings": {},
        "candidate_ids": list(CANDIDATES),
        "query_ids": list(QUERIES),
        "global_precedence": list(model.global_precedence),
        "candidate_metadata": list(model.candidate_metadata),
        "observations": observations,
        "coefficients": {"empty_estimates": dict(model.empty_estimates)},
    }
    path = tmp_path / "model.json"
    path.write_text(json.dumps(payload))
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    loaded = load_frozen_surrogate_model(
        path,
        expected_sha256=sha,
        expected_snapshot_digest="s",
        expected_candidate_universe_digest="u",
        expected_ground_truth_digest="g",
        expected_candidate_ids=CANDIDATES,
        expected_query_ids=QUERIES,
    )
    assert loaded.empty_estimates == model.empty_estimates
    with pytest.raises(PairValidationError, match="SHA256 mismatch"):
        load_frozen_surrogate_model(
            path,
            expected_sha256="wrong",
            expected_snapshot_digest="s",
            expected_candidate_universe_digest="u",
            expected_ground_truth_digest="g",
            expected_candidate_ids=CANDIDATES,
            expected_query_ids=QUERIES,
        )
