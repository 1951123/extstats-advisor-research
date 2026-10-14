from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_oracle import (
    OracleDiagnostics,
    OracleEvaluation,
)
from extstats_advisor_research.foundations_precision_retest import (
    PRECISION_QUERY_IDS,
    PrecisionPairRun,
    PrecisionRetestBinding,
    PrecisionRetestResult,
    _quadruple_payload,
    write_precision_retest_result_json,
)
from extstats_advisor_research.foundations_separability import DisjointCandidatePair


def _pair() -> DisjointCandidatePair:
    return DisjointCandidatePair(
        "a",
        "b",
        ("postgresql.mcv", "postgresql.mcv"),
        ("x",),
        ("y",),
        ("q",),
        1,
        2,
    )


def _evaluation(membership: tuple[str, ...], precise: float, rounded: int):
    diagnostics = OracleDiagnostics(
        "cfg",
        membership,
        "complete",
        0,
        1,
        1,
        1,
        False,
    )
    return OracleEvaluation(
        1.0,
        None,
        diagnostics,
        {"q": precise},
        {"q": rounded},
    )


def test_quadruple_payload_retains_rounded_and_precise_values() -> None:
    pair = _pair()
    evaluations = (
        _evaluation((), 10.25, 10),
        _evaluation(("a",), 11.5, 12),
        _evaluation(("b",), 12.0, 12),
        _evaluation(("a", "b"), 13.8, 14),
    )

    payload = _quadruple_payload(pair, evaluations, "q", (1e-12, 1e-9, 1e-6))

    assert payload["rounded_estimates"] == {
        "empty": 10,
        "a": 12,
        "b": 12,
        "ab": 14,
    }
    assert payload["precise_estimates"]["empty"] == 10.25
    assert payload["diagnostic"]["valid"] is True
    assert payload["diagnostic"]["delta"] != 0.0


def test_precision_result_json_is_atomic_and_no_overwrite(tmp_path) -> None:
    pair = _pair()
    evaluations = (
        _evaluation((), 10.0, 10),
        _evaluation(("a",), 11.0, 11),
        _evaluation(("b",), 12.0, 12),
        _evaluation(("a", "b"), 13.2, 13),
    )
    smoke = SimpleNamespace(
        expected_advisor_sha="a" * 40,
        expected_snapshot_digest="b" * 64,
        expected_candidate_universe_digest="c" * 64,
        expected_ground_truth_digest="d" * 64,
        dataset_id="forest10",
        relation_id="forest10",
        relation_schema="public",
        relation_name="forest10",
        workload_id="workload",
        sample_rows=30_000,
        statistics_target=100,
        query_ids=PRECISION_QUERY_IDS,
        global_precedence=("a", "b"),
    )
    binding = PrecisionRetestBinding(
        smoke,
        "e" * 40,
        "f" * 40,
        tmp_path / "historical.json",
        "0" * 64,
    )
    result = PrecisionRetestResult(
        binding,
        (PrecisionPairRun(pair, ("q",), evaluations, {}, "exp_test"),),
        "exp_test",
        {},
        4,
        4,
    )
    destination = tmp_path / "result.json"

    write_precision_retest_result_json(result, destination)

    decoded = json.loads(destination.read_text())
    assert decoded["complete"] is True
    assert decoded["quadruples"][0]["precise_estimates"]["ab"] == 13.2
    with pytest.raises(FileExistsError):
        write_precision_retest_result_json(result, destination)
