from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_mcv_mechanism import (
    FROZEN_QUERY_SQL,
    FROZEN_QUERY_SQL_SHA256,
    MCV_CANDIDATE_A,
    MCV_CANDIDATE_B,
    MECHANISM_MEMBERSHIPS,
    MechanismBinding,
    MechanismEvaluationError,
    MechanismObservation,
    MechanismResult,
    _metrics,
    _parse_trace,
    write_mcv_mechanism_result_json,
)
from extstats_advisor_research.foundations_oracle import OracleDiagnostics, OracleEvaluation


def _binding(tmp_path: Path) -> MechanismBinding:
    precedence = [f"cand_{index:024d}" for index in range(57)]
    precedence[3] = MCV_CANDIDATE_A
    precedence[12] = MCV_CANDIDATE_B
    return MechanismBinding(
        admin_dsn="host=/tmp/socket port=55433 dbname=extstats_lab",
        advisor_root=tmp_path,
        postgres_root=tmp_path,
        research_root=tmp_path,
        snapshot_path=tmp_path / "snapshot",
        candidate_universe_path=tmp_path / "universe.json",
        ground_truth_path=tmp_path / "truth.json",
        global_precedence=tuple(precedence),
        expected_snapshot_digest="a" * 64,
        expected_candidate_universe_digest="b" * 64,
        expected_ground_truth_digest="c" * 64,
        expected_advisor_sha="d" * 40,
        expected_research_sha="e" * 40,
        expected_postgres_sha="f" * 40,
    )


def _observation(membership: tuple[str, ...], precise: float, rounded: int, selectivity: float):
    evaluation = OracleEvaluation(
        precise,
        None,
        OracleDiagnostics("cfg", membership, "complete", 1, 1, 1, 1, False),
        {"arecel_forest10_test_000019": precise},
        {"arecel_forest10_test_000019": rounded},
    )
    return MechanismObservation(
        membership,
        evaluation,
        ({"event": "base_selectivity", "relation_oid": 42, "selectivity": selectivity},),
        selectivity,
    )


def _result(tmp_path: Path) -> MechanismResult:
    binding = _binding(tmp_path)
    observations = (
        _observation((), 100.0, 100, 0.10),
        _observation((MCV_CANDIDATE_A,), 110.0, 110, 0.11),
        _observation((MCV_CANDIDATE_B,), 120.0, 120, 0.12),
        _observation((MCV_CANDIDATE_A, MCV_CANDIDATE_B), 132.0, 132, 0.132),
    )
    return MechanismResult(binding, "exp_foundations_mechanism_test", 42, observations, {}, True)


def test_frozen_query_identity_and_precedence_are_explicit() -> None:
    assert hashlib.sha256(FROZEN_QUERY_SQL.encode()).hexdigest() == FROZEN_QUERY_SQL_SHA256
    assert MECHANISM_MEMBERSHIPS == (
        (),
        (MCV_CANDIDATE_A,),
        (MCV_CANDIDATE_B,),
        (MCV_CANDIDATE_A, MCV_CANDIDATE_B),
    )


def test_structured_trace_parser_rejects_unrelated_messages() -> None:
    assert _parse_trace("ordinary notice") is None
    event = _parse_trace('PGEXTADV_MCV_TRACE {"event":"mcv_application","stat_oid":123}')
    assert event == {"event": "mcv_application", "stat_oid": 123}
    with pytest.raises(MechanismEvaluationError):
        _parse_trace('PGEXTADV_MCV_TRACE {"event":"unknown"}')


def test_metrics_compare_pre_round_selectivity_and_rows() -> None:
    result = _result(Path("/tmp"))
    metrics = _metrics(result)
    assert metrics["raw_precise_plan_rows"] == {
        "empty": 100.0,
        "a": 110.0,
        "b": 120.0,
        "ab": 132.0,
    }
    assert metrics["rho_rows"] == pytest.approx(1.0)
    assert metrics["rho_selectivity"] == pytest.approx(1.0)
    assert metrics["r_b_empty_to_b"] == pytest.approx(1.2)
    assert metrics["r_b_after_a"] == pytest.approx(1.2)
    assert all(metrics["passes_by_tolerance"].values())


def test_final_trace_artifact_is_atomic_and_no_overwrite(tmp_path: Path) -> None:
    destination = tmp_path / "mcv-mechanism.json"
    result = _result(tmp_path)
    write_mcv_mechanism_result_json(result, destination)
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["complete"] is True
    assert payload["input_binding"]["query_id"] == "arecel_forest10_test_000019"
    assert payload["configurations"][3]["activation_membership"] == [
        MCV_CANDIDATE_A,
        MCV_CANDIDATE_B,
    ]
    with pytest.raises(FileExistsError):
        write_mcv_mechanism_result_json(result, destination)


def test_nonfinite_values_are_not_serialized(tmp_path: Path) -> None:
    result = _result(tmp_path)
    bad = _observation((MCV_CANDIDATE_A,), float("inf"), 110, 0.11)
    bad_result = MechanismResult(
        result.binding,
        result.database_name,
        result.relation_oid,
        (result.observations[0], bad, result.observations[2], result.observations[3]),
        result.sandbox_verification,
        result.cleanup_verified,
    )
    with pytest.raises(MechanismEvaluationError):
        write_mcv_mechanism_result_json(bad_result, tmp_path / "bad.json")
