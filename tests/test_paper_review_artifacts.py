import json
from pathlib import Path

from extstats_advisor_research.provenance import semantic_digest

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str) -> dict:
    return json.loads((ROOT / "paper" / name).read_text(encoding="utf-8"))


def test_current_review_artifacts_have_valid_digests() -> None:
    for name in (
        "evidence-to-manuscript-v2.json",
        "scientific-claims-audit-v1.json",
        "source-to-paper-technical-mapping-v1.json",
        "submission-readiness-review-v1.json",
        "research-backlog-v1.json",
    ):
        artifact = _load(name)
        assert artifact["semantic_digest"] == semantic_digest(
            {key: value for key, value in artifact.items() if key != "semantic_digest"}
        )


def test_claim_mapping_preserves_scopes_and_posthoc_status() -> None:
    artifact = _load("evidence-to-manuscript-v2.json")
    entries = {item["experiment_id"]: item for item in artifact["entries"]}
    assert entries["rq1-held-out-workload-generalization"]["status"] == "confirmatory-validated"
    assert entries["postgresql-extstats-oid-order-sensitivity-v1"]["status"] == (
        "qualified-validated"
    )
    assert entries["native-analyze-stability-v1-v2"]["status"] == "posthoc-qualified"
    assert (
        "not global ordering optimality or runtime improvement"
        in entries["postgresql-extstats-oid-order-sensitivity-v1"]["limitations"]
    )


def test_claims_audit_covers_major_scientific_scopes() -> None:
    artifact = _load("scientific-claims-audit-v1.json")
    locations = {item["location"] for item in artifact["entries"]}
    assert "results, subsection:native-analyze-stability, discussion" in locations
    assert "results, table:rq5-practicality, discussion, conclusion" in locations
    assert "global optimality of membership or ordering" in artifact["unsupported_claims"]


def test_source_mapping_binds_frozen_revisions_and_system_paths() -> None:
    artifact = _load("source-to-paper-technical-mapping-v1.json")
    assert artifact["source_revisions"]["advisor"] == ("e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3")
    assert artifact["source_revisions"]["patched_postgresql"] == (
        "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
    )
    assert any(
        "hypothetical.c" in item
        for entry in artifact["entries"]
        for item in entry["source_locations"]
    )
    assert any(
        "recommendation.py" in item
        for entry in artifact["entries"]
        for item in entry["source_locations"]
    )


def test_readiness_and_backlog_do_not_promote_missing_science() -> None:
    readiness = _load("submission-readiness-review-v1.json")
    assert readiness["overall_status"] == "not-submission-ready"
    assert any(item["status"] == "blocking" for item in readiness["checks"])

    backlog = _load("research-backlog-v1.json")
    assert any("fixed-evaluation-budget" in item for item in backlog["explicitly_not_recommended"])
