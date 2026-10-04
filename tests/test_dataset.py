from __future__ import annotations

import gzip
import json

from extstats_advisor_research.datasets import census13


def test_schema_mapping_and_logical_provenance() -> None:
    result = census13.inspect()
    assert result["benchmark_id"] == "arecel-census13"
    assert result["relation"] == "public.census13"
    assert result["schema_contract"]["id"] == "arecel-census13-postgres-schema-v1"
    assert [(column["name"], column["postgres_type"]) for column in result["columns"]] == list(
        census13.COLUMNS
    )
    assert len(result["columns"]) == 13
    assert "/home/wqts" not in json.dumps(result["data_root_logical"])


def test_schema_contract_is_part_of_dataset_identity() -> None:
    same_source_different_schema = census13.compute_dataset_content_identity(
        census13.CSV_SHA256,
        schema_contract_id="fixture-schema-v2",
        columns=(("age", "BIGINT"),),
    )
    assert same_source_different_schema != census13.compute_dataset_content_identity(
        census13.CSV_SHA256
    )


def test_inspect_and_manifest_share_dataset_identity(tmp_path) -> None:
    inspected = census13.inspect()
    manifest = census13.write_dataset_manifest(tmp_path / "dataset-manifest.json")
    assert inspected["dataset_content_identity"] == manifest["dataset_content_identity"]
    assert manifest["schema_contract"] == inspected["schema_contract"]


def test_workload_extraction_preserves_audited_sql(tmp_path, monkeypatch) -> None:
    audit = tmp_path / "arecel" / "audit-v1"
    source = tmp_path / "arecel" / "raw" / "data" / "census13"
    audit.mkdir(parents=True)
    source.mkdir(parents=True)
    monkeypatch.setenv("EXTSTATS_RESEARCH_DATA_ROOT", str(tmp_path))
    (audit / "audit.json").write_text(
        json.dumps(
            {
                "archive_expected_sha256": census13.ARCHIVE_SHA256,
                "upstream": {"AreCELearnedYet": {"commit": census13.UPSTREAM_COMMIT}},
                "datasets": {"census13": {"csv": {"sha256": census13.CSV_SHA256}}},
            }
        ),
        encoding="utf-8",
    )
    with gzip.open(audit / "census13.canonical.jsonl.gz", "wt", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "split": "test",
                    "query_id": "q1",
                    "sql": 'SELECT COUNT(*) FROM public."census13";',
                }
            )
            + "\n"
        )
        stream.write(json.dumps({"split": "train", "query_id": "q2", "sql": "SELECT 2"}) + "\n")
    result = census13.extract_workload(tmp_path / "workload.json", split="test")
    assert result["query_count"] == 1
    workload = json.loads((tmp_path / "workload.json").read_text())
    assert workload["workload_id"] == "arecel_census13_test_v1"
    assert workload["queries"][0]["query_id"] == "arecel_census13_test_000000"
    assert workload["queries"][0]["sql"] == 'SELECT * FROM public."census13";'
    assert workload["provenance"]["query_id_mapping"] == {"arecel_census13_test_000000": "q1"}
    assert workload["provenance"]["projection_adapter"] == "count-star-to-select-star-v1"
