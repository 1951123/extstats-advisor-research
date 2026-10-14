from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from extstats_advisor_research.foundations_landscape import (
    EXPECTED_PRIMARY_PLANNER_QUERIES,
    STRUCTURAL_CANDIDATES,
    STRUCTURAL_QUERY_IDS,
    FoundationsLandscapeInput,
    enumerate_structural_configurations,
    run_foundations_structural_landscape,
)
from extstats_advisor_research.foundations_live_integration import FoundationsOracleServices

ALL_CANDIDATES = STRUCTURAL_CANDIDATES + tuple(f"other-{index}" for index in range(49))


@dataclass(frozen=True)
class Candidate:
    candidate_id: str


@dataclass(frozen=True)
class Profile:
    query_id: str
    analysis_status: str = "supported"
    weight: float = 1.0


class FakeSession:
    def __init__(self, events: list[tuple[str, object]], failing_query: str | None = None) -> None:
        self.events = events
        self.failing_query = failing_query
        self.active_configuration = SimpleNamespace(ordered_candidate_ids=())

    def open(self) -> None:
        self.events.append(("session-open", None))

    def activate(self, configuration) -> None:
        self.active_configuration = configuration
        self.events.append(("activate", configuration.ordered_candidate_ids))

    def estimate_query(self, query_id: str):
        self.events.append(("estimate", query_id))
        if query_id == self.failing_query:
            raise RuntimeError("synthetic structural planner failure")
        return SimpleNamespace(estimated_rows=1)

    def close(self) -> None:
        self.events.append(("session-close", None))


class Utility:
    def evaluate(self, estimates):
        return SimpleNamespace(objective=sum(estimates.values()) / len(estimates))


class FakeLifecycle:
    def __init__(self, spec, events: list[tuple[str, object]]) -> None:
        self.events = events
        self.database_name = "exp_foundations_landscape_offline"
        self.database_dsn = "host=127.0.0.1 port=55433 dbname=exp_foundations_landscape_offline"

    def __enter__(self):
        self.events.append(("database-enter", None))
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.events.append(("database-exit", None))
        return False


def _inputs(**overrides) -> FoundationsLandscapeInput:
    values = {
        "admin_dsn": "host=127.0.0.1 port=55433 dbname=postgres",
        "advisor_root": "/unused/advisor",
        "snapshot_path": "snapshot.json",
        "candidate_universe_path": "candidates.json",
        "ground_truth_path": "truth.json",
        "dataset_id": "forest10",
        "relation_id": "forest10",
        "relation_schema": "public",
        "relation_name": "forest10",
        "workload_id": "foundations-structural-workload",
        "global_precedence": ALL_CANDIDATES,
        "expected_snapshot_digest": "s" * 64,
        "expected_candidate_universe_digest": "u" * 64,
        "expected_ground_truth_digest": "t" * 64,
        "expected_advisor_sha": "a" * 40,
    }
    values.update(overrides)
    return FoundationsLandscapeInput(**values)


def _fixture(events: list[tuple[str, object]], *, failing_query: str | None = None):
    # Deliberately reverse the first two static candidates.  The oracle must still
    # activate the explicit global precedence order.
    candidate_ids = (STRUCTURAL_CANDIDATES[1], STRUCTURAL_CANDIDATES[0], *STRUCTURAL_CANDIDATES[2:])
    candidate_ids += ALL_CANDIDATES[len(STRUCTURAL_CANDIDATES) :]
    universe = SimpleNamespace(
        candidates=tuple(Candidate(candidate_id) for candidate_id in candidate_ids),
        query_profiles=tuple(Profile(query_id) for query_id in STRUCTURAL_QUERY_IDS),
        semantic_digest="u" * 64,
    )
    snapshot = SimpleNamespace(
        semantic_digest="s" * 64,
        workload=SimpleNamespace(
            workload_id="foundations-structural-workload",
            provenance=None,
            queries=tuple(SimpleNamespace(query_id=query_id) for query_id in STRUCTURAL_QUERY_IDS),
        ),
        schemas=(
            SimpleNamespace(
                relation_id="forest10",
                relation_name=SimpleNamespace(schema="public", name="forest10"),
            ),
        ),
        samples={"forest10": SimpleNamespace(num_rows=30_000)},
    )
    truth = SimpleNamespace(semantic_digest="t" * 64)
    repository = SimpleNamespace(
        candidate_models=tuple(
            SimpleNamespace(candidate_id=candidate_id, state="present")
            for candidate_id in candidate_ids
        )
    )
    session = FakeSession(events, failing_query=failing_query)
    materializations: list[object] = []

    def materialize(*args):
        materializations.append(args)
        return repository

    def prepare(*args):
        events.append(("prepare-sandbox", args[-2]))

    def verify(*args):
        events.append(("verify-sandbox", args[-1]))
        return {"sample_row_count": 30_000}

    def destroy(dsn):
        events.append(("destroy-sandbox", dsn))

    services = FoundationsOracleServices(
        load_snapshot=lambda path: snapshot,
        load_candidate_universe=lambda path, loaded_snapshot: universe,
        load_ground_truth=lambda path, loaded_snapshot: truth,
        materialize_repository=materialize,
        prepare_sandbox=prepare,
        verify_sandbox=verify,
        destroy_sandbox=destroy,
        planner_session=lambda *args: events.append(("planner-database", args[-1])) or session,
        make_utility=lambda loaded_snapshot, loaded_truth, query_ids: Utility(),
    )
    return services, session, materializations


def test_enumeration_is_exact_mask_order_with_fixed_bit_mapping() -> None:
    configurations = enumerate_structural_configurations()

    assert len(configurations) == 256
    assert [configuration.mask for configuration in configurations] == list(range(256))
    assert configurations[0].membership == ()
    assert configurations[255].membership == STRUCTURAL_CANDIDATES
    assert len({configuration.membership for configuration in configurations}) == 256
    assert all(
        set(configuration.membership) <= set(STRUCTURAL_CANDIDATES)
        for configuration in configurations
    )
    assert configurations[5].membership == (
        STRUCTURAL_CANDIDATES[0],
        STRUCTURAL_CANDIDATES[2],
    )


def test_landscape_uses_one_repository_and_pi_restricted_activation() -> None:
    events: list[tuple[str, object]] = []
    services, session, materializations = _fixture(events)

    result = run_foundations_structural_landscape(
        _inputs(),
        services=services,
        lifecycle_factory=lambda spec: FakeLifecycle(spec, events),
    )

    assert result.complete
    assert len(result.evaluations) == 256
    assert result.primary_planner_query_count == EXPECTED_PRIMARY_PLANNER_QUERIES == 8192
    assert len(materializations) == 1
    assert len([event for event in events if event[0] == "estimate"]) == 8192
    assert result.evaluations[3].membership == (
        STRUCTURAL_CANDIDATES[0],
        STRUCTURAL_CANDIDATES[1],
    )
    assert result.evaluations[3].activation_membership == (
        STRUCTURAL_CANDIDATES[0],
        STRUCTURAL_CANDIDATES[1],
    )
    assert result.evaluations[3].oracle_evaluation.estimates == {
        query_id: 1.0 for query_id in STRUCTURAL_QUERY_IDS
    }
    assert session.active_configuration.ordered_candidate_ids == (*STRUCTURAL_CANDIDATES,)
    assert events.index(("session-close", None)) < events.index(
        ("destroy-sandbox", "host=127.0.0.1 port=55433 dbname=exp_foundations_landscape_offline")
    )
    assert events[-1] == ("database-exit", None)


def test_landscape_preserves_failed_observations_without_fabrication() -> None:
    events: list[tuple[str, object]] = []
    services, _session, _materializations = _fixture(
        events,
        failing_query=STRUCTURAL_QUERY_IDS[0],
    )

    result = run_foundations_structural_landscape(
        _inputs(),
        services=services,
        lifecycle_factory=lambda spec: FakeLifecycle(spec, events),
    )

    assert not result.complete
    assert len(result.evaluations) == 256
    assert all(
        item.oracle_evaluation.diagnostics.status == "failed"
        and item.oracle_evaluation.objective is None
        and item.oracle_evaluation.diagnostics.error
        for item in result.evaluations
    )
    assert all(item.oracle_evaluation.estimates == {} for item in result.evaluations)
    assert events[-1] == ("database-exit", None)
