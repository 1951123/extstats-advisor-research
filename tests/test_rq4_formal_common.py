from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq4_formal_common import (
    CANONICAL_METHOD_ORDER,
    DATASETS,
    DESIGN_FORMAT,
    DETERMINISM_FORMAT,
    FIXED_K,
    PREflight_FORMAT,
    build_preflight,
    validate_v2_design_artifact,
    validate_v2_determinism,
)

ROOT = Path(__file__).resolve().parents[1]
ADVISOR_ROOT = Path("/home/wqts/projects/extstats-advisor")


def test_v2_preflight_reuses_all_three_rq2_sources() -> None:
    expected = {
        "arecel-census13": (136, 402),
        "arecel-power7": (42, 120),
        "arecel-dmv11": (105, 309),
    }
    for dataset_id, (candidate_count, worst_case) in expected.items():
        result = build_preflight(dataset_id, ROOT, ADVISOR_ROOT)
        assert result["format_version"] == PREflight_FORMAT
        assert result["status"] == "ready-to-run"
        assert result["eligible_candidate_count"] == candidate_count
        assert result["fixed_k"] == FIXED_K
        assert result["greedy_worst_case_live_configuration_evaluations"] == worst_case
        assert (
            result["singleton_profile_reuse"]["new_selection_configuration_objective_evaluations"]
            == 0
        )
        assert result["final_evaluations"]["planner_query_calls"] == 90_000


def test_v2_method_order_is_nine_methods() -> None:
    assert CANONICAL_METHOD_ORDER == (
        "random-k-seed-1",
        "random-k-seed-2",
        "random-k-seed-3",
        "random-k-seed-4",
        "random-k-seed-5",
        "workload-frequency-top-k",
        "native-payload-size-top-k",
        "singleton-utility-top-k",
        "greedy-ADD",
    )
    assert DATASETS == ("arecel-census13", "arecel-power7", "arecel-dmv11")


def test_v2_design_validator_rejects_wrong_method_order(tmp_path: Path) -> None:
    methods = {}
    for method in CANONICAL_METHOD_ORDER:
        policy_key = method if not method.startswith("random-k-seed-") else "random-k"
        methods[method] = {
            "information_access_policy": {
                "ground_truth_during_selection": False,
                "allowed_inputs": [],
                "forbidden_inputs": [],
            },
            "status": "complete",
            "selected_membership": ["a", "b", "c", "d"],
        }
        if policy_key == "singleton-utility-top-k":
            methods[method]["information_access_policy"]["ground_truth_during_selection"] = True
    artifact = {
        "format_version": DESIGN_FORMAT,
        "fixed_k": 4,
        "method_order": list(CANONICAL_METHOD_ORDER),
        "methods": methods,
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    path = tmp_path / "rq4-design-evaluation-v2.json"
    path.write_text(json.dumps(artifact), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_v2_design_artifact(path)


def test_v2_determinism_validator_requires_equal_replays(tmp_path: Path) -> None:
    artifact = {
        "format_version": DETERMINISM_FORMAT,
        "replay_count": 2,
        "checks": {"semantic_projection_stable": True},
        "replay_semantic_digests": ["a" * 64, "b" * 64],
        "deterministic_projection": {},
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    path = tmp_path / "rq4-design-determinism-v2.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(artifact, handle)
    with pytest.raises(ValueError):
        validate_v2_determinism(path)
