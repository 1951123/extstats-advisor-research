from __future__ import annotations

import copy

import pytest

from extstats_advisor_research.system_freeze_v2 import (
    load_readiness_review,
    load_system_freeze_v2,
    validate_readiness_evidence,
    validate_readiness_review,
    validate_system_freeze_v2,
)


def test_system_freeze_v2_and_readiness_review_are_valid() -> None:
    freeze = validate_system_freeze_v2(load_system_freeze_v2())
    readiness = validate_readiness_review(load_readiness_review())
    assert freeze["status"] == "valid"
    assert readiness["status"] == "valid"
    assert readiness["mandatory_gate_count"] == 8
    evidence = validate_readiness_evidence()
    assert evidence["status"] == "valid"
    assert evidence["power7"] == "a735add5b4c5eaff5a9c61964a5f629ba20d3872eb9708d33047eb9693bb712c"


def test_system_freeze_v2_pins_current_advisor_and_preserves_v1_provenance() -> None:
    value = copy.deepcopy(load_system_freeze_v2())
    value["advisor"]["commit_sha"] = "0" * 40
    value.pop("semantic_digest")
    with pytest.raises(ValueError, match="advisor identity mismatch"):
        validate_system_freeze_v2(value)


def test_readiness_review_rejects_unresolved_or_unscoped_gate() -> None:
    value = copy.deepcopy(load_readiness_review())
    value["out_of_scope_validation"]["census13_singleton"]["equivalence_status"] = "passed"
    value.pop("artifact_digest")
    with pytest.raises(ValueError, match="census13_singleton is not explicitly out of scope"):
        validate_readiness_review(value)
