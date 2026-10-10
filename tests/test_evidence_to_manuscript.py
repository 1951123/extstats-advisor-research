from __future__ import annotations

import json
from pathlib import Path

from extstats_advisor_research.provenance import semantic_digest

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / "paper/evidence-to-manuscript-v1.json"


def test_evidence_to_manuscript_ledger_binds_committed_sources() -> None:
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    assert ledger["format_version"] == "evidence-to-manuscript-v1"
    assert ledger["source_commit"] == "e3e32e0316bfd57432ff9485768ceb970bc3b29d"

    statuses = {entry["status"] for entry in ledger["entries"]}
    assert "confirmatory-validated" in statuses
    assert "qualified-partial" in statuses
    assert "planned" in statuses
    assert "implementation-needed" in statuses

    for entry in ledger["entries"]:
        for source in entry["sources"]:
            path = ROOT / source["path"]
            assert path.is_file(), source["path"]
            artifact = json.loads(path.read_text(encoding="utf-8"))
            assert artifact["semantic_digest"] == source["semantic_digest"]
            assert artifact["semantic_digest"] == semantic_digest(
                {key: value for key, value in artifact.items() if key != "semantic_digest"}
            )

        if entry["status"] in {"planned", "implementation-needed"}:
            assert entry["sources"] == []
            assert entry["new_scientific_execution_required"] is True
        else:
            assert entry["sources"]
            assert entry["new_scientific_execution_required"] is False


def test_evidence_to_manuscript_ledger_does_not_promote_incomplete_studies() -> None:
    ledger = json.loads(LEDGER_PATH.read_text(encoding="utf-8"))
    by_id = {entry["experiment_id"]: entry for entry in ledger["entries"]}
    assert by_id["rq4-fixed-evaluation-budget"]["status"] == "implementation-needed"
    assert by_id["rq4-native-analyze-stability"]["status"] == "planned"
    assert by_id["rq5-cost-accounting"]["status"] == "qualified-partial"
    assert by_id["postgresql-extstats-oid-order-sensitivity-v1"]["status"] == (
        "qualified-validated"
    )
