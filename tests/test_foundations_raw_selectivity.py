from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_oracle import OracleDiagnostics, OracleEvaluation
from extstats_advisor_research.foundations_raw_selectivity import (
    RAW_CANDIDATE_C0,
    RAW_CANDIDATE_C1,
    RAW_QUERY_ID,
    RawSelectivityBinding,
    RawSelectivityEvaluationError,
    RawSelectivityObservation,
    RawSelectivityResult,
    _metrics,
    raw_log_correction,
    write_raw_selectivity_result_json,
)


def _binding(tmp_path: Path) -> RawSelectivityBinding:
    precedence = (RAW_CANDIDATE_C1, *[f"cand_{index:024d}" for index in range(56)])
    precedence = tuple(
        RAW_CANDIDATE_C0 if value == "cand_000000000000000000000003" else value
        for value in precedence
    )
    return RawSelectivityBinding(
        admin_dsn="host=/tmp/socket port=55433 dbname=extstats_lab",
        advisor_root=tmp_path,
        postgres_root=tmp_path,
        research_root=tmp_path,
        snapshot_path=tmp_path / "snapshot",
        candidate_universe_path=tmp_path / "universe.json",
        ground_truth_path=tmp_path / "truth.json",
        global_precedence=precedence,
        expected_snapshot_digest="a" * 64,
        expected_candidate_universe_digest="b" * 64,
        expected_ground_truth_digest="c" * 64,
        expected_advisor_sha="d" * 40,
        expected_research_sha="e" * 40,
        expected_postgres_sha="f" * 40,
    )


def _observation(
    membership: tuple[str, ...],
    raw_rows: float,
    plan_rows: int,
    activation: tuple[str, ...] | None = None,
):
    event = {
        "event": "base_selectivity",
        "relation_oid": 42,
        "tuples": 100.0,
        "selectivity": raw_rows / 100.0,
        "rows_before_clamp": raw_rows,
        "rows_after_clamp": plan_rows,
    }
    evaluation = OracleEvaluation(
        raw_rows,
        None,
        OracleDiagnostics("cfg", membership, "complete", 1, 1, 1, 1, False),
        {RAW_QUERY_ID: raw_rows},
        {RAW_QUERY_ID: plan_rows},
    )
    return RawSelectivityObservation(
        membership,
        membership if activation is None else activation,
        evaluation,
        (event,),
        event,
    )


def _result(tmp_path: Path, rows=(1.3, 2.6, 2.6, 5.2)) -> RawSelectivityResult:
    binding = _binding(tmp_path)
    joint = (RAW_CANDIDATE_C0, RAW_CANDIDATE_C1)
    observations = (
        _observation((), rows[0], 1),
        _observation((RAW_CANDIDATE_C0,), rows[1], 3),
        _observation((RAW_CANDIDATE_C1,), rows[2], 3),
        _observation(joint, rows[3], 5, (RAW_CANDIDATE_C1, RAW_CANDIDATE_C0)),
    )
    return RawSelectivityResult(binding, "exp_raw_selectivity_test", 42, observations, {}, True)


def test_raw_corrections_use_fractional_pre_clamp_values() -> None:
    assert raw_log_correction(2.6, 1.3) == pytest.approx(math.log(2.0))
    assert raw_log_correction(3.0, 1.0) == pytest.approx(math.log(3.0))


def test_metrics_keep_raw_values_separate_from_plan_rows() -> None:
    metrics = _metrics(_result(Path("/tmp")))
    assert metrics["rows_pre_clamp"] == {"empty": 1.3, "c0": 2.6, "c1": 2.6, "c0_c1": 5.2}
    assert metrics["plan_rows_clamped"] == {"empty": 1, "c0": 3, "c1": 3, "c0_c1": 5}
    assert metrics["delta_rows_pre_clamp"] == pytest.approx(0.0)
    assert metrics["passes_by_tolerance"] == {
        str(value): True for value in (1e-12, 1e-9, 1e-6, 1e-3)
    }


def test_joint_activation_preserves_global_precedence_and_rounding_identity(tmp_path: Path) -> None:
    result = _result(tmp_path)
    assert result.observations[-1].activation_membership == (RAW_CANDIDATE_C1, RAW_CANDIDATE_C0)
    assert result.observations[-1].evaluation.formatted_estimates[RAW_QUERY_ID] == 5


def test_invalid_raw_values_fail_explicitly() -> None:
    with pytest.raises(RawSelectivityEvaluationError):
        raw_log_correction(0.0, 1.0)
    with pytest.raises(RawSelectivityEvaluationError):
        raw_log_correction(float("nan"), 1.0)


def test_atomic_json_preserves_fractional_raw_observations_and_no_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "raw-selectivity.json"
    write_raw_selectivity_result_json(_result(tmp_path), destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["configurations"][0]["rows_pre_clamp"] == 1.3
    assert payload["configurations"][0]["plan_rows_clamped"] == 1
    assert payload["metrics"]["predicted_rows_pre_clamp_c0_c1"] == pytest.approx(5.2)
    with pytest.raises(FileExistsError):
        write_raw_selectivity_result_json(_result(tmp_path), destination)
