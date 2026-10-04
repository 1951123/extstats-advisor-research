from __future__ import annotations

from extstats_advisor_research.baseline_gap import (
    comparison_ladder,
    diagnostic_query_sql,
    relation_sql,
    scaled_sample_rows,
    summary_for,
    tail_records,
    zero_sample_summary,
)


def _record(query_id: str = "arecel_census13_test_005803", sample: int = 0) -> dict:
    return {
        "query_id": query_id,
        "weight": 1.0,
        "true_rows": 41660,
        "paper_target10000_qerror": 1.0,
        "paper_target100_qerror": 2.0,
        "advisor_schema_full_target100_qerror": 3.0,
        "advisor_schema_full_target10000_qerror": 1.5,
        "sample_true_rows": sample,
        "scaled_sample_rows": sample * 4.8842,
        "sample_oracle_qerror": 4.0,
        "advisor_sandbox_estimate": 1,
        "advisor_sandbox_qerror": 41660.0,
        "advisor_final_estimate": 16,
        "advisor_final_qerror": 2603.75,
    }


def test_relation_and_query_mapping_are_fixed_and_safe() -> None:
    assert relation_sql("baseline_paper_t100") == '"public"."baseline_paper_t100"'
    source = 'SELECT * FROM public."arecel_paper_census13" WHERE "age" <= 5;'
    assert diagnostic_query_sql(source, '"public"."baseline_paper_t100"') == (
        'SELECT * FROM "public"."baseline_paper_t100" WHERE "age" <= 5;'
    )


def test_scaled_sample_rows_and_summary_use_exact_values() -> None:
    assert scaled_sample_rows(5, 10_000, 48_842) == 24.421
    records = [_record("q1", 0), {**_record("q2", 2), "true_rows": 2}]
    assert summary_for(records, "sample_oracle_qerror")["mean"] == 4.0
    assert comparison_ladder(records)["A"]["mean"] == 1.0


def test_zero_coverage_and_tail_extraction() -> None:
    records = [_record()]
    records[0].update(
        {
            "paper_target10000_estimate": 41642,
            "paper_target10000_qerror": 1.0004322558954901,
            "paper_target100_estimate": 100,
            "paper_target100_qerror": 416.6,
            "advisor_schema_full_target100_estimate": 100,
            "advisor_schema_full_target100_qerror": 416.6,
            "advisor_schema_full_target10000_estimate": 41642,
            "advisor_schema_full_target10000_qerror": 1.0004322558954901,
            "sample_oracle_qerror": 41660.0,
            "advisor_final_estimate": 16,
            "advisor_final_qerror": 2603.75,
        }
    )
    result = zero_sample_summary(records)
    assert result["count"] == 1
    assert result["fraction"] == 1.0
    tail = [
        _record(query_id)
        for query_id in (
            "arecel_census13_test_005803",
            "arecel_census13_test_007775",
            "arecel_census13_test_008202",
            "arecel_census13_test_007718",
            "arecel_census13_test_007098",
            "arecel_census13_test_008766",
            "arecel_census13_test_005549",
            "arecel_census13_test_004272",
            "arecel_census13_test_002620",
            "arecel_census13_test_001940",
        )
    ]
    assert tail_records(tail)[0]["query_id"] == "arecel_census13_test_005803"
