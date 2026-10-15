"""Opt-in PostgreSQL MCV mechanism validation for one frozen query.

The PostgreSQL diagnostic is deliberately transported as structured NOTICE
messages.  The normal Oracle path remains unchanged; this module enables the
backend trace only for the four bounded observations in this experiment.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .foundations_four_predicate_mcv import (
    rounding_compatible_with_multiplicativity,
    rounding_intervals,
)
from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
)
from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .pins import verify_git_sha
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

MECHANISM_QUERY_ID = "arecel_forest10_test_000019"
MCV_CANDIDATE_A = "cand_817be8bf7bbbc96c20bde230"
MCV_CANDIDATE_B = "cand_599a2efab190b6e2b7457ff1"
MECHANISM_MEMBERSHIPS = (
    (),
    (MCV_CANDIDATE_A,),
    (MCV_CANDIDATE_B,),
    (MCV_CANDIDATE_A, MCV_CANDIDATE_B),
)
FROZEN_QUERY_SQL = (
    'SELECT * FROM public.forest10 WHERE "aspect" >= 255.5751099710982 '
    'AND "slope" <= 41.85551782749934 '
    'AND "vertical_distance_to_hydrology" BETWEEN -55.65422654453859 AND 87.65422654453859 '
    'AND "hillshade_noon" BETWEEN 224.82700735192552 AND 225.17299264807448 '
    'AND "hillshade_3pm" BETWEEN 152.25032231680362 AND 215.74967768319638;'
)
FROZEN_QUERY_SQL_SHA256 = "3fb2202ac9f34dadbdedb4d1c660215f4bf15bbff664ac5415a631185f109cbf"
TRACE_PREFIX = "PGEXTADV_MCV_TRACE "
RESIDUAL_TOLERANCES = (1e-12, 1e-9, 1e-6, 1e-3)


class MechanismInputError(ValueError):
    """Raised when the final mechanism test is not frozen and complete."""


class MechanismEvaluationError(RuntimeError):
    """Raised when the opt-in trace cannot be associated with the target estimate."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite_positive(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise MechanismEvaluationError(f"expected a finite positive value, got {value!r}")
    return result


@dataclass(frozen=True, slots=True)
class MechanismBinding:
    admin_dsn: str
    advisor_root: Path
    postgres_root: Path
    research_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    ground_truth_path: Path
    global_precedence: tuple[str, ...]
    expected_snapshot_digest: str
    expected_candidate_universe_digest: str
    expected_ground_truth_digest: str
    expected_advisor_sha: str
    expected_research_sha: str
    expected_postgres_sha: str
    statistics_target: int = 100
    sample_rows: int = 30_000
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations_mechanism"

    def __post_init__(self) -> None:
        if self.statistics_target != 100 or self.sample_rows != 30_000:
            raise MechanismInputError("the mechanism test requires target 100 and 30,000 rows")
        if len(self.global_precedence) != 57 or len(set(self.global_precedence)) != 57:
            raise MechanismInputError("global precedence must contain 57 unique candidates")
        if self.global_precedence.index(MCV_CANDIDATE_A) > self.global_precedence.index(
            MCV_CANDIDATE_B
        ):
            raise MechanismInputError("global precedence must place A before B")


@dataclass(frozen=True, slots=True)
class MechanismObservation:
    membership: tuple[str, ...]
    evaluation: OracleEvaluation
    trace: tuple[Mapping[str, Any], ...]
    final_selectivity: float


@dataclass(frozen=True, slots=True)
class MechanismResult:
    binding: MechanismBinding
    database_name: str
    relation_oid: int
    observations: tuple[MechanismObservation, ...]
    sandbox_verification: Mapping[str, Any]
    cleanup_verified: bool

    @property
    def complete(self) -> bool:
        return (
            self.cleanup_verified
            and len(self.observations) == 4
            and all(item.evaluation.diagnostics.status == "complete" for item in self.observations)
        )


class _RawEstimateUtility:
    def evaluate(self, estimates: Mapping[str, float]) -> Any:
        return SimpleNamespace(objective=float(estimates[MECHANISM_QUERY_ID]))


def _validate_inputs(
    binding: MechanismBinding, services: FoundationsOracleServices
) -> tuple[Any, Any]:
    snapshot = services.load_snapshot(Path(binding.snapshot_path))
    universe = services.load_candidate_universe(Path(binding.candidate_universe_path), snapshot)
    truth = services.load_ground_truth(Path(binding.ground_truth_path), snapshot)
    if snapshot.semantic_digest != binding.expected_snapshot_digest:
        raise MechanismInputError("snapshot digest mismatch")
    if universe.semantic_digest != binding.expected_candidate_universe_digest:
        raise MechanismInputError("candidate-universe digest mismatch")
    if getattr(truth, "semantic_digest", None) != binding.expected_ground_truth_digest:
        raise MechanismInputError("ground-truth digest mismatch")
    query = next((q for q in snapshot.workload.queries if q.query_id == MECHANISM_QUERY_ID), None)
    if query is None or query.sql != FROZEN_QUERY_SQL:
        raise MechanismInputError("frozen query 000019 SQL does not match")
    if _sha256_text(query.sql) != FROZEN_QUERY_SQL_SHA256:
        raise MechanismInputError("frozen query 000019 SQL digest does not match")
    candidate_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if set(binding.global_precedence) != set(candidate_by_id):
        raise MechanismInputError("global precedence does not match CandidateUniverse")
    expected = {
        MCV_CANDIDATE_A: ("postgresql.mcv", ("aspect", "hillshade_3pm"), 3),
        MCV_CANDIDATE_B: (
            "postgresql.mcv",
            ("vertical_distance_to_hydrology", "hillshade_noon"),
            12,
        ),
    }
    for candidate_id, (kind, columns, rank) in expected.items():
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate.kind != kind or tuple(candidate.column_names) != columns:
            raise MechanismInputError(f"candidate metadata mismatch: {candidate_id}")
        if candidate.static_precedence_rank != rank:
            raise MechanismInputError(f"candidate precedence rank mismatch: {candidate_id}")
    return snapshot, universe


def _parse_trace(message: str) -> dict[str, Any] | None:
    if not message.startswith(TRACE_PREFIX):
        return None
    value = json.loads(message[len(TRACE_PREFIX) :])
    if not isinstance(value, dict) or value.get("event") not in {
        "mcv_application",
        "base_selectivity",
    }:
        raise MechanismEvaluationError(f"unexpected PostgreSQL trace event: {value!r}")
    return value


def _database_exists(admin_dsn: str, database_name: str) -> bool:
    import psycopg

    with psycopg.connect(
        admin_dsn, autocommit=True, application_name="extstats-mechanism-cleanup"
    ) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (database_name,)
            ).fetchone()
            is not None
        )


def run_mcv_mechanism_experiment(
    binding: MechanismBinding,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> MechanismResult:
    """Run exactly four traced planner evaluations in one owned database."""

    verify_git_sha(binding.research_root, binding.expected_research_sha)
    verify_git_sha(binding.postgres_root, binding.expected_postgres_sha)
    services = services or _default_services(binding.advisor_root, binding.expected_advisor_sha)
    snapshot, universe = _validate_inputs(binding, services)
    lifecycle = lifecycle_factory(
        ExperimentDatabaseSpec(
            admin_dsn=binding.admin_dsn,
            name_prefix=binding.name_prefix,
            sample_rows=binding.sample_rows,
        )
    )
    result: MechanismResult | None = None
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
                binding.statistics_target,
                binding.native_batch_size,
            )
            present = {
                candidate.candidate_id
                for candidate in repository.candidate_models
                if candidate.state == "present"
            }
            if not {MCV_CANDIDATE_A, MCV_CANDIDATE_B}.issubset(present):
                raise MechanismInputError("required MCV payload is not present")
            services.prepare_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
                binding.native_batch_size,
            )
            sandbox_prepared = True
            verification = services.verify_sandbox(
                database.database_dsn, snapshot, universe, repository, database.database_name
            )
            session = services.planner_session(
                database.database_dsn, snapshot, universe, repository, database.database_name
            )
            session.open()
            relation_oid = int(
                session.connection.execute(
                    "SELECT %s::regclass::oid", ("public.forest10",)
                ).fetchone()[0]
            )
            notices: list[dict[str, Any]] = []

            def notice_handler(diagnostic: Any) -> None:
                event = _parse_trace(getattr(diagnostic, "message_primary", ""))
                if event is not None:
                    notices.append(event)

            session.connection.add_notice_handler(notice_handler)
            session.connection.execute("SET LOCAL pgextadv.mcv_trace = 'on'")
            oracle = BlackBoxMembershipOracle(
                session=session,
                snapshot=snapshot,
                candidate_universe=universe,
                global_precedence=binding.global_precedence,
                utility_provider=_RawEstimateUtility(),
                native_repository=repository,
                query_ids=(MECHANISM_QUERY_ID,),
                expected_snapshot_digest=binding.expected_snapshot_digest,
                expected_candidate_universe_digest=binding.expected_candidate_universe_digest,
            )
            oid_to_candidate = {
                oid: candidate_id for candidate_id, oid in session.registered_oids.items()
            }
            observations: list[MechanismObservation] = []
            for membership in MECHANISM_MEMBERSHIPS:
                notices.clear()
                evaluation = oracle.evaluate_precise(membership)
                trace = []
                for event in notices:
                    event_copy = dict(event)
                    if event_copy.get("event") == "mcv_application":
                        event_copy["candidate_id"] = oid_to_candidate.get(
                            event_copy.get("stat_oid")
                        )
                    trace.append(event_copy)
                final_events = [
                    event
                    for event in trace
                    if event.get("event") == "base_selectivity"
                    and int(event.get("relation_oid", -1)) == relation_oid
                ]
                if not final_events:
                    raise MechanismEvaluationError(
                        f"no base selectivity trace for membership {membership!r}"
                    )
                final_selectivity = _finite_positive(final_events[-1]["selectivity"])
                observations.append(
                    MechanismObservation(
                        tuple(membership), evaluation, tuple(trace), final_selectivity
                    )
                )
            result = MechanismResult(
                binding,
                database.database_name,
                relation_oid,
                tuple(observations),
                verification,
                False,
            )
            oracle.close()
            oracle = None
            session = None
            services.destroy_sandbox(database.database_dsn)
            sandbox_prepared = False
        except BaseException as exc:  # noqa: BLE001 - preserve primary live failure
            primary = exc
        finally:
            if oracle is not None:
                try:
                    oracle.close()
                except BaseException as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
            elif session is not None:
                try:
                    session.close()
                except BaseException as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
            if sandbox_prepared:
                try:
                    services.destroy_sandbox(database.database_dsn)
                except BaseException as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
            _raise_with_cleanup(primary, cleanup_errors)
    if result is None:
        raise MechanismEvaluationError("mechanism run returned no result")
    cleanup_verified = not _database_exists(binding.admin_dsn, result.database_name)
    if not cleanup_verified:
        raise MechanismEvaluationError(f"owned database remains: {result.database_name}")
    return MechanismResult(
        result.binding,
        result.database_name,
        result.relation_oid,
        result.observations,
        result.sandbox_verification,
        cleanup_verified,
    )


def _metrics(result: MechanismResult) -> dict[str, Any]:
    by_membership = {observation.membership: observation for observation in result.observations}
    labels = {
        (): "empty",
        (MCV_CANDIDATE_A,): "a",
        (MCV_CANDIDATE_B,): "b",
        (MCV_CANDIDATE_A, MCV_CANDIDATE_B): "ab",
    }
    estimates = {
        labels[key]: _finite_positive(by_membership[key].evaluation.estimates[MECHANISM_QUERY_ID])
        for key in labels
    }
    selectivities = {labels[key]: by_membership[key].final_selectivity for key in labels}
    plan_rows = {
        labels[key]: int(by_membership[key].evaluation.formatted_estimates[MECHANISM_QUERY_ID])
        for key in labels
    }
    delta_rows = (
        math.log(estimates["ab"])
        - math.log(estimates["a"])
        - math.log(estimates["b"])
        + math.log(estimates["empty"])
    )
    delta_sel = (
        math.log(selectivities["ab"])
        - math.log(selectivities["a"])
        - math.log(selectivities["b"])
        + math.log(selectivities["empty"])
    )
    return {
        "raw_precise_plan_rows": estimates,
        "formatted_plan_rows": plan_rows,
        "pre_rounded_selectivities": selectivities,
        "r_b_empty_to_b": selectivities["b"] / selectivities["empty"],
        "r_b_after_a": selectivities["ab"] / selectivities["a"],
        "conditional_b_relative_difference": selectivities["ab"] / selectivities["a"]
        - selectivities["b"] / selectivities["empty"],
        "delta_rows": delta_rows,
        "rho_rows": math.exp(delta_rows),
        "delta_selectivity": delta_sel,
        "rho_selectivity": math.exp(delta_sel),
        "passes_by_tolerance": {
            str(tolerance): abs(delta_sel) <= tolerance for tolerance in RESIDUAL_TOLERANCES
        },
        "rounding_compatible": rounding_compatible_with_multiplicativity(plan_rows),
        "rounding_intervals": {
            key: list(value) for key, value in rounding_intervals(plan_rows).items()
        },
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value cannot be serialized")
    return value


def write_mcv_mechanism_result_json(result: MechanismResult, destination: Path) -> None:
    """Publish one final trace artifact atomically without overwriting."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    configurations = []
    for observation in result.observations:
        evaluation = observation.evaluation
        configurations.append(
            {
                "membership": list(observation.membership),
                "activation_membership": list(evaluation.diagnostics.membership),
                "status": evaluation.diagnostics.status,
                "precise_plan_rows": None
                if evaluation.estimates is None
                else evaluation.estimates.get(MECHANISM_QUERY_ID),
                "formatted_plan_rows": None
                if evaluation.formatted_estimates is None
                else evaluation.formatted_estimates.get(MECHANISM_QUERY_ID),
                "pre_rounded_selectivity": observation.final_selectivity,
                "trace": list(observation.trace),
                "diagnostics": evaluation.diagnostics.as_dict(),
            }
        )
    payload = {
        "experiment_id": "foundations-mcv-mechanism-v1",
        "complete": result.complete,
        "input_binding": {
            "query_id": MECHANISM_QUERY_ID,
            "query_sql": FROZEN_QUERY_SQL,
            "query_sql_sha256": FROZEN_QUERY_SQL_SHA256,
            "sample_rows": result.binding.sample_rows,
            "statistics_target": result.binding.statistics_target,
            "snapshot_semantic_digest": result.binding.expected_snapshot_digest,
            "candidate_universe_semantic_digest": result.binding.expected_candidate_universe_digest,
            "ground_truth_semantic_digest": result.binding.expected_ground_truth_digest,
            "global_precedence": list(result.binding.global_precedence),
            "candidates": [
                {
                    "candidate_id": MCV_CANDIDATE_A,
                    "kind": "postgresql.mcv",
                    "columns": ["aspect", "hillshade_3pm"],
                    "precedence_rank": 3,
                },
                {
                    "candidate_id": MCV_CANDIDATE_B,
                    "kind": "postgresql.mcv",
                    "columns": ["vertical_distance_to_hydrology", "hillshade_noon"],
                    "precedence_rank": 12,
                },
            ],
            "source_commits": {
                "research": result.binding.expected_research_sha,
                "advisor": result.binding.expected_advisor_sha,
                "patched_postgres": result.binding.expected_postgres_sha,
            },
        },
        "runtime": {
            "database_name": result.database_name,
            "relation_oid": result.relation_oid,
            "sandbox_verification": dict(result.sandbox_verification),
            "cleanup_verified_by_database_catalog_check": result.cleanup_verified,
        },
        "configurations": configurations,
        "metrics": _metrics(result),
    }
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=f".{uuid.uuid4().hex}.tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
