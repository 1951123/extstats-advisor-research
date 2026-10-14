from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from extstats_advisor_research.foundations_binding import (
    ArtifactBindingError,
    incidence_digest,
    validate_digest,
    validate_source_bindings,
)
from extstats_advisor_research.foundations_oracle import BlackBoxMembershipOracle


@dataclass(frozen=True)
class Candidate:
    candidate_id: str


@dataclass(frozen=True)
class Profile:
    query_id: str
    analysis_status: str = "supported"
    weight: float = 1.0


class FakeSession:
    def __init__(self, estimates: dict[str, int], failing_query: str | None = None) -> None:
        self.estimates = estimates
        self.failing_query = failing_query
        self.active_configuration = SimpleNamespace(ordered_candidate_ids=())
        self.activations: list[tuple[str, ...]] = []
        self.closed = False

    def activate(self, configuration) -> None:
        self.active_configuration = configuration
        self.activations.append(configuration.ordered_candidate_ids)

    def estimate_query(self, query_id: str):
        if query_id == self.failing_query:
            raise RuntimeError("synthetic planner failure")
        return SimpleNamespace(estimated_rows=self.estimates[query_id])

    def estimate_query_precise(self, query_id: str):
        if query_id == self.failing_query:
            raise RuntimeError("synthetic planner failure")
        value = float(self.estimates[query_id]) + 0.125
        return SimpleNamespace(
            estimated_rows=value, formatted_estimated_rows=self.estimates[query_id]
        )

    def close(self) -> None:
        self.closed = True


class LegacySession:
    def __init__(self) -> None:
        self.active_configuration = SimpleNamespace(ordered_candidate_ids=())

    def activate(self, configuration) -> None:
        self.active_configuration = configuration

    def estimate_query(self, query_id: str):
        return SimpleNamespace(estimated_rows={"q1": 2, "q2": 4}[query_id])

    def close(self) -> None:
        pass


class Utility:
    def evaluate(self, estimates):
        return SimpleNamespace(objective=sum(estimates.values()) / len(estimates))


def _inputs():
    candidates = (Candidate("b"), Candidate("a"))
    universe = SimpleNamespace(
        candidates=candidates,
        query_profiles=(Profile("q1"), Profile("q2")),
        semantic_digest="u" * 64,
    )
    snapshot = SimpleNamespace(
        semantic_digest="s" * 64,
        workload=SimpleNamespace(
            queries=(SimpleNamespace(query_id="q1"), SimpleNamespace(query_id="q2"))
        ),
    )
    return snapshot, universe


def test_digest_and_source_binding_fail_closed() -> None:
    with pytest.raises(ArtifactBindingError, match="digest mismatch"):
        validate_digest("a" * 64, "b" * 64, label="snapshot")
    with pytest.raises(ArtifactBindingError, match="source binding mismatch"):
        validate_source_bindings({"advisor": "old"}, {"advisor": "new"})
    assert len(incidence_digest(["q2", "q1"])) == 64
    assert incidence_digest(["q2", "q1"]) == incidence_digest(["q1", "q2"])


def test_empty_and_canonical_memberships_reuse_one_session() -> None:
    snapshot, universe = _inputs()
    session = FakeSession({"q1": 2, "q2": 4})
    oracle = BlackBoxMembershipOracle(
        session=session,
        snapshot=snapshot,
        candidate_universe=universe,
        global_precedence=("a", "b"),
        utility_provider=Utility(),
        expected_snapshot_digest="s" * 64,
        expected_candidate_universe_digest="u" * 64,
    )
    first = oracle.evaluate(("b", "a"))
    cached = oracle.evaluate(("a", "b"))
    empty = oracle.evaluate(())
    assert first.objective == 3.0
    assert first.diagnostics.membership == ("a", "b")
    assert cached.diagnostics.cache_hit
    assert empty.diagnostics.status == "complete"
    assert session.activations == [("a", "b"), ()]
    assert oracle.distinct_configuration_evaluations == 2
    assert oracle.planner_query_attempts == 4
    oracle.close()
    assert session.closed


def test_explicit_precedence_controls_pair_and_singleton_activation() -> None:
    snapshot, universe = _inputs()
    session = FakeSession({"q1": 2, "q2": 4})
    oracle = BlackBoxMembershipOracle(
        session=session,
        snapshot=snapshot,
        candidate_universe=universe,
        global_precedence=("a", "b"),
        utility_provider=Utility(),
    )

    pair = oracle.evaluate(("b", "a"))
    singleton = oracle.evaluate(("b",))

    assert pair.diagnostics.membership == ("a", "b")
    assert singleton.diagnostics.membership == ("b",)
    assert session.activations == [("a", "b"), ("b",)]


@pytest.mark.parametrize(
    ("precedence", "message"),
    [
        (("a",), "missing candidates"),
        (("a", "a"), "duplicates"),
        (("a", "b", "unknown"), "unknown candidates"),
    ],
)
def test_global_precedence_must_cover_each_eligible_candidate_once(
    precedence: tuple[str, ...], message: str
) -> None:
    snapshot, universe = _inputs()
    with pytest.raises(ValueError, match=message):
        BlackBoxMembershipOracle(
            session=FakeSession({"q1": 1, "q2": 1}),
            snapshot=snapshot,
            candidate_universe=universe,
            global_precedence=precedence,
            utility_provider=Utility(),
        )


def test_digest_and_workload_mismatch_are_rejected_before_planner_calls() -> None:
    snapshot, universe = _inputs()
    with pytest.raises(ValueError, match="snapshot digest"):
        BlackBoxMembershipOracle(
            session=FakeSession({"q1": 1, "q2": 1}),
            snapshot=snapshot,
            candidate_universe=universe,
            global_precedence=("a", "b"),
            utility_provider=Utility(),
            expected_snapshot_digest="x" * 64,
        )
    bad_snapshot = SimpleNamespace(
        semantic_digest="s" * 64,
        workload=SimpleNamespace(queries=(SimpleNamespace(query_id="other"),)),
    )
    with pytest.raises(ValueError, match="unknown queries"):
        BlackBoxMembershipOracle(
            session=FakeSession({"q1": 1, "q2": 1}),
            snapshot=bad_snapshot,
            candidate_universe=universe,
            global_precedence=("a", "b"),
            utility_provider=Utility(),
        )


def test_missing_native_candidate_and_partial_failure_are_explicit() -> None:
    snapshot, universe = _inputs()
    repository = SimpleNamespace(
        candidate_models=(SimpleNamespace(candidate_id="a", state="present"),)
    )
    with pytest.raises(ValueError, match="candidate order"):
        BlackBoxMembershipOracle(
            session=FakeSession({"q1": 1, "q2": 1}),
            snapshot=snapshot,
            candidate_universe=universe,
            global_precedence=("a", "b"),
            utility_provider=Utility(),
            native_repository=repository,
        )
    result = BlackBoxMembershipOracle(
        session=FakeSession({"q1": 1, "q2": 1}, failing_query="q2"),
        snapshot=snapshot,
        candidate_universe=universe,
        global_precedence=("a", "b"),
        utility_provider=Utility(),
    ).evaluate(("a",))
    assert result.objective is None
    assert result.diagnostics.status == "failed"
    assert result.diagnostics.planner_query_attempts == 2
    assert result.diagnostics.planner_query_successes == 1
    assert "synthetic planner failure" in (result.diagnostics.error or "")


def test_precise_evaluation_retains_fractional_rows_and_uses_separate_cache() -> None:
    snapshot, universe = _inputs()
    session = FakeSession({"q1": 2, "q2": 4})
    oracle = BlackBoxMembershipOracle(
        session=session,
        snapshot=snapshot,
        candidate_universe=universe,
        global_precedence=("a", "b"),
        utility_provider=Utility(),
    )

    ordinary = oracle.evaluate(("a",))
    precise = oracle.evaluate_precise(("a",))
    precise_cached = oracle.evaluate_precise(("a",))

    assert ordinary.estimates == {"q1": 2.0, "q2": 4.0}
    assert precise.estimates == {"q1": 2.125, "q2": 4.125}
    assert precise.formatted_estimates == {"q1": 2, "q2": 4}
    assert precise_cached.diagnostics.cache_hit
    assert oracle.distinct_configuration_evaluations == 2
    assert oracle.planner_query_attempts == 4


def test_precise_evaluation_fails_clearly_when_session_has_no_precise_method() -> None:
    snapshot, universe = _inputs()
    session = LegacySession()
    oracle = BlackBoxMembershipOracle(
        session=session,
        snapshot=snapshot,
        candidate_universe=universe,
        global_precedence=("a", "b"),
        utility_provider=Utility(),
    )

    result = oracle.evaluate_precise(("a",))

    assert result.diagnostics.status == "failed"
    assert "estimate_query_precise" in (result.diagnostics.error or "")
