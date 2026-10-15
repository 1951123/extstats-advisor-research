import json
import math
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_raw_mcv_matrix import (
    RAW_MCV_CANDIDATES,
    RAW_MCV_QUERY_IDS,
    RawMcvMatrixBinding,
    RawMcvMatrixError,
    RawMcvMatrixResult,
    RawMcvObservation,
    _matrix_memberships,
    _postgres_clamp_row_estimate,
    build_analysis_payload,
    planned_matrix_cells,
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


def test_planned_schedule_has_416_unique_cells_and_routes_query_ids() -> None:
    cells = planned_matrix_cells()
    assert len(cells) == 416
    assert len(set(cells)) == 416
    assert cells[0] == ((), RAW_MCV_QUERY_IDS[0])
    assert cells[208] == ((), RAW_MCV_QUERY_IDS[16])
    assert cells[-1] == ((RAW_MCV_CANDIDATES[-1],), RAW_MCV_QUERY_IDS[-1])
    for membership, query_id in cells[::37]:
        assert membership in _matrix_memberships()
        assert query_id in RAW_MCV_QUERY_IDS
        assert _RawEstimateUtility(query_id).evaluate({query_id: 7.25}).objective == 7.25


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


def test_synthetic_416_matrix_passes_reconstruction_enumeration_and_publication(
    tmp_path: Path,
) -> None:
    root = Path.cwd()
    optimization_path = (
        root / ".runtime/foundations-pessimistic-12mcv/pessimistic-12mcv-unit-v1.json"
    )
    permutation_path = (
        root / ".runtime/foundations-pessimistic-12mcv/order-permutations-4mcv-v1.json"
    )
    earlier_path = root / ".runtime/foundations-raw-selectivity/000146-raw-v1.json"
    optimization = json.loads(optimization_path.read_text(encoding="utf-8"))
    precedence = tuple(optimization["experiment_inputs"]["global_precedence"])
    binding = RawMcvMatrixBinding(
        admin_dsn="unused",
        advisor_root=root,
        postgres_root=root,
        research_root=root,
        snapshot_path=root,
        candidate_universe_path=root,
        ground_truth_path=root,
        historical_optimization_path=optimization_path,
        historical_permutation_path=permutation_path,
        earlier_raw_path=earlier_path,
        global_precedence=precedence,
        expected_snapshot_digest="snapshot",
        expected_candidate_universe_digest="universe",
        expected_ground_truth_digest="truth",
        expected_advisor_sha="advisor",
        expected_research_sha="research",
        expected_postgres_sha="postgres",
        historical_optimization_sha256="unused",
        historical_permutation_sha256="unused",
        earlier_raw_sha256="unused",
    )
    observations = []
    for query_index, query_id in enumerate(RAW_MCV_QUERY_IDS):
        base = 10.0 + query_index
        for membership in _matrix_memberships():
            observations.append(
                RawMcvObservation(
                    membership=membership,
                    activation_membership=membership,
                    query_id=query_id,
                    selectivity_pre_clamp=base / 1_000_000.0,
                    rows_pre_clamp=base,
                    relation_tuples=1_000_000.0,
                    plan_rows_clamped=round(base),
                    precise_plan_rows=base,
                    trace=(),
                )
            )
    result = RawMcvMatrixResult(binding, "synthetic", 1, tuple(observations), {}, True)
    payload = build_analysis_payload(
        result,
        optimization_artifact=optimization,
        permutation_artifact=json.loads(permutation_path.read_text(encoding="utf-8")),
        truth_by_query={
            query_id: float(optimization["model"]["truths"][query_id])
            for query_id in RAW_MCV_QUERY_IDS
        },
    )
    assert len(payload["observations"]) == 416
    assert len(payload["raw_coefficients"]["b_rows"]) == 12
    assert len(payload["enumeration"]["raw"]["budgets"]) == 13
    assert len(payload["enumeration"]["plan"]["budgets"]) == 13
    destination = tmp_path / "synthetic-raw-mcv.json"
    write_raw_mcv_matrix_json(payload, destination)
    serialized = json.loads(destination.read_text(encoding="utf-8"))
    assert serialized["complete"] is True
    assert len(serialized["observations"]) == 416
