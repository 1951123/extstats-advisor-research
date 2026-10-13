from __future__ import annotations

import json
from pathlib import Path

from extstats_advisor_research.foundations_interaction import canonical_membership


def _manifest() -> dict:
    path = (
        Path(__file__).parents[1]
        / "experiments"
        / "foundations-interaction-landscape-v1"
        / "manifest-v2.json"
    )
    return json.loads(path.read_text(encoding="utf-8"))


def test_exp002c_frozen_artifact_and_workload_binding_is_unchanged() -> None:
    manifest = _manifest()
    assert manifest["format_version"] == "foundations-interaction-landscape-manifest-v2"
    assert manifest["artifact_bindings"] == {
        "snapshot_semantic_digest": "ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5",
        "candidate_universe_semantic_digest": "292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134",
        "native_repository_semantic_digest": "fef94172830a312edf8df764adb11a46baafcf458b8cfb1679806f1cbbb83184",
        "ground_truth_semantic_digest": "bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2",
        "source_observation_sha256": "88f752eda0a55e3b361ac2a24651a4faf5039aec77c4669bb7a8f6b0802a0744",
        "workload_id": "arecel_forest10_test_v1",
        "query_count": 10000,
    }
    assert manifest["source_bindings"] == {
        "advisor_frozen_commit": "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641",
        "patched_postgresql_commit": "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6",
        "stock_postgresql_commit": "0d1c00c624fa7367d4a895f44381887757289682",
        "upstream_arecel_commit": "aa52da7768023270bad884232972e0b77ec6534a",
    }
    assert manifest["selection_policy"]["mode"] == "prespecified-structural-study-universe-v1"
    assert len(manifest["selection_policy"]["candidate_ids"]) == 8


def test_exp002c_smoke_memberships_are_declared_ordered_and_counted() -> None:
    candidate_order = tuple(_manifest()["selection_policy"]["candidate_ids"])
    m0: tuple[str, ...] = ()
    m1 = (candidate_order[0],)
    m2 = candidate_order[:2]
    assert canonical_membership(candidate_order, m0) == m0
    assert canonical_membership(candidate_order, m1) == m1
    assert canonical_membership(candidate_order, m2) == m2
    assert len({m0, m1, m2}) == 3
    assert 3 * _manifest()["artifact_bindings"]["query_count"] == 30000
