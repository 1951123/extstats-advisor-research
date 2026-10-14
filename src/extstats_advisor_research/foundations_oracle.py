"""Research-owned black-box membership oracle for Foundations experiments.

The adapter composes existing Advisor planner and utility contracts.  It does not inspect
PostgreSQL estimator formulas and it does not perform candidate search.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from .foundations_interaction import (
    ConfigurationObservation,
    LandscapeValidationError,
    canonical_membership,
    configuration_identity,
    objective_from_utility_result,
)


class OracleIntegrationError(ValueError):
    """Raised when a membership cannot be evaluated under the declared binding."""


class PlannerSession(Protocol):
    active_configuration: Any

    def activate(self, configuration: Any) -> None: ...

    def estimate_query(self, query_id: str) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class OracleDiagnostics:
    configuration_id: str
    membership: tuple[str, ...]
    status: str
    elapsed_ns: int
    oracle_invocations: int
    planner_query_attempts: int
    planner_query_successes: int
    cache_hit: bool
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "membership": list(self.membership),
            "status": self.status,
            "elapsed_ns": self.elapsed_ns,
            "oracle_invocations": self.oracle_invocations,
            "planner_query_attempts": self.planner_query_attempts,
            "planner_query_successes": self.planner_query_successes,
            "cache_hit": self.cache_hit,
            "error": self.error,
        }


@dataclass(frozen=True)
class OracleEvaluation:
    objective: float | None
    utility_result: Any | None
    diagnostics: OracleDiagnostics
    estimates: Mapping[str, float] | None = None

    def as_observation(self) -> ConfigurationObservation:
        diagnostics = self.diagnostics
        if diagnostics.status == "complete":
            if self.objective is None:
                raise OracleIntegrationError("complete evaluation has no objective")
            return ConfigurationObservation.complete(
                diagnostics.membership,
                self.objective,
                elapsed_ns=diagnostics.elapsed_ns,
                oracle_invocations=diagnostics.oracle_invocations,
                planner_query_count=diagnostics.planner_query_successes,
                cache_hit=diagnostics.cache_hit,
            )
        return ConfigurationObservation.failed(
            diagnostics.membership,
            diagnostics.error or "oracle evaluation failed",
            elapsed_ns=diagnostics.elapsed_ns,
            oracle_invocations=diagnostics.oracle_invocations,
            planner_query_count=diagnostics.planner_query_successes,
        )


def _candidate_ids(universe: Any) -> tuple[str, ...]:
    return tuple(candidate.candidate_id for candidate in universe.candidates)


def _positive_query_ids(
    universe: Any,
    snapshot: Any | None,
    requested_query_ids: Sequence[str] | None = None,
) -> tuple[str, ...]:
    profiles = tuple(universe.query_profiles)
    supported = tuple(
        profile.query_id
        for profile in profiles
        if profile.analysis_status == "supported" and profile.weight > 0
    )
    if not supported:
        raise OracleIntegrationError("candidate universe has no positive-weight supported queries")
    if requested_query_ids is None:
        result = supported
    else:
        result = tuple(requested_query_ids)
        if any(not isinstance(query_id, str) or not query_id for query_id in result):
            raise OracleIntegrationError("query_ids must contain non-empty query IDs")
        if len(result) != len(set(result)):
            raise OracleIntegrationError("query_ids must not contain duplicates")
        unknown = sorted(set(result) - set(supported))
        if unknown:
            raise OracleIntegrationError(
                f"query_ids are not positive-weight supported queries: {unknown}"
            )
    if snapshot is not None:
        workload_ids = {query.query_id for query in snapshot.workload.queries}
        unknown = set(result) - workload_ids
        if unknown:
            raise OracleIntegrationError(
                f"candidate universe references unknown queries: {sorted(unknown)}"
            )
    return result


class BlackBoxMembershipOracle:
    """Evaluate arbitrary memberships through one reusable Advisor planner session."""

    def __init__(
        self,
        *,
        session: PlannerSession,
        snapshot: Any,
        candidate_universe: Any,
        global_precedence: Sequence[str],
        utility_provider: Any,
        native_repository: Any | None = None,
        query_ids: Sequence[str] | None = None,
        expected_snapshot_digest: str | None = None,
        expected_candidate_universe_digest: str | None = None,
    ) -> None:
        self._session = session
        self._snapshot = snapshot
        self._universe = candidate_universe
        self._utility = utility_provider
        self._candidate_order = _candidate_ids(candidate_universe)
        if not self._candidate_order:
            raise OracleIntegrationError("candidate universe is empty")
        self._global_precedence = self._validate_global_precedence(global_precedence)
        self._query_ids = _positive_query_ids(candidate_universe, snapshot, query_ids)
        if (
            expected_snapshot_digest is not None
            and snapshot.semantic_digest != expected_snapshot_digest
        ):
            raise OracleIntegrationError("snapshot digest does not match oracle binding")
        if expected_candidate_universe_digest is not None:
            actual = getattr(candidate_universe, "semantic_digest", None)
            if actual != expected_candidate_universe_digest:
                raise OracleIntegrationError(
                    "candidate-universe digest does not match oracle binding"
                )
        if native_repository is not None:
            repository_ids = tuple(item.candidate_id for item in native_repository.candidate_models)
            if repository_ids != self._candidate_order:
                raise OracleIntegrationError(
                    "native repository candidate order does not match universe"
                )
            absent = {
                item.candidate_id
                for item in native_repository.candidate_models
                if item.state == "absent-native"
            }
            if absent:
                raise OracleIntegrationError(
                    f"native repository is missing payloads for candidates: {sorted(absent)}"
                )
        self._cache: dict[tuple[str, ...], OracleEvaluation] = {}
        self.distinct_configuration_evaluations = 0
        self.oracle_invocations = 0
        self.planner_query_attempts = 0
        self.planner_query_successes = 0

    @property
    def query_ids(self) -> tuple[str, ...]:
        return self._query_ids

    @property
    def candidate_order(self) -> tuple[str, ...]:
        return self._candidate_order

    @property
    def global_precedence(self) -> tuple[str, ...]:
        return self._global_precedence

    def _validate_global_precedence(self, precedence: Sequence[str]) -> tuple[str, ...]:
        ordered = tuple(precedence)
        if any(not isinstance(candidate_id, str) or not candidate_id for candidate_id in ordered):
            raise OracleIntegrationError("global precedence must contain non-empty candidate IDs")
        if len(ordered) != len(set(ordered)):
            raise OracleIntegrationError("global precedence must not contain duplicates")
        eligible = set(self._candidate_order)
        actual = set(ordered)
        missing = sorted(eligible - actual)
        unknown = sorted(actual - eligible)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing candidates: {missing}")
            if unknown:
                details.append(f"unknown candidates: {unknown}")
            raise OracleIntegrationError("global precedence mismatch (" + "; ".join(details) + ")")
        return ordered

    def _configuration(self, membership: Sequence[str]) -> tuple[str, ...]:
        validated = canonical_membership(self._candidate_order, membership)
        selected = set(validated)
        return tuple(
            candidate_id for candidate_id in self._global_precedence if candidate_id in selected
        )

    def _activate(self, membership: tuple[str, ...]) -> None:
        from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

        configuration = PostgresStatisticsConfiguration(membership)
        self._session.activate(configuration)
        active = getattr(self._session, "active_configuration", None)
        observed = getattr(active, "ordered_candidate_ids", None)
        if observed is not None and tuple(observed) != membership:
            raise OracleIntegrationError(
                f"planner activated {tuple(observed)!r}, expected {membership!r}"
            )

    def evaluate(self, membership: Iterable[str], *, use_cache: bool = True) -> OracleEvaluation:
        try:
            ordered = self._configuration(tuple(membership))
        except (LandscapeValidationError, OracleIntegrationError) as exc:
            ordered = tuple(membership)
            try:
                invalid_identity = configuration_identity(ordered)
            except LandscapeValidationError:
                invalid_identity = "cfg-invalid"
            diagnostics = OracleDiagnostics(
                invalid_identity,
                ordered,
                "failed",
                0,
                0,
                0,
                0,
                False,
                f"{type(exc).__name__}: {exc}",
            )
            return OracleEvaluation(None, None, diagnostics)
        if use_cache and ordered in self._cache:
            cached = self._cache[ordered]
            prior = cached.diagnostics
            return OracleEvaluation(
                cached.objective,
                cached.utility_result,
                OracleDiagnostics(
                    prior.configuration_id,
                    prior.membership,
                    prior.status,
                    0,
                    0,
                    0,
                    0,
                    True,
                    prior.error,
                ),
                cached.estimates,
            )

        self.distinct_configuration_evaluations += 1
        self.oracle_invocations += 1
        started = time.perf_counter_ns()
        attempts = successes = 0
        estimates: dict[str, float] = {}
        try:
            self._activate(ordered)
            for query_id in self._query_ids:
                attempts += 1
                self.planner_query_attempts += 1
                estimate = self._session.estimate_query(query_id)
                value = getattr(estimate, "estimated_rows", None)
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    raise OracleIntegrationError(
                        f"invalid estimate for query {query_id}: {value!r}"
                    )
                estimates[query_id] = float(value)
                successes += 1
                self.planner_query_successes += 1
            utility_result = self._utility.evaluate(estimates)
            objective = objective_from_utility_result(utility_result)
            diagnostics = OracleDiagnostics(
                configuration_identity(ordered),
                ordered,
                "complete",
                time.perf_counter_ns() - started,
                1,
                attempts,
                successes,
                False,
            )
            result = OracleEvaluation(objective, utility_result, diagnostics, dict(estimates))
            self._cache[ordered] = result
            return result
        except Exception as exc:  # noqa: BLE001 - every planner failure is an explicit observation
            diagnostics = OracleDiagnostics(
                configuration_identity(ordered),
                ordered,
                "failed",
                time.perf_counter_ns() - started,
                1,
                attempts,
                successes,
                False,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
            )
            return OracleEvaluation(None, None, diagnostics, dict(estimates))

    def close(self) -> None:
        self._session.close()
