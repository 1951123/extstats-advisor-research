from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_live_integration import (
    DEFAULT_SAMPLE_ROWS,
    FoundationsOracleServices,
    FoundationsSmokeInput,
    FoundationsSmokeInputError,
    run_foundations_oracle_smoke,
)

A = "cand_0deae0d5de82a5a1fdc34111"
B = "cand_76679921292e8c769dbbd7bc"


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
            raise RuntimeError("synthetic planner failure")
        return SimpleNamespace(estimated_rows=10)

    def close(self) -> None:
        self.events.append(("session-close", None))


class Utility:
    def evaluate(self, estimates):
        return SimpleNamespace(objective=sum(estimates.values()) / len(estimates))


class FakeLifecycle:
    def __init__(self, spec, events: list[tuple[str, object]]) -> None:
        self.spec = spec
        self.events = events
        self.database_name = "exp_foundations_offline"
        self.database_dsn = "host=127.0.0.1 port=55433 dbname=exp_foundations_offline"

    def __enter__(self):
        self.events.append(("database-enter", self.spec.sample_rows))
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        self.events.append(("database-exit", None))
        return False


def _fixture(events: list[tuple[str, object]], *, failing_query: str | None = None):
    universe = SimpleNamespace(
        candidates=(Candidate(B), Candidate(A)),
        query_profiles=(Profile("q1"), Profile("q2")),
        semantic_digest="u" * 64,
    )
    snapshot = SimpleNamespace(
        semantic_digest="s" * 64,
        workload=SimpleNamespace(
            workload_id="foundations-smoke-workload",
            provenance=None,
            queries=(SimpleNamespace(query_id="q1"), SimpleNamespace(query_id="q2")),
        ),
        schemas=(
            SimpleNamespace(
                relation_id="forest10",
                relation_name=SimpleNamespace(schema="public", name="forest10"),
            ),
        ),
        samples={"forest10": SimpleNamespace(num_rows=DEFAULT_SAMPLE_ROWS)},
    )
    truth = SimpleNamespace(semantic_digest="t" * 64)
    repository = SimpleNamespace(
        candidate_models=(
            SimpleNamespace(candidate_id=B, state="present"),
            SimpleNamespace(candidate_id=A, state="present"),
        )
    )
    session = FakeSession(events, failing_query=failing_query)

    def prepare(*args):
        events.append(("prepare-sandbox", args[-2]))

    def verify(*args):
        events.append(("verify-sandbox", args[-1]))
        return {"sample_row_count": DEFAULT_SAMPLE_ROWS}

    def destroy(dsn):
        events.append(("destroy-sandbox", dsn))

    services = FoundationsOracleServices(
        load_snapshot=lambda path: snapshot,
        load_candidate_universe=lambda path, loaded_snapshot: universe,
        load_ground_truth=lambda path, loaded_snapshot: truth,
        materialize_repository=lambda dsn, loaded_snapshot, loaded_universe, target, batch: (
            events.append(("materialize", (target, batch))) or repository
        ),
        prepare_sandbox=prepare,
        verify_sandbox=verify,
        destroy_sandbox=destroy,
        planner_session=lambda *args: events.append(("planner-database", args[-1])) or session,
        make_utility=lambda loaded_snapshot, loaded_truth, query_ids: Utility(),
    )
    return services


def _inputs(query_ids: tuple[str, ...] = ("q1",), **overrides) -> FoundationsSmokeInput:
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
        "workload_id": "foundations-smoke-workload",
        "query_ids": query_ids,
        "global_precedence": (A, B),
        "expected_snapshot_digest": "s" * 64,
        "expected_candidate_universe_digest": "u" * 64,
        "expected_ground_truth_digest": "t" * 64,
    }
    values.update(overrides)
    return FoundationsSmokeInput(**values)


def test_smoke_composes_lifecycle_and_explicit_precedence() -> None:
    events: list[tuple[str, object]] = []
    services = _fixture(events)

    result = run_foundations_oracle_smoke(
        _inputs(),
        services=services,
        lifecycle_factory=lambda spec: FakeLifecycle(spec, events),
    )

    assert result.complete
    assert [item.diagnostics.membership for item in result.evaluations] == [(), (A,), (A, B)]
    assert [event for event in events if event[0] == "activate"] == [
        ("activate", ()),
        ("activate", (A,)),
        ("activate", (A, B)),
    ]
    assert ("planner-database", "exp_foundations_offline") in events
    assert events.index(("session-close", None)) < events.index(("destroy-sandbox", events[-2][1]))
    assert events[-1] == ("database-exit", None)


def test_smoke_preserves_failed_query_and_closes_before_database_cleanup() -> None:
    events: list[tuple[str, object]] = []
    result = run_foundations_oracle_smoke(
        _inputs(),
        services=_fixture(events, failing_query="q1"),
        lifecycle_factory=lambda spec: FakeLifecycle(spec, events),
    )

    assert not result.complete
    assert all(item.diagnostics.status == "failed" for item in result.evaluations)
    assert events.index(("session-close", None)) < events.index(("destroy-sandbox", events[-2][1]))
    assert events[-1] == ("database-exit", None)


@pytest.mark.parametrize(
    "change",
    [
        {"sample_rows": DEFAULT_SAMPLE_ROWS - 1},
        {"global_precedence": (B, A)},
        {"query_ids": ("q1", "q1")},
    ],
)
def test_smoke_inputs_reject_inconsistent_bindings(change) -> None:
    with pytest.raises(FoundationsSmokeInputError):
        _inputs(**change)


def test_exception_during_materialization_still_exits_database() -> None:
    events: list[tuple[str, object]] = []
    services = _fixture(events)
    failing = FoundationsOracleServices(
        **{
            field: (
                (lambda *args: (_ for _ in ()).throw(RuntimeError("materialization failed")))
                if field == "materialize_repository"
                else getattr(services, field)
            )
            for field in services.__dataclass_fields__
        }
    )

    with pytest.raises(RuntimeError, match="materialization failed"):
        run_foundations_oracle_smoke(
            _inputs(),
            services=failing,
            lifecycle_factory=lambda spec: FakeLifecycle(spec, events),
        )
    assert events[-1] == ("database-exit", None)
