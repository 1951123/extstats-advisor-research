from extstats_advisor_research import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    TRANSFER_SOURCE_ADVISOR_SHA,
)


def test_frozen_system_metadata() -> None:
    assert FROZEN_ADVISOR_SHA == "bb4d58d46e734981a4542de4bcf59441d3effb98"
    assert TRANSFER_SOURCE_ADVISOR_SHA == "aa65af49fdbbf7443f8fa7295677724babfddfc5"
    assert FROZEN_PATCHED_POSTGRES_SHA == "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6"
