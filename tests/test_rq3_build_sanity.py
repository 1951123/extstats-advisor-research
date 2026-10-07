from __future__ import annotations

import pytest

from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq3_build_sanity import (
    build_build_sanity_artifact,
    validate_build_sanity_artifact,
)
from extstats_advisor_research.system_freeze_v2 import formal_system_freeze_v2_identity


def _controls(**overrides: bool) -> dict[str, bool]:
    controls = {
        "identical_relation_contents": True,
        "identical_schema": True,
        "identical_workload": True,
        "ordinary_stats_equal": True,
        "identical_statistics_target": True,
        "equivalent_design": True,
        "payload_correspondence_verified": True,
        "identical_planner_settings": True,
        "patched_overlay_inactive": True,
        "physical_catalog_objects_present": True,
    }
    controls.update(overrides)
    return controls


def _artifact(*, stock_rows: int = 10, patched_rows: int = 10, **control_overrides: bool) -> dict:
    return build_build_sanity_artifact(
        system={
            "research_commit_sha": "a" * 40,
            "advisor_commit_sha": "b" * 40,
            "stock_postgres_commit_sha": "c" * 40,
            "patched_postgres_commit_sha": "d" * 40,
            "stock_build_identity": {},
            "patched_build_identity": {},
        },
        fixture={"fixture_id": "rq3-synthetic-build-sanity-v1"},
        configurations=[
            {
                "configuration_id": "mcv-only",
                "controls": _controls(**control_overrides),
                "stock": {"overlay_inactive": "not-applicable-stock-binary"},
                "patched": {"overlay_inactive": True},
                "paired_queries": [
                    {
                        "query_id": "q1",
                        "sql": "SELECT 1",
                        "stock_plan_rows": stock_rows,
                        "patched_plan_rows": patched_rows,
                    }
                ],
            }
        ],
    )


def test_exact_rows_pass() -> None:
    artifact = _artifact()
    assert validate_build_sanity_artifact(artifact)["gate"] == "pass"


def test_controlled_plan_rows_mismatch_fails() -> None:
    artifact = _artifact(stock_rows=10, patched_rows=11)
    assert validate_build_sanity_artifact(artifact)["gate"] == "fail"


def test_payload_mismatch_is_inconclusive() -> None:
    artifact = _artifact(payload_correspondence_verified=False)
    assert validate_build_sanity_artifact(artifact)["gate"] == "inconclusive"


def test_active_overlay_is_rejected() -> None:
    artifact = _artifact()
    artifact["configurations"][0]["patched"]["overlay_inactive"] = False
    artifact["semantic_digest"] = semantic_digest(
        {key: value for key, value in artifact.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="active"):
        validate_build_sanity_artifact(artifact)


def test_formal_build_sanity_artifact_requires_v2_freeze_identity() -> None:
    artifact = build_build_sanity_artifact(
        system={
            "research_commit_sha": "a" * 40,
            "advisor_commit_sha": "e0aa1ad736deb77cf0c05e3befb2b1e772bc7da3",
            "stock_postgres_commit_sha": "c" * 40,
            "patched_postgres_commit_sha": "d" * 40,
            "stock_build_identity": {},
            "patched_build_identity": {},
        },
        fixture={"fixture_id": "fixture"},
        configurations=[
            {
                "configuration_id": "mcv-only",
                "controls": _controls(),
                "stock": {"overlay_inactive": "not-applicable-stock-binary"},
                "patched": {"overlay_inactive": True},
                "paired_queries": [
                    {
                        "query_id": "q1",
                        "sql": "SELECT 1",
                        "stock_plan_rows": 10,
                        "patched_plan_rows": 10,
                    }
                ],
            }
        ],
        formal_experiment=True,
        system_freeze=formal_system_freeze_v2_identity(),
    )
    assert artifact["formal_experiment"] is True
    artifact["system_freeze"]["semantic_digest"] = "a" * 64
    with pytest.raises(ValueError, match="system-freeze-v2"):
        validate_build_sanity_artifact(
            {
                **artifact,
                "semantic_digest": semantic_digest(
                    {key: value for key, value in artifact.items() if key != "semantic_digest"}
                ),
            }
        )
