from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.datasets import power7
from extstats_advisor_research.forest_baseline import power7_paper_references
from extstats_advisor_research.postgres.loader import validate_power7_physical_schema
from extstats_advisor_research.runner import run_power7


def test_power7_schema_is_exact_lowercase_nullable_double_precision() -> None:
    assert power7.RELATION == "public.power7"
    assert [name for name, _ in power7.COLUMNS] == [
        "global_active_power",
        "global_reactive_power",
        "voltage",
        "global_intensity",
        "sub_metering_1",
        "sub_metering_2",
        "sub_metering_3",
    ]
    assert all(type_ == "DOUBLE PRECISION" for _, type_ in power7.COLUMNS)
    assert power7.schema_contract()["not_null"] is False


def test_power7_loader_contract_rejects_not_null_or_extra_statistics() -> None:
    observed = [
        {"name": name, "postgres_type": "double precision", "not_null": False}
        for name, _ in power7.COLUMNS
    ]
    result = validate_power7_physical_schema(
        observed, row_count=power7.EXPECTED_ROWS, extended_statistics_count=0
    )
    assert result["verified"] is True
    with pytest.raises(ValueError, match="physical schema"):
        validate_power7_physical_schema(
            [{**item, "not_null": True} for item in observed],
            row_count=power7.EXPECTED_ROWS,
            extended_statistics_count=0,
        )
    with pytest.raises(ValueError, match="extended statistics"):
        validate_power7_physical_schema(
            observed,
            row_count=power7.EXPECTED_ROWS,
            extended_statistics_count=1,
        )


def test_power7_identity_is_schema_aware() -> None:
    baseline = power7.compute_dataset_content_identity(power7.CSV_SHA256)
    assert baseline != power7.compute_dataset_content_identity(
        power7.CSV_SHA256, columns=(("voltage", "BIGINT"),)
    )
    assert baseline != power7.compute_dataset_content_identity(power7.CSV_SHA256, not_null=True)


def test_power7_sql_adaptation_preserves_literals_and_predicates() -> None:
    source = (
        'SELECT COUNT(*) FROM public."power7" '
        'WHERE "Global_active_power" <= 1.23456789012345 '
        'AND "Voltage" BETWEEN 230.0 AND 240.0 '
        "AND 'Global_active_power' = 'Global_active_power';"
    )
    adapted = power7.adapt_query_sql(source)
    assert adapted.startswith("SELECT * FROM public.power7 WHERE ")
    assert '"global_active_power" <= 1.23456789012345' in adapted
    assert '"voltage" BETWEEN 230.0 AND 240.0' in adapted
    assert "'Global_active_power' = 'Global_active_power'" in adapted
    with pytest.raises(ValueError, match="source relation"):
        power7.adapt_query_sql('SELECT COUNT(*) FROM public."other" WHERE 1 = 1;')


def test_power7_hash_contract_is_frozen() -> None:
    for value in (
        power7.CSV_SHA256,
        power7.WORKLOAD_PICKLE_SHA256,
        power7.LABEL_PICKLE_SHA256,
        power7.CANONICAL_WORKLOAD_SHA256,
        power7.ARCHIVE_SHA256,
    ):
        assert len(value) == 64
        assert int(value, 16) >= 0
    assert power7.EXPECTED_ROWS == 2_075_259
    assert power7.EXPECTED_TEST_QUERIES == 10_000


def test_power7_workload_extraction_projects_production_fields(tmp_path, monkeypatch) -> None:
    canonical = tmp_path / "power7.canonical.jsonl.gz"
    with gzip.open(canonical, "wt", encoding="utf-8") as stream:
        for index in range(10_000):
            stream.write(
                json.dumps(
                    {
                        "dataset": "power7",
                        "index": index,
                        "query_id": f"arecel:power7:test:{index:06d}",
                        "source_label": {"cardinality": index},
                        "source_query": {"predicates": []},
                        "source_query_sha256": "a" * 64,
                        "split": "test",
                        "sql": 'SELECT COUNT(*) FROM public."power7" WHERE "Voltage" >= 1.25;',
                    }
                )
                + "\n"
            )
    monkeypatch.setattr(power7, "_audit", lambda value=None: {})
    monkeypatch.setattr(
        power7,
        "_source_hashes",
        lambda value=None: {
            "workload_pickle": power7.WORKLOAD_PICKLE_SHA256,
            "label_pickle": power7.LABEL_PICKLE_SHA256,
            "canonical_workload": power7.CANONICAL_WORKLOAD_SHA256,
            "csv": power7.CSV_SHA256,
        },
    )
    monkeypatch.setattr(power7, "canonical_workload_path", lambda value=None: canonical)
    output = tmp_path / "workload.json"
    result = power7.extract_workload(output)
    value = json.loads(output.read_text())
    assert result["query_count"] == 10_000
    assert value["workload_id"] == "arecel_power7_test_v1"
    assert value["queries"][0]["query_id"] == "arecel_power7_test_000000"
    assert set(value["queries"][0]) == {"query_id", "sql", "weight"}
    assert value["queries"][0]["sql"].endswith('"voltage" >= 1.25;')


def test_power7_cli_dispatch_and_fixed_settings() -> None:
    args = _parser().parse_args(
        [
            "run",
            power7.BENCHMARK_ID,
            "--production-dsn",
            "stock",
            "--planner-dsn",
            "planner",
        ]
    )
    assert args.dataset_id == power7.BENCHMARK_ID
    assert args.search_wall_clock_seconds is None
    with pytest.raises(ValueError, match="canonical settings"):
        run_power7(
            production_dsn="stock",
            planner_dsn="planner",
            advisor_root=Path("/tmp/advisor"),
            patched_postgres_root=Path("/tmp/postgres"),
            output_root=Path("/tmp/power7-test-run"),
            sample_seed=41,
        )


def test_power7_paper_references_match_table_4() -> None:
    assert power7_paper_references()["postgresql"] == {
        "p50": 1.06,
        "p95": 15.0,
        "p99": 235.0,
        "max": 200000.0,
    }
