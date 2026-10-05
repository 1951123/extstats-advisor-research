from pathlib import Path

from extstats_advisor_research import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA,
    TRANSFER_SOURCE_ADVISOR_SHA,
)
from extstats_advisor_research.forest_transfer import (
    SOURCE_ADVISOR_SHA as FOREST_SOURCE_ADVISOR_SHA,
)
from extstats_advisor_research.power_transfer import SOURCE_ADVISOR_SHA as POWER_SOURCE_ADVISOR_SHA


def test_frozen_system_metadata() -> None:
    assert FROZEN_ADVISOR_SHA == "0865c5a6afb8bc176bd7d3b10b13b3da83f1f641"
    assert PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA == ("bb4d58d46e734981a4542de4bcf59441d3effb98")
    assert TRANSFER_SOURCE_ADVISOR_SHA == "aa65af49fdbbf7443f8fa7295677724babfddfc5"
    assert FROZEN_PATCHED_POSTGRES_SHA == "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
    assert FOREST_SOURCE_ADVISOR_SHA == PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA
    assert POWER_SOURCE_ADVISOR_SHA == PRE_EXTERNAL_GROUND_TRUTH_ADVISOR_SHA
    dependency = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert f"extstats-advisor.git@{FROZEN_ADVISOR_SHA}" in dependency
