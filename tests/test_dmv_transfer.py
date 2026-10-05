from __future__ import annotations

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.datasets import dmv11
from extstats_advisor_research.dmv_transfer import (
    EXPECTED_ACCEPTED_MOVE_ORDER,
    EXPECTED_ARTIFACTS,
    EXPECTED_OBJECT_ORDER,
    SOURCE_RUN_ID,
    SOURCE_TRUTH_SHA256,
    _decision,
    _validate_external_truth_source,
)
from extstats_advisor_research.transfer_engine import validate_audited_truth_binding


def _summary(p0: float, p1: float, p2: float) -> dict:
    return {
        "p0": {"weighted_objective": p0},
        "p1": {"weighted_objective": p1},
        "p2": {"weighted_objective": p2},
    }


def test_dmv_source_run_and_artifact_binding_is_frozen() -> None:
    assert SOURCE_RUN_ID == "82f277385bf5381a43b0c268"
    assert EXPECTED_ARTIFACTS["snapshot"] == (
        "9687d42c311226678eeddb653a80fb769604656f82820a76e2d844eb2310247d"
    )
    assert EXPECTED_ARTIFACTS["ground_truth"] == (
        "6096a1e8435a7a0a471a214955ded71629555be87652e0d26d67c443971eea44"
    )
    assert EXPECTED_ARTIFACTS["recommendation"] == (
        "6bd0eb2a3c89bdc4fb0bca7319c1e40fc86cb323028eb1ff6e7a13e18372c8e2"
    )
    assert SOURCE_TRUTH_SHA256 == (
        "aaedaf54313926fb03efb1051ba58c86b17729cad372a69e9c6faf5b192eb37a"
    )


def test_dmv_external_truth_requires_exact_provenance() -> None:
    source = {
        "kind": "authoritative-external-exact",
        "authority": dmv11.AUTHORITATIVE_TRUTH_AUTHORITY,
        "dataset_identity": dmv11.AUTHORITATIVE_TRUTH_DATASET_IDENTITY,
        "source_revision": dmv11.AUTHORITATIVE_TRUTH_SOURCE_REVISION,
        "source_artifact_sha256": SOURCE_TRUTH_SHA256,
    }
    assert _validate_external_truth_source(source)["kind"] == "authoritative-external-exact"
    for key in (
        "kind",
        "authority",
        "dataset_identity",
        "source_revision",
        "source_artifact_sha256",
    ):
        bad = dict(source)
        bad[key] = "wrong"
        with pytest.raises(ValueError, match="provenance"):
            _validate_external_truth_source(bad)
    bad = {**source, "server_version": "16.14"}
    with pytest.raises(ValueError, match="server_version"):
        _validate_external_truth_source(bad)


def test_dmv_source_truth_binds_all_10000_audited_labels() -> None:
    truth = {f"arecel_dmv11_test_{index:06d}": index for index in range(10_000)}
    audit = {query_id: {"true_rows": rows} for query_id, rows in truth.items()}
    assert validate_audited_truth_binding(truth, audit, expected_count=10_000) == {
        "query_count": 10_000,
        "matched": 10_000,
        "mismatched": 0,
    }
    audit["arecel_dmv11_test_000042"]["true_rows"] = -1
    with pytest.raises(ValueError, match="labels differ"):
        validate_audited_truth_binding(truth, audit, expected_count=10_000)


def test_dmv_recommendation_membership_and_orders_are_distinct_and_frozen() -> None:
    assert len(EXPECTED_OBJECT_ORDER) == 5
    assert set(EXPECTED_OBJECT_ORDER) == set(EXPECTED_ACCEPTED_MOVE_ORDER)
    assert EXPECTED_OBJECT_ORDER != EXPECTED_ACCEPTED_MOVE_ORDER


def test_dmv_transfer_decision_and_cli_dispatch() -> None:
    assert _decision(_summary(10.0, 5.0, 8.0)) == "dmv-k8-transfer-success"
    assert _decision(_summary(10.0, 8.0, 8.0)) == "dmv-k8-transfer-neutral"
    assert _decision(_summary(10.0, 9.0, 8.0)) == "dmv-k8-transfer-regression"
    args = _parser().parse_args(
        [
            "validate",
            "full-data-transfer",
            "runs/82f277385bf5381a43b0c268",
            "--production-dsn",
            "stock",
        ]
    )
    assert args.budget_directory is None
    assert args.planner_dsn is None
