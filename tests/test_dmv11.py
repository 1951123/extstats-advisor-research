from __future__ import annotations

import gzip
import json

import pytest

from extstats_advisor_research.datasets import dmv11
from extstats_advisor_research.forest_baseline import dmv11_paper_references, representative_indices
from extstats_advisor_research.postgres.loader import validate_dmv11_physical_schema


def test_dmv11_schema_is_exact_ordered_nullable_and_typmod_aware() -> None:
    assert dmv11.RELATION == "public.dmv11"
    assert [name for name, _ in dmv11.COLUMNS] == [
        "record_type",
        "registration_class",
        "state",
        "county",
        "body_type",
        "fuel_type",
        "reg_valid_date",
        "color",
        "scofflaw_indicator",
        "suspension_indicator",
        "revocation_indicator",
    ]
    assert sum(type_ == "VARCHAR(64)" for _, type_ in dmv11.COLUMNS) == 10
    assert dmv11.COLUMNS[6] == ("reg_valid_date", "DOUBLE PRECISION")
    assert dmv11.schema_contract()["not_null"] is False
    assert dmv11.schema_contract()["id"] == "arecel-dmv11-postgres-schema-v1"


def test_dmv11_physical_validation_requires_typmods_nullable_columns_and_collations() -> None:
    observed = [
        {
            "name": name,
            "postgres_type": type_.lower(),
            "not_null": False,
            "native_collation": "C.UTF-8" if type_ == "VARCHAR(64)" else None,
        }
        for name, type_ in dmv11.COLUMNS
    ]
    result = validate_dmv11_physical_schema(
        observed,
        row_count=dmv11.EXPECTED_ROWS,
        extended_statistics_count=0,
    )
    assert result["verified"] is True
    assert result["varchar_collations"]["state"] == "C.UTF-8"

    with pytest.raises(ValueError, match="physical schema"):
        validate_dmv11_physical_schema(
            [
                {**item, "postgres_type": "text"} if item["name"] == "state" else item
                for item in observed
            ],
            row_count=dmv11.EXPECTED_ROWS,
            extended_statistics_count=0,
        )
    with pytest.raises(ValueError, match="missing native collation"):
        validate_dmv11_physical_schema(
            [
                {**item, "native_collation": None} if item["name"] == "state" else item
                for item in observed
            ],
            row_count=dmv11.EXPECTED_ROWS,
            extended_statistics_count=0,
        )
    with pytest.raises(ValueError, match="extended statistics"):
        validate_dmv11_physical_schema(
            observed,
            row_count=dmv11.EXPECTED_ROWS,
            extended_statistics_count=1,
        )


def test_dmv11_identity_binds_schema_and_nullability() -> None:
    baseline = dmv11.compute_dataset_content_identity(dmv11.CSV_SHA256)
    assert baseline != dmv11.compute_dataset_content_identity(
        dmv11.CSV_SHA256,
        columns=(("state", "TEXT"),),
    )
    assert baseline != dmv11.compute_dataset_content_identity(dmv11.CSV_SHA256, not_null=True)


def test_dmv11_source_hashes_and_row_count_are_frozen() -> None:
    for value in (
        dmv11.CSV_SHA256,
        dmv11.WORKLOAD_PICKLE_SHA256,
        dmv11.LABEL_PICKLE_SHA256,
        dmv11.CANONICAL_WORKLOAD_SHA256,
        dmv11.ARCHIVE_SHA256,
    ):
        assert len(value) == 64
        int(value, 16)
    assert dmv11.EXPECTED_ROWS == 11_591_877
    assert dmv11.EXPECTED_TEST_QUERIES == 10_000


def test_dmv11_sql_adaptation_preserves_literals_and_adapts_only_identifiers() -> None:
    source = (
        "SELECT COUNT(*) FROM public.dmv11_original "
        "WHERE State = 'Mixed Case, Punctuation!' AND \"Registration_Class\" = 'PAS' "
        "AND Reg_Valid_Date BETWEEN 2017.25 AND 2018.75 "
        "AND Color = 'MiXeD  Case' AND 'State' = 'State';"
    )
    adapted = dmv11.adapt_query_sql(source)
    assert adapted.startswith("SELECT * FROM public.dmv11 WHERE ")
    assert "state = 'Mixed Case, Punctuation!'" in adapted
    assert "\"registration_class\" = 'PAS'" in adapted
    assert "reg_valid_date BETWEEN 2017.25 AND 2018.75" in adapted
    assert "color = 'MiXeD  Case'" in adapted
    assert "'State' = 'State'" in adapted
    assert "COUNT" not in adapted
    with pytest.raises(ValueError, match="source relation"):
        dmv11.adapt_query_sql("SELECT COUNT(*) FROM public.other WHERE State = 'NY';")


def test_dmv11_workload_extraction_has_stable_ids_and_provenance(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "dmv11.canonical.jsonl.gz"
    with gzip.open(canonical, "wt", encoding="utf-8") as stream:
        for index in range(10_000):
            stream.write(
                json.dumps(
                    {
                        "dataset": "dmv11",
                        "index": index,
                        "query_id": f"arecel:dmv11:test:{index:06d}",
                        "source_label": {"cardinality": index},
                        "source_query": {
                            "predicates": [{"column": "State", "operator": "=", "value": "MiXeD"}]
                        },
                        "source_query_sha256": "a" * 64,
                        "split": "test",
                        "sql": "SELECT COUNT(*) FROM public.dmv11_original WHERE State = 'MiXeD';",
                    }
                )
                + "\n"
            )
    monkeypatch.setattr(dmv11, "_audit", lambda value=None: {})
    monkeypatch.setattr(
        dmv11,
        "_source_hashes",
        lambda value=None: {
            "csv": dmv11.CSV_SHA256,
            "workload_pickle": dmv11.WORKLOAD_PICKLE_SHA256,
            "label_pickle": dmv11.LABEL_PICKLE_SHA256,
            "canonical_workload": dmv11.CANONICAL_WORKLOAD_SHA256,
        },
    )
    monkeypatch.setattr(dmv11, "canonical_workload_path", lambda value=None: canonical)
    monkeypatch.setattr(
        dmv11,
        "inspect",
        lambda value=None: {
            "dataset_content_identity": "identity",
            "source_file_sha256": {
                "csv": dmv11.CSV_SHA256,
                "workload_pickle": dmv11.WORKLOAD_PICKLE_SHA256,
                "label_pickle": dmv11.LABEL_PICKLE_SHA256,
                "canonical_workload": dmv11.CANONICAL_WORKLOAD_SHA256,
            },
        },
    )
    output = tmp_path / "workload.json"
    result = dmv11.extract_workload(output)
    value = json.loads(output.read_text())
    assert result["query_count"] == 10_000
    assert value["workload_id"] == "arecel_dmv11_test_v1"
    assert value["queries"][0]["query_id"] == "arecel_dmv11_test_000000"
    assert value["queries"][0]["sql"] == "SELECT * FROM public.dmv11 WHERE state = 'MiXeD';"
    assert value["provenance"]["dataset_content_identity"] == "identity"
    assert set(value["queries"][0]) == {"query_id", "sql", "weight"}


def test_dmv11_truth_sanity_selection_is_deterministic_and_broad() -> None:
    records = [
        {
            "source_index": index,
            "truth": 0 if index == 3 else (11_591_877 if index == 17 else index + 1),
            "source_query": {
                "predicates": [{"column": "State", "operator": "=", "value": "NY"}][
                    : (index % 3) + 1
                ]
                + (
                    [{"column": "Reg_Valid_Date", "operator": ">=", "value": 2017.0}]
                    if index % 4 == 0
                    else []
                ),
            },
        }
        for index in range(20)
    ]
    selected = representative_indices(records)
    assert selected == representative_indices(records)
    assert {0, 10, 19, 3, 17}.issubset(selected)
    assert len(selected) <= 20


def test_dmv11_paper_references_are_rounded_external_metadata() -> None:
    references = dmv11_paper_references()
    assert references["postgresql"] == {"p50": 1.19, "p95": 78.0, "p99": 3255.0, "max": 100000.0}
    assert references["naru"] == {"p50": 1.01, "p95": 1.09, "p99": 1.35, "max": 16.0}
