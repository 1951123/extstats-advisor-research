from __future__ import annotations

from extstats_advisor_research.type_coercion import (
    ADVISOR_NUMERIC_COLUMNS,
    HYBRID_H1_COLUMNS,
    HYBRID_H2_COLUMNS,
    PAPER_NUMERIC_COLUMNS,
    classify_numeric_literal,
    numeric_predicates,
    schema_contract,
    workload_numeric_summary,
)


def test_numeric_literal_lexical_classification() -> None:
    assert classify_numeric_literal("55") == "integer"
    assert classify_numeric_literal("55.0") == "decimal"
    assert classify_numeric_literal("55.3721") == "decimal"
    assert classify_numeric_literal("1e+03") == "scientific"


def test_numeric_predicate_extraction_and_counts() -> None:
    queries = [
        {
            "sql": 'SELECT * FROM public."arecel_paper_census13" '
            'WHERE "age" <= 55.0 AND "sex" = \'Male\';'
        },
        {
            "sql": 'SELECT * FROM public."arecel_paper_census13" '
            'WHERE "hours_per_week" BETWEEN 1 AND 2.5;'
        },
    ]
    assert [item["lexical_form"] for item in numeric_predicates(queries[0]["sql"])] == ["decimal"]
    summary = workload_numeric_summary(queries)
    assert summary["numeric_predicate_instances"] == 3
    assert summary["lexical_form_counts"] == {"integer": 1, "decimal": 2, "scientific": 0}
    assert summary["queries_containing_decimal_numeric_predicate"] == 2


def test_hybrid_schema_definitions_keep_only_requested_type_axes() -> None:
    paper = {item["name"]: item["postgres_type"] for item in schema_contract(PAPER_NUMERIC_COLUMNS)}
    advisor = {
        item["name"]: item["postgres_type"] for item in schema_contract(ADVISOR_NUMERIC_COLUMNS)
    }
    h1 = {item["name"]: item["postgres_type"] for item in schema_contract(HYBRID_H1_COLUMNS)}
    h2 = {item["name"]: item["postgres_type"] for item in schema_contract(HYBRID_H2_COLUMNS)}
    assert paper["age"] == "DOUBLE PRECISION"
    assert paper["workclass"] == "VARCHAR(64)"
    assert advisor["age"] == "BIGINT"
    assert advisor["workclass"] == "VARCHAR(64)"
    assert h1["age"] == "DOUBLE PRECISION"
    assert h1["workclass"] == "TEXT"
    assert h2 == advisor
