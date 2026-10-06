from __future__ import annotations

import copy
import json
import os

import pytest

from extstats_advisor_research import FROZEN_ADVISOR_SHA, FROZEN_PATCHED_POSTGRES_SHA
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq3_fidelity import (
    MISMATCH_CATEGORIES,
    _relation_identity,
    build_fidelity_artifact,
    classify_plan_rows_mismatch,
    fidelity_gate,
    fidelity_run_result,
    inspect_fidelity_artifact,
    paired_plan_rows_metrics,
    run_synthetic_fidelity,
    validate_fidelity_artifact,
    write_fidelity_artifact,
)

_DIGEST = "a" * 64
_ORDINARY_DIGEST = "b" * 64
_RESEARCH_SHA = "c" * 40


def _configuration(configuration_id: str, candidate_ids: list[str], oids: list[int]) -> dict:
    kinds = {"mcv": "mcv", "fd": "dependencies"}
    payloads = {
        candidate_id: (str(index + 1) * 64) for index, candidate_id in enumerate(candidate_ids)
    }
    physical_explain = []
    hypothetical_explain = []
    paired = []
    for query_index, query_id in enumerate(("q1", "q2")):
        physical_document = {"Plan": {"Node": "Seq Scan", "Plan Rows": query_index + 10}}
        hypothetical_document = copy.deepcopy(physical_document)
        physical_record = {
            "query_id": query_id,
            "sql": f"SELECT * FROM fixture WHERE x = {query_index}",
            "plan_rows": query_index + 10,
            "explain": physical_document,
            "explain_sha256": semantic_digest(physical_document),
        }
        hypothetical_record = {**physical_record, "explain": hypothetical_document}
        hypothetical_record["explain_sha256"] = semantic_digest(hypothetical_document)
        physical_explain.append(physical_record)
        hypothetical_explain.append(hypothetical_record)
        paired.append(
            {
                "query_id": query_id,
                "sql": physical_record["sql"],
                "physical_plan_rows": physical_record["plan_rows"],
                "hypothetical_plan_rows": hypothetical_record["plan_rows"],
                "no_extstats_baseline_plan_rows": physical_record["plan_rows"],
                "physical_estimate_changed_from_no_extstats_baseline": False,
                "physical_explain": physical_document,
                "hypothetical_explain": hypothetical_document,
                "physical_explain_sha256": physical_record["explain_sha256"],
                "hypothetical_explain_sha256": hypothetical_record["explain_sha256"],
                "mismatch_evidence": {
                    "payload_correspondence": True,
                    "ordinary_stats_equal": True,
                    "physical_catalog_order_equal": True,
                    "planner_settings_equal": True,
                    "overlay_resolution_equal": True,
                },
            }
        )
    objects = [
        {
            "candidate_id": candidate_id,
            "kind": kinds[candidate_id],
            "name": f"stats_{candidate_id}",
            "oid": oid,
            "keys": "1 2",
            "catalog_kinds": "{m}" if candidate_id == "mcv" else "{f}",
            "definition": f"(x, y) {candidate_id}",
            "payload_sha256": payloads[candidate_id],
            "payload_size": 64,
        }
        for candidate_id, oid in zip(candidate_ids, oids, strict=True)
    ]
    controls = {
        "identical_binary": True,
        "identical_relation_contents": True,
        "identical_schema": True,
        "identical_workload": True,
        "ordinary_stats_equal": True,
        "identical_statistics_target": True,
        "equivalent_design": True,
        "payload_correspondence_verified": True,
        "identical_planner_settings": True,
        "physical_catalog_order_equal": True,
        "overlay_resolution_verified": True,
    }
    return {
        "configuration_id": configuration_id,
        "settings": {
            "physical": {"transaction_isolation": "repeatable read", "transaction_read_only": "on"},
            "hypothetical": {
                "transaction_isolation": "repeatable read",
                "transaction_read_only": "on",
            },
            "statistics_target": 100,
        },
        "controls": controls,
        "physical": {
            "relation": {"schema": "pg_temp", "name": "fixture", "oid": 1001},
            "candidate_ids": candidate_ids,
            "catalog_order_candidate_ids": candidate_ids,
            "ordinary_stats_fingerprint": _ORDINARY_DIGEST,
            "payload_correspondence": "exact-bytes-from-physical-source",
            "objects": objects,
            "explain": physical_explain,
            "mechanism_evidence": {
                "payloads_nonempty": True,
                "supported_clause_form": "simple equality conjunction over (a, b)",
            },
        },
        "hypothetical": {
            "active_candidate_ids": candidate_ids,
            "virtual_oids": [oid + 10000 for oid in oids],
            "ordinary_stats_fingerprint": _ORDINARY_DIGEST,
            "payload_source": "physical-extracted-payloads",
            "payload_sha256": payloads,
            "explain": hypothetical_explain,
        },
        "paired_queries": paired,
        "cleanup": {
            "verified": True,
            "physical_extstats_count_after": 0,
            "overlay_active_after": None,
        },
    }


def _artifact() -> dict:
    return build_fidelity_artifact(
        experiment_id="rq3-synthetic-test",
        system={
            "research_commit_sha": _RESEARCH_SHA,
            "advisor_commit_sha": FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
            "patched_backend_contract": "postgresql-pgextadv-16.14-v1",
            "patched_server_version": "16.14",
        },
        fixture={"fixture_id": "fixture", "workload_digest": _DIGEST},
        configurations=[
            _configuration("mcv-only", ["mcv"], [101]),
            _configuration("fd-only", ["fd"], [201]),
            _configuration("mcv-plus-fd", ["mcv", "fd"], [301, 302]),
        ],
    )


def _resign(artifact: dict) -> dict:
    artifact["semantic_digest"] = semantic_digest(
        {key: value for key, value in artifact.items() if key != "semantic_digest"}
    )
    return artifact


def test_plan_rows_metrics_are_direct_and_exact() -> None:
    records = [
        {
            "query_id": "q1",
            "physical_plan_rows": 0,
            "hypothetical_plan_rows": 2,
            "exact_match": False,
            "absolute_delta": 2,
            "relative_delta": 2.0,
            "mismatch_category": "plan-rows-mismatch-unexplained",
        },
        {
            "query_id": "q2",
            "physical_plan_rows": 4,
            "hypothetical_plan_rows": 4,
            "exact_match": True,
            "absolute_delta": 0,
            "relative_delta": 0.0,
            "mismatch_category": "exact-match",
        },
    ]
    result = paired_plan_rows_metrics(records)
    assert result["query_count"] == 2
    assert result["exact_match_fraction"] == 0.5
    assert result["max_absolute_delta"] == 2


def test_mismatch_classification_requires_evidence() -> None:
    evidence = {
        "payload_correspondence": True,
        "ordinary_stats_equal": False,
        "physical_catalog_order_equal": True,
        "planner_settings_equal": True,
        "overlay_resolution_equal": True,
    }
    assert classify_plan_rows_mismatch(5, 4, evidence) == "ordinary-stats-drift"
    with pytest.raises(ValueError, match="missing boolean field"):
        classify_plan_rows_mismatch(5, 4, {"payload_correspondence": True})


def test_fidelity_artifact_round_trips_and_digest_is_stable(tmp_path) -> None:
    artifact = _artifact()
    path = tmp_path / "rq3-fidelity-v1.json"
    write_fidelity_artifact(path, artifact)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert validate_fidelity_artifact(loaded) == artifact["summary"]
    assert inspect_fidelity_artifact(path)["semantic_digest"] == artifact["semantic_digest"]


def test_fidelity_artifact_fails_closed_on_digest_or_cleanup_drift() -> None:
    artifact = _artifact()
    broken = {**artifact, "summary": {"query_count": 0}}
    with pytest.raises(ValueError, match="digest mismatch"):
        validate_fidelity_artifact(broken)
    broken = copy.deepcopy(artifact)
    broken["configurations"][0]["cleanup"]["verified"] = False
    with pytest.raises(ValueError, match="cleanup"):
        validate_fidelity_artifact(_resign(broken))


@pytest.mark.parametrize(
    "field", ["research_commit_sha", "advisor_commit_sha", "patched_postgres_commit_sha"]
)
def test_git_sha_must_be_a_40_character_source_revision(field: str) -> None:
    artifact = _artifact()
    artifact["system"][field] = _DIGEST
    with pytest.raises(ValueError, match="SHA-1 token"):
        validate_fidelity_artifact(_resign(artifact))


def test_settings_must_match_between_realizations() -> None:
    artifact = _artifact()
    artifact["configurations"][0]["settings"]["hypothetical"]["timezone"] = "UTC"
    with pytest.raises(ValueError, match="settings differ"):
        validate_fidelity_artifact(_resign(artifact))


def test_payload_digest_correspondence_is_cross_checked() -> None:
    artifact = _artifact()
    artifact["configurations"][0]["physical"]["objects"][0]["payload_sha256"] = _DIGEST
    with pytest.raises(ValueError, match="payload digests"):
        validate_fidelity_artifact(_resign(artifact))


def test_candidate_order_is_cross_checked() -> None:
    artifact = _artifact()
    artifact["configurations"][2]["hypothetical"]["active_candidate_ids"] = ["fd", "mcv"]
    with pytest.raises(ValueError, match="candidate ordering"):
        validate_fidelity_artifact(_resign(artifact))


@pytest.mark.parametrize("virtual_oids", [[30100, 30100], [30100]])
def test_virtual_oids_must_be_unique_and_complete(virtual_oids: list[int]) -> None:
    artifact = _artifact()
    artifact["configurations"][2]["hypothetical"]["virtual_oids"] = virtual_oids
    with pytest.raises(ValueError, match="virtual OIDs"):
        validate_fidelity_artifact(_resign(artifact))


def test_explain_digest_is_recomputed() -> None:
    artifact = _artifact()
    artifact["configurations"][0]["physical"]["explain"][0]["explain"]["tampered"] = True
    with pytest.raises(ValueError, match="EXPLAIN semantic digest"):
        validate_fidelity_artifact(_resign(artifact))


def test_fidelity_gate_records_mismatch_as_failure_without_ready_status() -> None:
    assert fidelity_gate({"mismatch_count": 0}) == "pass"
    assert fidelity_gate({"mismatch_count": 1}) == "fail"


def test_runner_result_contract_preserves_a_mismatch_as_an_artifact(tmp_path) -> None:
    artifact = _artifact()
    configurations = copy.deepcopy(artifact["configurations"])
    configurations[0]["paired_queries"][0]["hypothetical_plan_rows"] += 1
    mismatching = build_fidelity_artifact(
        experiment_id=artifact["experiment_id"],
        system=artifact["system"],
        fixture=artifact["fixture"],
        configurations=configurations,
    )
    result = fidelity_run_result(mismatching, tmp_path / "mismatch.json")
    assert result["status"] == "artifact-created"
    assert result["fidelity_gate"] == "fail"
    assert result["summary"]["mismatch_count"] == 1


def test_categories_are_closed() -> None:
    assert "exact-match" in MISMATCH_CATEGORIES
    assert "statistics-order-mismatch" in MISMATCH_CATEGORIES
    assert "physical-object-selection-difference" not in MISMATCH_CATEGORIES
    assert "plan-rows-mismatch-unexplained" in MISMATCH_CATEGORIES


def test_temporary_catalog_namespace_matches_explain_namespace() -> None:
    class Result:
        def fetchone(self):
            return ("pg_temp_7", "fixture")

    class Connection:
        def execute(self, query, parameters):
            return Result()

    assert _relation_identity(Connection(), 1001) == ("pg_temp", "fixture")


@pytest.mark.integration
def test_synthetic_mcv_fd_live_fixture(tmp_path) -> None:
    dsn = os.environ.get("EXTSTATS_RQ3_PATCHED_DSN")
    if not dsn:
        pytest.skip("set EXTSTATS_RQ3_PATCHED_DSN to run the small patched-PostgreSQL fixture")
    result = run_synthetic_fidelity(
        dsn=dsn,
        output=tmp_path / "rq3-fidelity-v1.json",
    )
    assert result["status"] == "artifact-created"
    assert result["fidelity_gate"] == "pass"
    assert result["configuration_count"] == 3
    assert result["summary"]["mismatch_count"] == 0
