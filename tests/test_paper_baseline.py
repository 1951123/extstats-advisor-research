from __future__ import annotations

from extstats_advisor_research.paper_baseline import (
    PAPER_COLUMNS,
    comparison,
    paper_schema,
    paper_select_sql,
    qerror_compatibility,
    summarize,
)


def test_paper_schema_matches_upstream_contract() -> None:
    assert paper_schema() == [
        {"name": name, "postgres_type": type_} for name, type_ in PAPER_COLUMNS
    ]
    assert paper_schema()[0]["postgres_type"] == "DOUBLE PRECISION"
    assert paper_schema()[1]["postgres_type"] == "VARCHAR(64)"


def test_source_query_is_rendered_as_upstream_row_preserving_sql() -> None:
    source = 'SELECT COUNT(*) FROM public."census13" WHERE "age" <= 3;'
    assert (
        paper_select_sql(source) == 'SELECT * FROM public."arecel_paper_census13" WHERE "age" <= 3;'
    )


def test_qerror_matches_upstream_zero_rules() -> None:
    for estimate, truth in ((0, 0), (0, 4), (4, 0), (9, 3), (3, 9)):
        result = qerror_compatibility(estimate, truth)
        assert result["matches"] is True


def test_summary_and_paper_comparison() -> None:
    records = [
        {"query_id": "q1", "true_rows": 1, "estimated_rows": 1, "qerror": 1.0},
        {"query_id": "q2", "true_rows": 1, "estimated_rows": 4, "qerror": 4.0},
        {"query_id": "q3", "true_rows": 1, "estimated_rows": 2, "qerror": 2.0},
    ]
    result = summarize(records)
    assert result["max_query_id"] == "q2"
    assert result["p50"] == 2.0
    assert comparison({"p50": 1.4, "p95": 18.6, "p99": 58.0, "max": 1635.0})["p50"]["ratio"] == 1.0
