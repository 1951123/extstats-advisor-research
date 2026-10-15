import math
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_raw_mcv_matrix import (
    RAW_MCV_CANDIDATES,
    RAW_MCV_QUERY_IDS,
    RawMcvMatrixError,
    _matrix_memberships,
    _postgres_clamp_row_estimate,
    write_raw_mcv_matrix_json,
)
from extstats_advisor_research.foundations_raw_selectivity import _RawEstimateUtility


def test_matrix_has_exact_empty_and_twelve_singletons() -> None:
    memberships = _matrix_memberships()
    assert len(memberships) == 13
    assert memberships[0] == ()
    assert memberships[1:] == tuple((candidate_id,) for candidate_id in RAW_MCV_CANDIDATES)
    assert len({frozenset(item) for item in memberships}) == 13
    assert len(RAW_MCV_QUERY_IDS) == 32


def test_fixed_queries_are_a_selected_subset_of_the_broader_snapshot() -> None:
    broader = ("unselected_query",) + RAW_MCV_QUERY_IDS + ("another_query",)
    selected = tuple(query_id for query_id in broader if query_id in set(RAW_MCV_QUERY_IDS))
    assert selected == RAW_MCV_QUERY_IDS


def test_raw_utility_can_target_each_matrix_query() -> None:
    utility = _RawEstimateUtility(RAW_MCV_QUERY_IDS[0])
    assert utility.evaluate({RAW_MCV_QUERY_IDS[0]: 3.25}).objective == 3.25


@pytest.mark.parametrize(
    ("value", "expected"),
    ((0.1, 1.0), (1.0, 1.0), (1.4, 1.0), (2.5, 2.0), (3.5, 4.0), (4.596809797437805, 5.0)),
)
def test_postgres_clamp_matches_positive_finite_rint_regime(value: float, expected: float) -> None:
    assert _postgres_clamp_row_estimate(value) == expected


def test_invalid_model_rows_fail_explicitly() -> None:
    with pytest.raises(RawMcvMatrixError):
        _postgres_clamp_row_estimate(0.0)
    with pytest.raises(RawMcvMatrixError):
        _postgres_clamp_row_estimate(math.inf)


def test_atomic_writer_rejects_existing_destination(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    destination.write_text("historical", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_raw_mcv_matrix_json({"complete": True}, destination)


def test_atomic_writer_emits_standard_json(tmp_path: Path) -> None:
    destination = tmp_path / "result.json"
    write_raw_mcv_matrix_json({"complete": True, "value": 4.596809797437805}, destination)
    assert destination.exists()
    assert destination.read_text(encoding="utf-8").endswith("\n")
