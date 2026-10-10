from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.native_analyze_stability_posthoc import (
    _classify_payload,
    validate_posthoc_artifact,
)
from extstats_advisor_research.provenance import semantic_digest

ROOT = Path(__file__).resolve().parents[1]
POSTHOC = (
    ROOT
    / "experiments/native-analyze-stability-v1/native-analyze-stability-posthoc-analysis-v2.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_posthoc_artifact_validates_without_live_services() -> None:
    value = _load(POSTHOC)
    assert validate_posthoc_artifact(value, ROOT)["status"] == "valid"
    assert value["v1_status"] == "failed/ineligible with evidence preserved"


def test_posthoc_validator_rejects_promotion_of_v1() -> None:
    value = _load(POSTHOC)
    value["v1_status"] = "complete-validated"
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="v1 status was promoted"):
        validate_posthoc_artifact(value, ROOT)


def test_dependency_null_requires_parent_clone_controls() -> None:
    item = {
        "kind": "postgresql.dependencies",
        "payload_present": False,
        "payload_bytes": 0,
        "payload_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    }
    controls = {
        "payloads_exactly_preserved_after_drop": True,
        "ordinary_statistics_equal_to_parent": True,
        "post_drop_analyze_count": 0,
    }
    assert _classify_payload(item, controls) == "native-null-supported"
    broken = copy.deepcopy(controls)
    broken["payloads_exactly_preserved_after_drop"] = False
    assert _classify_payload(item, broken) == "unverified"
