from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_four_predicate_mcv import (
    DIAGNOSTIC_COLUMNS,
    DIAGNOSTIC_MEMBERSHIPS,
    MCV_CANDIDATE_A,
    MCV_CANDIDATE_B,
    FourPredicateBinding,
    FourPredicateInputError,
    build_four_predicate_sql,
    classify_observation,
    derive_four_predicate_bounds,
    rounding_compatible_with_multiplicativity,
    write_four_predicate_result_json,
)
from extstats_advisor_research.foundations_oracle import OracleDiagnostics, OracleEvaluation


class _Column:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def to_pylist(self) -> list[float]:
        return self._values


class _Sample:
    def __init__(self) -> None:
        self._columns = {column: _Column(list(range(1, 9))) for column in DIAGNOSTIC_COLUMNS}

    def column(self, name: str) -> _Column:
        return self._columns[name]


def _binding(tmp_path) -> FourPredicateBinding:
    bounds = derive_four_predicate_bounds(_Sample())
    sql = build_four_predicate_sql(bounds)
    precedence = [f"cand_{index:024d}" for index in range(57)]
    precedence[3] = MCV_CANDIDATE_A
    precedence[12] = MCV_CANDIDATE_B
    return FourPredicateBinding(
        admin_dsn="host=/tmp/socket port=55433 dbname=extstats_lab",
        advisor_root=tmp_path,
        postgres_root=tmp_path,
        research_root=tmp_path,
        snapshot_path=tmp_path / "snapshot",
        candidate_universe_path=tmp_path / "universe.json",
        ground_truth_path=tmp_path / "truth.json",
        dataset_id="forest10",
        relation_id="forest10",
        relation_schema="public",
        relation_name="forest10",
        workload_id="arecel_forest10_test_v1",
        global_precedence=tuple(precedence),
        expected_snapshot_digest="a" * 64,
        expected_candidate_universe_digest="b" * 64,
        expected_ground_truth_digest="c" * 64,
        expected_advisor_sha="d" * 40,
        expected_research_sha="e" * 40,
        expected_postgres_sha="f" * 40,
        query_sql=sql,
        query_sql_sha256=__import__("hashlib").sha256(sql.encode()).hexdigest(),
        bounds=bounds,
    )


def test_bounds_and_query_shape_are_deterministic() -> None:
    bounds = derive_four_predicate_bounds(_Sample())
    assert bounds == {column: (2.0, 6.0) for column in DIAGNOSTIC_COLUMNS}
    sql = build_four_predicate_sql(bounds)
    assert sql.count(" BETWEEN ") == 4
    assert sql.count(" AND ") == 7
    assert sql.endswith(";")
    assert all(column in sql for column in DIAGNOSTIC_COLUMNS)


def test_degenerate_quantiles_use_declared_min_max_fallback() -> None:
    sample = _Sample()
    sample._columns["aspect"] = _Column([4.0] * 8)
    bounds = derive_four_predicate_bounds(sample)
    assert bounds["aspect"] == (4.0, 4.0)


def test_strict_four_memberships_and_precedence_binding() -> None:
    assert DIAGNOSTIC_MEMBERSHIPS == (
        (),
        (MCV_CANDIDATE_A,),
        (MCV_CANDIDATE_B,),
        (MCV_CANDIDATE_A, MCV_CANDIDATE_B),
    )
    binding = _binding(__import__("pathlib").Path("/tmp"))
    assert binding.global_precedence.index(MCV_CANDIDATE_A) < binding.global_precedence.index(
        MCV_CANDIDATE_B
    )


def test_observation_classification_and_invalid_raw_estimates() -> None:
    exact = classify_observation({"empty": 100, "a": 110, "b": 120, "ab": 132})
    assert exact["category"] == "two-sided-informative"
    assert exact["absolute_log_residual"] == pytest.approx(0.0)
    assert (
        classify_observation({"empty": 100, "a": 100, "b": 120, "ab": 120})["category"]
        == "one-sided"
    )
    assert classify_observation({"empty": 0, "a": 1, "b": 1, "ab": 1})["valid"] is False


def test_rounding_compatibility_is_distinct_from_recorded_equality() -> None:
    assert rounding_compatible_with_multiplicativity({"empty": 100, "a": 110, "b": 120, "ab": 132})
    assert not rounding_compatible_with_multiplicativity(
        {"empty": 100, "a": 110, "b": 120, "ab": 200}
    )


def _evaluation(membership: tuple[str, ...], value: float, formatted: int) -> OracleEvaluation:
    return OracleEvaluation(
        value,
        None,
        OracleDiagnostics("cfg", membership, "complete", 1, 1, 1, 1, False),
        {"foundations_four_predicate_two_mcv_v1": value},
        {"foundations_four_predicate_two_mcv_v1": formatted},
    )


def test_final_result_publication_is_atomic_and_no_overwrite(tmp_path) -> None:
    binding = _binding(tmp_path)
    result = SimpleNamespace(
        binding=binding,
        database_name="exp_foundations_mcv_test",
        evaluations=(
            _evaluation((), 100.0, 100),
            _evaluation((MCV_CANDIDATE_A,), 110.0, 110),
            _evaluation((MCV_CANDIDATE_B,), 120.0, 120),
            _evaluation((MCV_CANDIDATE_A, MCV_CANDIDATE_B), 132.0, 132),
        ),
        sandbox_verification={"sample_row_count": 30_000},
        cleanup_verified=True,
        complete=True,
    )
    destination = tmp_path / "result.json"
    write_four_predicate_result_json(result, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["diagnostic"]["absolute_log_residual"] == pytest.approx(0.0)
    with pytest.raises(FileExistsError):
        write_four_predicate_result_json(result, destination)


def test_binding_rejects_wrong_sql_digest(tmp_path) -> None:
    binding = _binding(tmp_path)
    with pytest.raises(FourPredicateInputError):
        replace(binding, query_sql_sha256="0" * 64)
