from __future__ import annotations

import pytest

from extstats_advisor_research.datasets import forest10
from extstats_advisor_research.forest_baseline import (
    QERROR_CONTRACT,
    compare_paper_postgres,
    paper_references,
    representative_indices,
)
from extstats_advisor_research.provenance import reject_credentials


def test_forest_schema_is_ordered_lowercase_nullable_and_double_precision() -> None:
    assert forest10.RELATION == "public.forest10"
    assert [name for name, _ in forest10.COLUMNS] == [
        "elevation",
        "aspect",
        "slope",
        "horizontal_distance_to_hydrology",
        "vertical_distance_to_hydrology",
        "horizontal_distance_to_roadways",
        "hillshade_9am",
        "hillshade_noon",
        "hillshade_3pm",
        "horizontal_distance_to_fire_points",
    ]
    assert all(type_ == "DOUBLE PRECISION" for _, type_ in forest10.COLUMNS)
    assert forest10.schema_contract()["not_null"] is False


def test_forest_identity_is_schema_aware() -> None:
    baseline = forest10.compute_dataset_content_identity(forest10.CSV_SHA256)
    assert baseline != forest10.compute_dataset_content_identity(
        forest10.CSV_SHA256, columns=(("elevation", "BIGINT"),)
    )
    assert baseline != forest10.compute_dataset_content_identity(forest10.CSV_SHA256, not_null=True)


def test_projection_and_relation_adaptation_preserve_predicates_and_literals() -> None:
    source = (
        'SELECT COUNT(*) FROM public."forest10_original" '
        'WHERE "Elevation" <= 101.25 AND "Aspect" BETWEEN 1.0 AND 2.0 '
        "AND 'forest10_original' = 'forest10_original';"
    )
    adapted = forest10.adapt_query_sql(source)
    assert adapted.startswith("SELECT * FROM public.forest10 WHERE ")
    assert '"elevation" <= 101.25' in adapted
    assert '"aspect" BETWEEN 1.0 AND 2.0' in adapted
    assert "'forest10_original' = 'forest10_original'" in adapted
    assert "COUNT" not in adapted
    with pytest.raises(ValueError, match="source relation"):
        forest10.adapt_query_sql('SELECT COUNT(*) FROM public."other" WHERE 1 = 1;')


def test_representative_truth_selection_is_deterministic_and_covers_contract() -> None:
    records = []
    operators = ["[]", "<=", ">="]
    for index in range(20):
        records.append(
            {
                "source_index": index,
                "truth": 0 if index == 3 else (1_000_000 if index == 17 else index + 1),
                "source_query": {
                    "predicates": [
                        {
                            "column": f"column_{predicate_index}",
                            "operator": operators[(index + predicate_index) % 3],
                            "value": [1, 2],
                        }
                        for predicate_index in range(4)
                    ][: (index % 4) + 1]
                },
            }
        )
    selected = representative_indices(records)
    assert selected == representative_indices(records)
    assert {0, 10, 19, 3, 17}.issubset(selected)
    assert any(len(records[index]["source_query"]["predicates"]) == 4 for index in selected)


def test_paper_reference_comparison_is_external_and_explicit() -> None:
    references = paper_references()
    assert references["postgresql"] == {"p50": 1.21, "p95": 17.0, "p99": 71.0, "max": 9374.0}
    assert references["mscn"]["p95"] == 7.62
    comparison = compare_paper_postgres({"p50": 1.5, "p95": 18.0, "p99": 72.0, "max": 10000.0})
    assert comparison["p50"]["difference"] == pytest.approx(0.29)
    assert QERROR_CONTRACT == "qerror-cardinality-floor-1-v1"
    with pytest.raises(ValueError, match="credentials"):
        reject_credentials({"dsn": "postgresql://secret"})


def test_forest_workload_projection_excludes_truth_fields() -> None:
    workload = forest10._build_workload(
        [
            {
                "source_index": 0,
                "original_sql": 'SELECT COUNT(*) FROM public."forest10"',
                "sql": "SELECT * FROM public.forest10",
                "truth": 1,
            }
        ],
        {"workload_pickle": "w", "label_pickle": "l", "canonical_workload": "c"},
    )
    assert set(workload["queries"][0]) == {"query_id", "sql", "weight"}


def test_forest_source_records_expose_canonical_query_ids() -> None:
    records = forest10.load_test_records()
    assert len(records) == forest10.EXPECTED_TEST_QUERIES
    assert records[0]["query_id"] == "arecel_forest10_test_000000"
    assert records[-1]["query_id"] == "arecel_forest10_test_009999"
