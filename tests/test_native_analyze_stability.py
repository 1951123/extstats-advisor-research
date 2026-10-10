from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.native_analyze_stability import (
    validate_decision,
    validate_protocol,
    validate_readiness,
)
from extstats_advisor_research.provenance import semantic_digest

ROOT = Path(__file__).resolve().parents[1]
DECISION = ROOT / "experiments/rq4-fixed-evaluation-budget-scope-decision-v1.json"
PROTOCOL = ROOT / "paper/native-analyze-stability-protocol-v1.json"
READINESS = ROOT / "experiments/native-analyze-stability-readiness-review-v1.json"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _reseal(value: dict) -> dict:
    body = {key: item for key, item in value.items() if key != "semantic_digest"}
    return body | {"semantic_digest": semantic_digest(body)}


def test_native_stability_records_validate_without_live_services() -> None:
    decision = _load(DECISION)
    protocol = _load(PROTOCOL)
    readiness = _load(READINESS)
    assert validate_decision(decision, ROOT)["status"] == "valid"
    assert validate_protocol(protocol, ROOT)["status"] == "valid"
    assert validate_readiness(readiness, protocol, ROOT)["status"] == "valid"
    assert protocol["execution_boundary"]["formal_invocation_count"] == 0
    assert readiness["formal_execution"]["authorized"] is False


def test_native_stability_binds_historical_and_current_strata() -> None:
    protocol = _load(PROTOCOL)
    assert protocol["datasets"]["primary"] == [
        "arecel-forest10",
        "arecel-census13",
        "arecel-dmv11",
    ]
    assert protocol["datasets"]["sensitivity_extension"] == ["arecel-power7"]
    assert protocol["dataset_bindings"]["arecel-forest10"]["comparability_stratum"] == (
        "historical-v1"
    )
    assert protocol["dataset_bindings"]["arecel-census13"]["comparability_stratum"] == (
        "current-v2"
    )
    assert (
        protocol["datasets"]["cross_dataset_pooling"]
        == "prohibited; report dataset-scoped strata because Forest10 retains system-freeze-v1"
    )


def test_protocol_rejects_resealed_membership_mutation() -> None:
    protocol = _load(PROTOCOL)
    broken = copy.deepcopy(protocol)
    broken["dataset_bindings"]["arecel-census13"]["method_memberships"]["greedy-ADD"][0] = (
        "cand-mutated"
    )
    broken = _reseal(broken)
    with pytest.raises(ValueError, match="dataset binding drifted"):
        validate_protocol(broken, ROOT)


def test_readiness_rejects_formal_authorization_or_invocation() -> None:
    protocol = _load(PROTOCOL)
    readiness = _load(READINESS)
    broken = copy.deepcopy(readiness)
    broken["formal_execution"]["authorized"] = True
    broken = _reseal(broken)
    with pytest.raises(ValueError, match="authorizes formal execution"):
        validate_readiness(broken, protocol, ROOT)


def test_decision_rejects_claimed_results() -> None:
    decision = _load(DECISION)
    broken = copy.deepcopy(decision)
    broken["results"]["fixed_evaluation_budget_results_generated"] = True
    broken = _reseal(broken)
    with pytest.raises(ValueError, match="claims fixed-evaluation-budget results"):
        validate_decision(broken, ROOT)
