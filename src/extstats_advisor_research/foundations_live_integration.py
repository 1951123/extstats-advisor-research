"""Offline-tested composition of the Foundations oracle and Advisor sandbox.

The module deliberately owns only the integration boundary.  Snapshot loading,
native-statistics materialization, sandbox preparation, and planner evaluation
remain Advisor responsibilities.  No Advisor import or database connection is
performed at module import time.

The native repository is kept in memory.  The pinned Advisor release exposes
materialization as a runtime object but only exposes repository construction
through its persistence helper, so this bridge uses the same manifest builder
and ``NativeStatsRepository`` model without writing a repository artifact.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .pins import verify_git_sha
from .postgres.experiment_database import (
    ExperimentDatabase,
    ExperimentDatabaseSpec,
)

SMOKE_CANDIDATE_A = "cand_0deae0d5de82a5a1fdc34111"
SMOKE_CANDIDATE_B = "cand_76679921292e8c769dbbd7bc"
# This integration path requires the Advisor revision that accepts the explicit
# physical-database binding.  Historical artifact pins remain unchanged.
FOUNDATIONS_INTEGRATION_ADVISOR_SHA = "2cb0f27f032f1fe4abdbf9e4d4db7e403885f30a"
DEFAULT_SAMPLE_ROWS = 30_000
DEFAULT_STATISTICS_TARGET = 100
MAX_SMOKE_QUERIES = 5


class FoundationsSmokeInputError(ValueError):
    """Raised when the explicit smoke input is incomplete or inconsistent."""


@dataclass(frozen=True, slots=True)
class FoundationsSmokeInput:
    """All experiment-owned inputs needed to compose one tiny oracle smoke run."""

    admin_dsn: str
    advisor_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    ground_truth_path: Path
    dataset_id: str
    relation_id: str
    relation_schema: str
    relation_name: str
    workload_id: str
    query_ids: tuple[str, ...]
    global_precedence: tuple[str, ...]
    expected_snapshot_digest: str
    expected_candidate_universe_digest: str
    expected_ground_truth_digest: str
    expected_advisor_sha: str = FOUNDATIONS_INTEGRATION_ADVISOR_SHA
    statistics_target: int = DEFAULT_STATISTICS_TARGET
    sample_rows: int = DEFAULT_SAMPLE_ROWS
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations"

    def __post_init__(self) -> None:
        for label in (
            "dataset_id",
            "relation_id",
            "relation_schema",
            "relation_name",
            "workload_id",
            "expected_snapshot_digest",
            "expected_candidate_universe_digest",
            "expected_ground_truth_digest",
            "expected_advisor_sha",
        ):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise FoundationsSmokeInputError(f"{label} must be a non-empty string")
        if not isinstance(self.admin_dsn, str) or not self.admin_dsn.strip():
            raise FoundationsSmokeInputError("admin_dsn must be an explicit non-empty DSN")
        if not self.query_ids or len(self.query_ids) > MAX_SMOKE_QUERIES:
            raise FoundationsSmokeInputError(
                f"query_ids must contain between 1 and {MAX_SMOKE_QUERIES} queries"
            )
        if any(not isinstance(query_id, str) or not query_id for query_id in self.query_ids):
            raise FoundationsSmokeInputError("query_ids must contain non-empty strings")
        if len(self.query_ids) != len(set(self.query_ids)):
            raise FoundationsSmokeInputError("query_ids must not contain duplicates")
        if any(
            not isinstance(candidate_id, str) or not candidate_id
            for candidate_id in self.global_precedence
        ):
            raise FoundationsSmokeInputError(
                "global_precedence must contain non-empty candidate IDs"
            )
        if len(self.global_precedence) != len(set(self.global_precedence)):
            raise FoundationsSmokeInputError("global_precedence must not contain duplicates")
        try:
            a_position = self.global_precedence.index(SMOKE_CANDIDATE_A)
            b_position = self.global_precedence.index(SMOKE_CANDIDATE_B)
        except ValueError as exc:
            raise FoundationsSmokeInputError(
                "global_precedence must include both smoke candidates"
            ) from exc
        if a_position > b_position:
            raise FoundationsSmokeInputError("smoke precedence must place A before B")
        if (
            isinstance(self.sample_rows, bool)
            or not isinstance(self.sample_rows, int)
            or self.sample_rows != DEFAULT_SAMPLE_ROWS
        ):
            raise FoundationsSmokeInputError(
                f"sample_rows must be the approved default {DEFAULT_SAMPLE_ROWS}"
            )
        if (
            isinstance(self.statistics_target, bool)
            or not isinstance(self.statistics_target, int)
            or not 1 <= self.statistics_target <= 10_000
        ):
            raise FoundationsSmokeInputError("statistics_target must be between 1 and 10000")
        if (
            isinstance(self.native_batch_size, bool)
            or not isinstance(self.native_batch_size, int)
            or self.native_batch_size < 1
        ):
            raise FoundationsSmokeInputError("native_batch_size must be a positive integer")


@dataclass(frozen=True, slots=True)
class FoundationsSmokeResult:
    """In-memory result; callers decide whether to persist a small observation."""

    database_name: str
    query_ids: tuple[str, ...]
    memberships: tuple[tuple[str, ...], ...]
    evaluations: tuple[OracleEvaluation, ...]
    sandbox_verification: Mapping[str, Any]

    @property
    def complete(self) -> bool:
        return all(item.diagnostics.status == "complete" for item in self.evaluations)


@dataclass(frozen=True, slots=True)
class FoundationsOracleServices:
    """Small dependency seam used by offline tests and the live composition."""

    load_snapshot: Callable[[Path], Any]
    load_candidate_universe: Callable[[Path, Any], Any]
    load_ground_truth: Callable[[Path, Any], Any]
    materialize_repository: Callable[[str, Any, Any, int, int], Any]
    prepare_sandbox: Callable[[str, Any, Any, Any, str, int], Any]
    verify_sandbox: Callable[[str, Any, Any, Any, str], Mapping[str, Any]]
    destroy_sandbox: Callable[[str], Any]
    planner_session: Callable[[str, Any, Any, Any, str], Any]
    make_utility: Callable[[Any, Any, Sequence[str]], Any]


def _default_services(advisor_root: Path, expected_advisor_sha: str) -> FoundationsOracleServices:
    verify_git_sha(Path(advisor_root), expected_advisor_sha)
    source = str(Path(advisor_root) / "src")
    if source not in sys.path:
        sys.path.insert(0, source)

    from extstats_advisor.candidates import load_candidate_universe
    from extstats_advisor.dbms.postgres import materialize_native_stats
    from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
    from extstats_advisor.dbms.postgres.sandbox import (
        destroy_postgres_planner_sandbox,
        prepare_postgres_planner_sandbox,
        verify_postgres_planner_sandbox,
    )
    from extstats_advisor.ground_truth.artifact import load_ground_truth_set
    from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
    from extstats_advisor.native_stats.model import NativeStatsRepository
    from extstats_advisor.native_stats.repository import _manifest
    from extstats_advisor.snapshot.bundle import load_snapshot
    from extstats_advisor.snapshot.model import Workload
    from extstats_advisor.utility import QErrorLoss, WeightedWorkloadUtility

    def materialize(dsn: str, snapshot: Any, universe: Any, target: int, batch_size: int) -> Any:
        materialization = materialize_native_stats(
            dsn,
            snapshot,
            universe,
            statistics_target=target,
            batch_size=batch_size,
        )
        return NativeStatsRepository(_manifest(materialization), materialization.payloads)

    def utility(snapshot: Any, truth: Any, query_ids: Sequence[str]) -> Any:
        selected = tuple(query_ids)
        queries = tuple(query for query in snapshot.workload.queries if query.query_id in selected)
        if tuple(query.query_id for query in queries) != selected:
            raise FoundationsSmokeInputError("query subset is not in snapshot workload order")
        workload = Workload(
            snapshot.workload.workload_id,
            queries,
            snapshot.workload.provenance,
        )
        return WeightedWorkloadUtility(
            workload,
            ArtifactGroundTruthProvider(truth),
            QErrorLoss(),
        )

    return FoundationsOracleServices(
        load_snapshot=load_snapshot,
        load_candidate_universe=load_candidate_universe,
        load_ground_truth=load_ground_truth_set,
        materialize_repository=materialize,
        prepare_sandbox=lambda dsn, snapshot, universe, repository, database, batch: (
            prepare_postgres_planner_sandbox(
                dsn,
                snapshot,
                universe,
                repository,
                physical_database=database,
                batch_size=batch,
            )
        ),
        verify_sandbox=lambda dsn, snapshot, universe, repository, database: (
            verify_postgres_planner_sandbox(
                dsn,
                snapshot,
                universe,
                repository,
                physical_database=database,
            )
        ),
        destroy_sandbox=destroy_postgres_planner_sandbox,
        planner_session=lambda dsn, snapshot, universe, repository, database: (
            PostgresPlannerSession(
                dsn,
                snapshot,
                universe,
                repository,
                physical_database=database,
            )
        ),
        make_utility=utility,
    )


def _validate_bound_inputs(inputs: FoundationsSmokeInput, services: FoundationsOracleServices):
    snapshot = services.load_snapshot(Path(inputs.snapshot_path))
    universe = services.load_candidate_universe(Path(inputs.candidate_universe_path), snapshot)
    ground_truth = services.load_ground_truth(Path(inputs.ground_truth_path), snapshot)
    if snapshot.semantic_digest != inputs.expected_snapshot_digest:
        raise FoundationsSmokeInputError("snapshot digest does not match smoke binding")
    if universe.semantic_digest != inputs.expected_candidate_universe_digest:
        raise FoundationsSmokeInputError("candidate-universe digest does not match smoke binding")
    if getattr(ground_truth, "semantic_digest", None) != inputs.expected_ground_truth_digest:
        raise FoundationsSmokeInputError("ground-truth digest does not match smoke binding")
    if snapshot.workload.workload_id != inputs.workload_id:
        raise FoundationsSmokeInputError("workload identity does not match smoke binding")
    if len(snapshot.schemas) != 1 or len(snapshot.samples) != 1:
        raise FoundationsSmokeInputError("smoke input must contain exactly one relation sample")
    schema = snapshot.schemas[0]
    if schema.relation_id != inputs.relation_id:
        raise FoundationsSmokeInputError("relation identity does not match smoke binding")
    if schema.relation_name.schema != inputs.relation_schema:
        raise FoundationsSmokeInputError("relation schema does not match smoke binding")
    if schema.relation_name.name != inputs.relation_name:
        raise FoundationsSmokeInputError("relation name does not match smoke binding")
    sample = snapshot.samples.get(inputs.relation_id)
    if sample is None or sample.num_rows != inputs.sample_rows:
        raise FoundationsSmokeInputError("sample row count does not match smoke binding")
    query_ids = tuple(query.query_id for query in snapshot.workload.queries)
    if any(query_id not in query_ids for query_id in inputs.query_ids):
        raise FoundationsSmokeInputError("smoke query is missing from the bound workload")
    candidate_ids = tuple(candidate.candidate_id for candidate in universe.candidates)
    if any(candidate_id not in candidate_ids for candidate_id in inputs.global_precedence):
        raise FoundationsSmokeInputError("global precedence contains an unknown candidate")
    if set(inputs.global_precedence) != set(candidate_ids):
        raise FoundationsSmokeInputError(
            "global precedence must cover the complete candidate universe"
        )
    return snapshot, universe, ground_truth


def _raise_with_cleanup(primary: BaseException | None, errors: list[BaseException]) -> None:
    if primary is not None and errors:
        raise BaseExceptionGroup("smoke execution and cleanup both failed", [primary, *errors])
    if errors:
        raise BaseExceptionGroup("smoke cleanup failed", errors)
    if primary is not None:
        raise primary


def run_foundations_oracle_smoke(
    inputs: FoundationsSmokeInput,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> FoundationsSmokeResult:
    """Compose one isolated M0/M1/M2 smoke run without persisting intermediates."""

    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    snapshot, universe, ground_truth = _validate_bound_inputs(inputs, services)
    spec = ExperimentDatabaseSpec(
        admin_dsn=inputs.admin_dsn,
        name_prefix=inputs.name_prefix,
        sample_rows=inputs.sample_rows,
    )
    lifecycle = lifecycle_factory(spec)
    result: FoundationsSmokeResult | None = None
    with lifecycle as database:
        oracle: BlackBoxMembershipOracle | None = None
        session: Any | None = None
        sandbox_prepared = False
        primary: BaseException | None = None
        cleanup_errors: list[BaseException] = []
        try:
            repository = services.materialize_repository(
                database.database_dsn,
                snapshot,
                universe,
                inputs.statistics_target,
                inputs.native_batch_size,
            )
            services.prepare_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
                inputs.native_batch_size,
            )
            sandbox_prepared = True
            verification = services.verify_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
            )
            session = services.planner_session(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
            )
            session.open()
            utility = services.make_utility(snapshot, ground_truth, inputs.query_ids)
            oracle = BlackBoxMembershipOracle(
                session=session,
                snapshot=snapshot,
                candidate_universe=universe,
                global_precedence=inputs.global_precedence,
                utility_provider=utility,
                native_repository=repository,
                query_ids=inputs.query_ids,
                expected_snapshot_digest=inputs.expected_snapshot_digest,
                expected_candidate_universe_digest=inputs.expected_candidate_universe_digest,
            )
            memberships = ((), (SMOKE_CANDIDATE_A,), (SMOKE_CANDIDATE_A, SMOKE_CANDIDATE_B))
            evaluations = tuple(oracle.evaluate(membership) for membership in memberships)
            result = FoundationsSmokeResult(
                database.database_name,
                inputs.query_ids,
                memberships,
                evaluations,
                verification,
            )
            oracle.close()
            oracle = None
            session = None
            services.destroy_sandbox(database.database_dsn)
            sandbox_prepared = False
        except BaseException as exc:  # noqa: BLE001 - preserve the original experiment error
            primary = exc
        finally:
            if oracle is not None:
                try:
                    oracle.close()
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            elif session is not None:
                try:
                    session.close()
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            if sandbox_prepared:
                try:
                    services.destroy_sandbox(database.database_dsn)
                except BaseException as exc:  # noqa: BLE001 - preserve cleanup evidence
                    cleanup_errors.append(exc)
            _raise_with_cleanup(primary, cleanup_errors)

    if result is None:  # pragma: no cover - defensive invariant after exception handling
        raise FoundationsSmokeInputError("smoke run produced no result")
    return result
