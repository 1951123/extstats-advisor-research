from __future__ import annotations

import copy

import pytest

from extstats_advisor_research.system_freeze import load_system_freeze, validate_system_freeze


def test_system_freeze_v1_is_valid() -> None:
    result = validate_system_freeze(load_system_freeze())
    assert result["status"] == "valid"
    assert result["stock_postgres_commit_sha"] == "0d1c00c624fa7367d4a895f44381887757289682"


def test_system_freeze_rejects_sut_sha_mismatch() -> None:
    value = copy.deepcopy(load_system_freeze())
    value["stock_postgresql"]["source_commit_sha"] = "0" * 40
    with pytest.raises(ValueError, match="SHA or repository mismatch"):
        validate_system_freeze(value)
