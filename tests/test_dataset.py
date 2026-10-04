from __future__ import annotations

import gzip
import json

from extstats_advisor_research.datasets import census13


def test_schema_mapping_and_logical_provenance() -> None:
    result = census13.inspect()
    assert result["benchmark_id"] == "arecel-census13"
    assert result["relation"] == "public.census13"
    assert [column["name"] for column in result["columns"]] == [
        name for name, _ in census13.COLUMNS
    ]
    assert "/home/wqts" not in json.dumps(result["data_root_logical"])


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
        stream.write(json.dumps({"split": "test", "query_id": "q1", "sql": "SELECT 1"}) + "\n")
        stream.write(json.dumps({"split": "train", "query_id": "q2", "sql": "SELECT 2"}) + "\n")
    result = census13.extract_workload(tmp_path / "workload.json", split="test")
    assert result["query_count"] == 1
    assert json.loads((tmp_path / "workload.json").read_text())["queries"][0]["sql"] == "SELECT 1"
