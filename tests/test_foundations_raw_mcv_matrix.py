import json
import math
from pathlib import Path

import pytest

from extstats_advisor_research.foundations_live_integration import _default_services
from extstats_advisor_research.foundations_raw_mcv_matrix import (
    RAW_MCV_CANDIDATES,
    RAW_MCV_QUERY_IDS,
    RawMcvCheckpointWriter,
    RawMcvMatrixBinding,
    RawMcvMatrixError,
    RawMcvMatrixResult,
    RawMcvObservation,
    _matrix_memberships,
    _postgres_clamp_row_estimate,
    build_analysis_payload,
    ground_truth_by_query,
    load_raw_mcv_checkpoint,
    planned_matrix_cells,
    replay_raw_mcv_checkpoint,
    write_raw_mcv_matrix_json,
)
from extstats_advisor_research.foundations_raw_selectivity import _RawEstimateUtility


def _canonical_binding() -> RawMcvMatrixBinding:
    root = Path.cwd()
    advisor_root = Path("/home/wqts/projects/extstats-advisor")
    snapshot_path = root / ".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/advisor-snapshot"
    candidate_universe_path = (
        root / ".runtime/rq5-whatif-evaluation-cost-v3/forest10-30000/candidate-universe.json"
    )
    optimization_path = (
        root / ".runtime/foundations-pessimistic-12mcv/pessimistic-12mcv-unit-v1.json"
    )
    services = _default_services(advisor_root, "ddc7cc2b3dc61156a1fb3dbecb4c8ecd2e41baa5")
    snapshot = services.load_snapshot(snapshot_path)
    universe = services.load_candidate_universe(candidate_universe_path, snapshot)
    precedence = tuple(
        item.candidate_id
        for item in sorted(universe.candidates, key=lambda item: item.static_precedence_rank)
    )
    return RawMcvMatrixBinding(
        admin_dsn="unused",
        advisor_root=advisor_root,
        postgres_root=Path("/home/wqts/projects/postgresql-src-pgextadv"),
        research_root=root,
        snapshot_path=snapshot_path,
        candidate_universe_path=candidate_universe_path,
        ground_truth_path=root
        / ".runtime/foundations-interaction-landscape-v1/current-snapshot-ground-truth-v1.json",
        historical_optimization_path=optimization_path,
        historical_permutation_path=root
        / ".runtime/foundations-pessimistic-12mcv/order-permutations-4mcv-v1.json",
        earlier_raw_path=root / ".runtime/foundations-raw-selectivity/000146-raw-v1.json",
        global_precedence=precedence,
        expected_snapshot_digest="ed5d82ffd6999989d86097d42252f635e0fa38928774b5ed38910aa47bf2a4f5",
        expected_candidate_universe_digest="292247e8a8041eeb91eb88de906b52eaf81f9fe981d33395a98d40698cca3134",
        expected_ground_truth_digest="bcea3b0f4076f0b27aa751d04a54688b3823983feadf6556aff7e35114d700c2",
        expected_advisor_sha="ddc7cc2b3dc61156a1fb3dbecb4c8ecd2e41baa5",
        expected_research_sha="a4a308cbdacaea7540e50e48e6d8ff2288d03eed",
        expected_postgres_sha="6357958f94b196b085038308d952afe901174fc3",
        historical_optimization_sha256="2d9dcda5a081a0262c2fc7a720ea9c7ba19ae6371714b09d87e9f8a346329acc",
        historical_permutation_sha256="18c1bc93cc049885bccbcd2cb149c945dc15ae4c528ac1ec86275cccdb1fa7aa",
        earlier_raw_sha256="a2ce825f4e6647b954c5d1c661e0feb232950f42db306fb0e2680b038f60ebdd",
    )


def _synthetic_observation(
    index: int, membership: tuple[str, ...], query_id: str
) -> RawMcvObservation:
    value = 10.0 + RAW_MCV_QUERY_IDS.index(query_id) / 100.0
    return RawMcvObservation(
        membership=membership,
        activation_membership=membership,
        query_id=query_id,
        selectivity_pre_clamp=value / 1_000_000.0,
        rows_pre_clamp=value,
        relation_tuples=1_000_000.0,
        plan_rows_clamped=round(value),
        precise_plan_rows=value,
        trace=(),
    )


def _write_synthetic_checkpoint(path: Path, *, count: int | None = None) -> Path:
    binding = _canonical_binding()
    writer = RawMcvCheckpointWriter(
        path,
        binding=binding,
        database_name="synthetic_checkpoint_db",
        relation_oid=12345,
        payload_sha256={candidate_id: "a" * 64 for candidate_id in RAW_MCV_CANDIDATES},
    )
    cells = planned_matrix_cells()
    limit = len(cells) if count is None else count
    for index, (membership, query_id) in enumerate(cells[:limit]):
        writer.append(_synthetic_observation(index, membership, query_id))
    if count is None:
        writer.mark_complete()
        writer.mark_cleanup_verified()
    return path


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


def test_canonical_truth_api_and_complete_checkpoint_replay(tmp_path: Path) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "complete-checkpoint.json")
    destination = tmp_path / "replayed-result.json"
    payload = replay_raw_mcv_checkpoint(
        checkpoint,
        binding=_canonical_binding(),
        destination=destination,
    )
    services = _default_services(
        Path("/home/wqts/projects/extstats-advisor"),
        "ddc7cc2b3dc61156a1fb3dbecb4c8ecd2e41baa5",
    )
    snapshot = services.load_snapshot(_canonical_binding().snapshot_path)
    truth = services.load_ground_truth(_canonical_binding().ground_truth_path, snapshot)
    assert len(ground_truth_by_query(truth)) == 32
    assert len(payload["observations"]) == 416
    assert len(payload["enumeration"]["raw"]["budgets"]) == 13
    assert len(payload["enumeration"]["plan"]["budgets"]) == 13
    reopened = json.loads(destination.read_text(encoding="utf-8"))
    assert reopened["complete"] is True
    assert reopened["runtime"]["planner_query_evaluations"] == 416


@pytest.mark.parametrize("count", (1, 415))
def test_partial_checkpoint_survives_failure_before_completion(tmp_path: Path, count: int) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / f"partial-{count}.json", count=count)
    result = load_raw_mcv_checkpoint(checkpoint, binding=_canonical_binding())
    assert result.complete is False
    assert len(result.observations) == count
    with pytest.raises(RawMcvMatrixError, match="incomplete"):
        load_raw_mcv_checkpoint(checkpoint, binding=_canonical_binding(), require_complete=True)


def test_complete_checkpoint_survives_failure_before_optimization(tmp_path: Path) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "complete-before-analysis.json")
    result = load_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), require_complete=True
    )
    assert result.complete is True
    assert len(result.observations) == 416


def test_analysis_failure_leaves_complete_checkpoint_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "analysis-failure.json")

    def fail_analysis(*args: object, **kwargs: object) -> dict[str, object]:
        raise RuntimeError("synthetic optimization failure")

    monkeypatch.setattr(
        "extstats_advisor_research.foundations_raw_mcv_matrix.build_analysis_payload",
        fail_analysis,
    )
    with pytest.raises(RuntimeError, match="synthetic optimization failure"):
        replay_raw_mcv_checkpoint(checkpoint, binding=_canonical_binding())
    result = load_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), require_complete=True
    )
    assert result.complete is True


def test_duplicate_and_conflicting_checkpoint_cells_are_rejected(tmp_path: Path) -> None:
    binding = _canonical_binding()
    checkpoint = tmp_path / "duplicates.json"
    writer = RawMcvCheckpointWriter(
        checkpoint,
        binding=binding,
        database_name="synthetic_checkpoint_db",
        relation_oid=12345,
        payload_sha256={candidate_id: "b" * 64 for candidate_id in RAW_MCV_CANDIDATES},
    )
    observation = _synthetic_observation(0, (), RAW_MCV_QUERY_IDS[0])
    writer.append(observation)
    with pytest.raises(RawMcvMatrixError, match="duplicate"):
        writer.append(observation)


def test_checkpoint_identity_tampering_is_rejected(tmp_path: Path) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "tampered.json")
    value = json.loads(checkpoint.read_text(encoding="utf-8"))
    value["observations"][0]["identity"]["ground_truth_semantic_digest"] = "f" * 64
    checkpoint.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RawMcvMatrixError, match="identity"):
        load_raw_mcv_checkpoint(checkpoint, binding=_canonical_binding())


def test_checkpoint_preserves_fractional_values_and_replay_is_repeatable(tmp_path: Path) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "precision.json")
    first = load_raw_mcv_checkpoint(checkpoint, binding=_canonical_binding(), require_complete=True)
    second = load_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), require_complete=True
    )
    assert first.observations[0].rows_pre_clamp == second.observations[0].rows_pre_clamp
    assert first.observations[0].rows_pre_clamp == 10.0
    first_destination = tmp_path / "first.json"
    second_destination = tmp_path / "second.json"
    replay_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), destination=first_destination
    )
    replay_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), destination=second_destination
    )
    first_value = json.loads(first_destination.read_text(encoding="utf-8"))
    second_value = json.loads(second_destination.read_text(encoding="utf-8"))
    for value in (first_value, second_value):
        for convention in ("raw", "plan"):
            value["enumeration"][convention]["enumeration_statistics"].pop("elapsed_ns")
    assert first_value == second_value


def test_publication_failure_does_not_remove_checkpoint(tmp_path: Path) -> None:
    checkpoint = _write_synthetic_checkpoint(tmp_path / "publication-failure.json")
    destination = tmp_path / "existing-result.json"
    destination.write_text("historical", encoding="utf-8")
    with pytest.raises(FileExistsError):
        replay_raw_mcv_checkpoint(
            checkpoint,
            binding=_canonical_binding(),
            destination=destination,
        )
    loaded = load_raw_mcv_checkpoint(
        checkpoint, binding=_canonical_binding(), require_complete=True
    )
    assert loaded.complete is True
