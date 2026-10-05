from __future__ import annotations

import json
from pathlib import Path

import pytest

from extstats_advisor_research import FROZEN_ADVISOR_SHA, TRANSFER_SOURCE_ADVISOR_SHA
from extstats_advisor_research.full_data_transfer import (
    ACCEPTED_MOVE_ORDER,
    EXPECTED_MEMBERSHIP,
    _wrapped_count_sql,
    build_transfer_records,
    classify_change,
    precedence_check,
    summarize_transfer,
    validate_frozen_sources,
)
from extstats_advisor_research.provenance import reject_credentials


def _search() -> dict:
    return {
        "final_ordered_candidate_ids": list(EXPECTED_MEMBERSHIP),
        "accepted_moves": [{"added_candidate_id": value} for value in ACCEPTED_MOVE_ORDER],
    }


def _singleton() -> dict:
    return {
        "candidate_profiles": [
            {"candidate_id": value, "frozen_precedence_rank": index}
            for index, value in enumerate(EXPECTED_MEMBERSHIP, 1)
        ]
    }


def test_precedence_order_is_not_accepted_move_order() -> None:
    result = precedence_check(
        _search(), _singleton(), {"deployment_ordered_candidate_ids": list(EXPECTED_MEMBERSHIP)}
    )
    assert result["precedence_order"] == list(EXPECTED_MEMBERSHIP)
    assert result["accepted_move_order"] == list(ACCEPTED_MOVE_ORDER)
    assert result["accepted_move_order_differs"] is True


def test_precedence_mismatch_fails_closed() -> None:
    bad = {"deployment_ordered_candidate_ids": list(ACCEPTED_MOVE_ORDER)}
    with pytest.raises(ValueError, match="not precedence order"):
        precedence_check(_search(), _singleton(), bad)


def test_classification_and_three_phase_transfer_records() -> None:
    assert classify_change(2.0, 1.0) == "improved"
    assert classify_change(1.0, 1.0) == "unchanged"
    assert classify_change(1.0, 2.0) == "worsened"
    truth = [{"query_id": "q1", "cardinality": 10}, {"query_id": "q2", "cardinality": 20}]
    sandbox = {
        "q1": {
            "baseline_estimate": 1,
            "final_estimate": 10,
            "baseline_qerror": 10.0,
            "final_qerror": 1.0,
        },
        "q2": {
            "baseline_estimate": 20,
            "final_estimate": 40,
            "baseline_qerror": 1.0,
            "final_qerror": 2.0,
        },
    }
    estimates = {"q1": {"p0": 1, "p1": 10, "p2": 1}, "q2": {"p0": 20, "p1": 40, "p2": 20}}
    records = build_transfer_records(truth, sandbox, estimates)
    assert [row["p2_to_p1_classification"] for row in records] == ["improved", "worsened"]
    assert [row["p0_to_p1_classification"] for row in records] == ["improved", "worsened"]
    summary = summarize_transfer(records)
    assert summary["p2_to_p1_classification"] == {"improved": 1, "unchanged": 0, "worsened": 1}
    assert summary["sandbox_production_correspondence"]["same_direction"] == 2


def test_transfer_qerror_uses_production_floor_one_contract() -> None:
    truth = [
        {"query_id": "zero", "cardinality": 0},
        {"query_id": "nonzero", "cardinality": 10},
    ]
    sandbox = {
        "zero": {
            "baseline_estimate": 0,
            "final_estimate": 0,
            "baseline_qerror": 1.0,
            "final_qerror": 1.0,
        },
        "nonzero": {
            "baseline_estimate": 0,
            "final_estimate": 20,
            "baseline_qerror": 10.0,
            "final_qerror": 2.0,
        },
    }
    records = build_transfer_records(
        truth,
        sandbox,
        {"zero": {"p0": 0, "p1": 1, "p2": 0}, "nonzero": {"p0": 0, "p1": 20, "p2": 0}},
    )
    by_id = {record["query_id"]: record for record in records}
    assert by_id["zero"]["p0_qerror"] == 1.0
    assert by_id["nonzero"]["p0_qerror"] == 10.0
    assert by_id["nonzero"]["p1_qerror"] == 2.0


def test_transfer_bridge_pins_are_explicit() -> None:
    assert TRANSFER_SOURCE_ADVISOR_SHA == "aa65af49fdbbf7443f8fa7295677724babfddfc5"
    assert FROZEN_ADVISOR_SHA == "bb4d58d46e734981a4542de4bcf59441d3effb98"


def test_frozen_selected_varchar_columns_preserve_typmod() -> None:
    root = Path(__file__).parents[1]
    schema = json.loads(
        (root / "runs/bf7fda28d90b3da88e7a14e4/advisor-snapshot/schema.json").read_text()
    )
    candidate_universe = json.loads(
        (root / "runs/bf7fda28d90b3da88e7a14e4/candidate-universe.json").read_text()
    )
    selected = {
        name
        for candidate in candidate_universe["candidates"]
        if candidate["candidate_id"] in EXPECTED_MEMBERSHIP
        for name in candidate["column_names"]
    }
    columns = {
        column["name"]: column
        for column in schema["relations"][0]["columns"]
        if column["name"] in selected
    }
    varchar_columns = {name for name, column in columns.items() if column["arrow_type"] == "string"}
    assert varchar_columns
    assert {columns[name]["native_type"] for name in varchar_columns} == {"character varying(64)"}
    assert {columns[name]["native_collation"] for name in varchar_columns} == {
        'pg_catalog."default"'
    }


def test_source_binding_rejects_wrong_budget(tmp_path) -> None:
    source = tmp_path / "source"
    budget = tmp_path / "budget"
    source.mkdir()
    budget.mkdir()
    with pytest.raises(FileNotFoundError):
        validate_frozen_sources(source, budget)


def test_transfer_artifact_helpers_reject_credentials() -> None:
    with pytest.raises(ValueError, match="credentials"):
        reject_credentials({"production_dsn": "postgresql://u:p@h/db"})


def test_representative_count_wrapper_removes_workload_terminator() -> None:
    assert _wrapped_count_sql("SELECT * FROM public.census13;") == (
        "SELECT count(*) FROM (SELECT * FROM public.census13) AS q"
    )
