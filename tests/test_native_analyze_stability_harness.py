"""Offline state-machine and control tests for Native ANALYZE Stability v1."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from extstats_advisor_research.native_analyze_stability import METHOD_ORDER
from extstats_advisor_research.native_analyze_stability_harness import (
    IncompleteEvidenceError,
    LiveExecutionNotAuthorized,
    MockStabilityAdapter,
    StabilityError,
    aggregate_realizations,
    canonical_union,
    dry_run_plan,
    execute_campaign,
    execute_mock_invocation,
    filter_membership,
    make_realization_id,
    output_scope,
    paired_counts,
    validate_invocation_artifact,
    verify_clone_controls,
    verify_oid_order_controls,
    write_failure,
)


def _protocol() -> dict[str, object]:
    return {
        "format_version": "native-analyze-stability-protocol-v1",
        "semantic_digest": "protocol-fixture",
        "datasets": {"primary": ["arecel-forest10"]},
        "realizations": {"count": 5},
        "method_roster": list(METHOD_ORDER),
    }


def _memberships() -> dict[str, list[str]]:
    return {
        method: ["cand-a", "cand-b"] if index % 2 else ["cand-a"]
        for index, method in enumerate(METHOD_ORDER)
    }


def _queries() -> list[dict[str, object]]:
    return [
        {"query_id": "q-1", "truth": 10, "sql_digest": "a" * 64},
        {"query_id": "q-2", "truth": 20, "sql_digest": "b" * 64},
    ]


def test_identity_and_append_only_scope(tmp_path: Path) -> None:
    scope = output_scope(tmp_path, "invocation-001")
    scope.reserve()
    with pytest.raises(StabilityError):
        scope.reserve()
    assert make_realization_id("invocation-001", "arecel-forest10", 5).endswith("realization-05")
    with pytest.raises(StabilityError):
        make_realization_id("invocation-001", "arecel-forest10", 6)


def test_union_membership_and_oid_relation_are_distinct() -> None:
    memberships = _memberships()
    union = canonical_union(memberships)
    assert union == ("cand-a", "cand-b")
    assert filter_membership(union, ["cand-b"]) == ("cand-b",)
    parent = {
        "relation_oid": 900,
        "statistics_objects": [
            {"candidate_id": "cand-a", "relation_oid": 900, "oid": 1001},
            {"candidate_id": "cand-b", "relation_oid": 900, "oid": 1002},
        ],
    }
    assert verify_oid_order_controls(parent, union)["relative_order_status"] == "verified"
    broken = copy.deepcopy(parent)
    broken["statistics_objects"][0]["oid"] = 900
    with pytest.raises(StabilityError):
        verify_oid_order_controls(broken, union)


def test_clone_controls_reject_payload_and_ordinary_drift() -> None:
    parent = {
        "ordinary_statistics_fingerprint": "ordinary-a",
        "payloads": {"cand-a": {"payload_sha256": "a" * 64, "payload_present": True}},
    }
    clone = {
        "retained_candidate_ids": ["cand-a"],
        "payloads": {"cand-a": {"payload_sha256": "a" * 64}},
        "ordinary_statistics_fingerprint": "ordinary-a",
        "analyze_count": 0,
    }
    assert verify_clone_controls(parent, clone, ["cand-a"])["retained_payloads_equal"]
    clone["ordinary_statistics_fingerprint"] = "changed"
    with pytest.raises(StabilityError):
        verify_clone_controls(parent, clone, ["cand-a"])


def test_mock_five_realization_state_machine_and_raw_evidence(tmp_path: Path) -> None:
    protocol = _protocol()
    adapter = MockStabilityAdapter()
    result = execute_mock_invocation(
        protocol,
        output_root=tmp_path,
        invocation_id="oid-stability-offline-001",
        dataset_id="arecel-forest10",
        method_memberships=_memberships(),
        queries=_queries(),
        adapter=adapter,
        realization_numbers=(1, 2, 3, 4, 5),
    )
    assert result["completed_realizations"] == 5
    assert result["completed_method_arms"] == 45
    assert result["analyze_count"] == 5
    assert result["physical_explain_count"] == 90
    assert adapter.calls.count("analyze") == 5
    assert adapter.calls.count("cleanup") == 5
    artifact = tmp_path / "oid-stability-offline-001" / "invocation-result.json"
    assert validate_invocation_artifact(artifact)["status"] == "valid"
    aggregate = aggregate_realizations(result["realizations"])
    assert aggregate["realization_count"] == 5
    assert aggregate["greedy_reference_rank"] == [1, 1, 1, 1, 1]


def test_campaign_keeps_dataset_scoped_invocations(tmp_path: Path) -> None:
    protocol = _protocol()
    protocol["datasets"] = {"primary": ["arecel-forest10", "arecel-census13", "arecel-dmv11"]}
    datasets = {
        dataset_id: {"method_memberships": _memberships(), "queries": _queries()}
        for dataset_id in protocol["datasets"]["primary"]
    }
    result = execute_campaign(
        protocol,
        output_root=tmp_path,
        campaign_id="campaign-001",
        datasets=datasets,
        adapter=MockStabilityAdapter(),
    )
    assert result["completed_datasets"] == 3
    assert result["completed_realizations"] == 15
    assert result["completed_method_arms"] == 135
    assert result["analyze_count"] == 15
    assert result["physical_explain_count"] == 270
    assert len(set(result["dataset_invocations"].values())) == 3


def test_failure_writer_is_atomic_and_append_only(tmp_path: Path) -> None:
    scope = output_scope(tmp_path, "failure-invocation-001")
    scope.reserve()
    first = write_failure(
        scope,
        identity="failure-001",
        primary=RuntimeError("controlled failure"),
        phase="parent-created",
        producer_sha="p" * 40,
        protocol_digest="d" * 64,
        events=[{"phase": "parent-created"}],
        stdout="safe",
        stderr="password=secret",
    )
    assert first["status"] == "failed"
    assert "secret" not in (scope.path / "failures" / "failure-001.json").read_text()
    with pytest.raises(StabilityError):
        write_failure(
            scope,
            identity="failure-001",
            primary=RuntimeError("replacement"),
            phase="cleanup",
            producer_sha="p" * 40,
            protocol_digest="d" * 64,
            events=[],
        )


def test_aggregator_rejects_incomplete_realization() -> None:
    with pytest.raises(IncompleteEvidenceError):
        aggregate_realizations([], expected_realizations=5)


def test_paired_counts_are_query_aligned() -> None:
    reference = [{"query_id": "q", "truth": 10, "plan_rows": 10}]
    candidate = [{"query_id": "q", "truth": 10, "plan_rows": 20}]
    assert paired_counts(reference, candidate) == {"improved": 0, "unchanged": 0, "worsened": 1}
    with pytest.raises(StabilityError):
        paired_counts(reference, [{"query_id": "other", "truth": 10, "plan_rows": 10}])


def test_dry_run_is_offline_and_no_adapter_is_implicit() -> None:
    plan = dry_run_plan(_protocol(), invocation_id="dry-run-001", output_root=Path("/nonexistent"))
    assert plan["database_connections_opened"] == 0
    assert plan["formal_execution"] is False
    with pytest.raises(LiveExecutionNotAuthorized):
        execute_mock_invocation(
            _protocol(),
            output_root=Path("/tmp/native-stability-test"),
            invocation_id="missing-adapter",
            dataset_id="arecel-forest10",
            method_memberships=_memberships(),
            queries=_queries(),
        )
