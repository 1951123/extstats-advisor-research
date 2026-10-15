"""One bounded pre-clamp selectivity diagnostic for the frozen 000146 query."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
)
from .foundations_mcv_mechanism import _parse_trace
from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .pins import verify_git_sha
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

RAW_QUERY_ID = "arecel_forest10_test_000146"
RAW_CANDIDATE_C0 = "cand_0deae0d5de82a5a1fdc34111"
RAW_CANDIDATE_C1 = "cand_1537a6ae8ef67c379c8d0fa0"
RAW_CANDIDATES = (RAW_CANDIDATE_C0, RAW_CANDIDATE_C1)
RAW_MEMBERSHIPS = ((), (RAW_CANDIDATE_C0,), (RAW_CANDIDATE_C1,))
RAW_QUERY_SQL = (
    'SELECT * FROM public.forest10 WHERE "elevation" BETWEEN 2737.8645674718964 '
    'AND 2850.1354325281036 AND "aspect" BETWEEN 28.867389030497392 '
    'AND 45.13261096950261 AND "slope" BETWEEN 7.514216234095868 '
    'AND 16.48578376590413 AND "horizontal_distance_to_roadways" BETWEEN '
    '1850.7803616377864 AND 4869.219638362214 AND "hillshade_9am" BETWEEN '
    '199.3192636997884 AND 238.6807363002116 AND "hillshade_noon" BETWEEN '
    "204.55712430873774 AND 223.44287569126226 AND "
    '"horizontal_distance_to_fire_points" BETWEEN 2746.018608056267 '
    "AND 2785.981391943733;"
)
RAW_QUERY_SQL_SHA256 = hashlib.sha256(RAW_QUERY_SQL.encode("utf-8")).hexdigest()
RAW_RESIDUAL_TOLERANCES = (1e-12, 1e-9, 1e-6, 1e-3)


class RawSelectivityInputError(ValueError):
    """Raised when the frozen raw-selectivity binding is invalid."""


class RawSelectivityEvaluationError(RuntimeError):
    """Raised when the target pre-clamp event cannot be identified safely."""


@dataclass(frozen=True, slots=True)
class RawSelectivityBinding:
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
    name_prefix: str = "exp_foundations_raw_selectivity"

    def __post_init__(self) -> None:
        if self.statistics_target != 100 or self.sample_rows != 30_000:
            raise RawSelectivityInputError("raw diagnostic requires target 100 and 30,000 rows")
        if len(self.global_precedence) != 57 or len(set(self.global_precedence)) != 57:
            raise RawSelectivityInputError("global precedence must contain 57 unique candidates")
        if not set(RAW_CANDIDATES).issubset(self.global_precedence):
            raise RawSelectivityInputError(
                "global precedence must contain both diagnostic candidates"
            )


@dataclass(frozen=True, slots=True)
class RawSelectivityObservation:
    membership: tuple[str, ...]
    activation_membership: tuple[str, ...]
    evaluation: OracleEvaluation
    trace: tuple[Mapping[str, Any], ...]
    base_selectivity_event: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class RawSelectivityResult:
    binding: RawSelectivityBinding
    database_name: str
    relation_oid: int
    observations: tuple[RawSelectivityObservation, ...]
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
        return type("RawObjective", (), {"objective": float(estimates[RAW_QUERY_ID])})()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite_positive(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise RawSelectivityEvaluationError(f"{label} must be finite and positive: {value!r}")
    return result


def raw_log_correction(singleton_rows: float, baseline_rows: float) -> float:
    """Return a log correction from positive pre-clamp observations only."""

    singleton = _finite_positive(singleton_rows, "singleton raw rows")
    baseline = _finite_positive(baseline_rows, "baseline raw rows")
    return math.log(singleton) - math.log(baseline)


def _validate_inputs(
    binding: RawSelectivityBinding, services: FoundationsOracleServices
) -> tuple[Any, Any]:
    snapshot = services.load_snapshot(Path(binding.snapshot_path))
    universe = services.load_candidate_universe(Path(binding.candidate_universe_path), snapshot)
    services.load_ground_truth(Path(binding.ground_truth_path), snapshot)
    if snapshot.semantic_digest != binding.expected_snapshot_digest:
        raise RawSelectivityInputError("snapshot digest mismatch")
    if universe.semantic_digest != binding.expected_candidate_universe_digest:
        raise RawSelectivityInputError("candidate-universe digest mismatch")
    query = next((q for q in snapshot.workload.queries if q.query_id == RAW_QUERY_ID), None)
    if query is None or query.sql != RAW_QUERY_SQL:
        raise RawSelectivityInputError("frozen 000146 SQL does not match")
    if _sha256_text(query.sql) != RAW_QUERY_SQL_SHA256:
        raise RawSelectivityInputError("frozen 000146 SQL digest does not match")
    candidate_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if set(binding.global_precedence) != set(candidate_by_id):
        raise RawSelectivityInputError("global precedence does not match CandidateUniverse")
    expected = {
        RAW_CANDIDATE_C0: ("postgresql.mcv", ("slope", "hillshade_noon")),
        RAW_CANDIDATE_C1: ("postgresql.mcv", ("aspect", "hillshade_9am")),
    }
    for candidate_id, (kind, columns) in expected.items():
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate.kind != kind or tuple(candidate.column_names) != columns:
            raise RawSelectivityInputError(f"candidate metadata mismatch: {candidate_id}")
    return snapshot, universe


def _ordered_membership(
    membership: Sequence[str], global_precedence: Sequence[str]
) -> tuple[str, ...]:
    selected = set(membership)
    return tuple(candidate_id for candidate_id in global_precedence if candidate_id in selected)


def _target_base_event(
    trace: Sequence[Mapping[str, Any]], relation_oid: int, final_plan_rows: int
) -> Mapping[str, Any]:
    events = [
        event
        for event in trace
        if event.get("event") == "base_selectivity"
        and int(event.get("relation_oid", -1)) == relation_oid
    ]
    matching = [event for event in events if event.get("rows_after_clamp") == final_plan_rows]
    if not matching:
        raise RawSelectivityEvaluationError(
            "no target base-selectivity event matches relation and final Plan Rows"
        )
    normalized = {
        json.dumps(dict(event), sort_keys=True, separators=(",", ":")) for event in matching
    }
    if len(normalized) != 1:
        raise RawSelectivityEvaluationError(
            "multiple non-identical target base-selectivity events were observed"
        )
    return matching[0]


def run_raw_selectivity_diagnostic(
    binding: RawSelectivityBinding,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> RawSelectivityResult:
    """Run exactly four traced Planner evaluations for frozen query 000146."""

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
    result: RawSelectivityResult | None = None
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
            if not set(RAW_CANDIDATES).issubset(present):
                raise RawSelectivityInputError("required MCV payload is not present")
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
                query_ids=(RAW_QUERY_ID,),
                expected_snapshot_digest=binding.expected_snapshot_digest,
                expected_candidate_universe_digest=binding.expected_candidate_universe_digest,
            )
            oid_to_candidate = {
                oid: candidate_id for candidate_id, oid in session.registered_oids.items()
            }
            observations: list[RawSelectivityObservation] = []
            memberships = (*RAW_MEMBERSHIPS, RAW_CANDIDATES)
            for membership in memberships:
                ordered = _ordered_membership(membership, binding.global_precedence)
                notices.clear()
                evaluation = oracle.evaluate_precise(ordered)
                trace: list[Mapping[str, Any]] = []
                for event in notices:
                    copied = dict(event)
                    if copied.get("event") == "mcv_application":
                        copied["candidate_id"] = oid_to_candidate.get(copied.get("stat_oid"))
                    trace.append(copied)
                final_rows = int(evaluation.formatted_estimates[RAW_QUERY_ID])
                base_event = _target_base_event(trace, relation_oid, final_rows)
                _finite_positive(base_event.get("selectivity"), "pre-clamp selectivity")
                _finite_positive(base_event.get("rows_before_clamp"), "pre-clamp rows")
                _finite_positive(base_event.get("tuples"), "relation cardinality")
                observations.append(
                    RawSelectivityObservation(
                        tuple(membership),
                        ordered,
                        evaluation,
                        tuple(trace),
                        base_event,
                    )
                )
            result = RawSelectivityResult(
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
        except BaseException as exc:  # noqa: BLE001 - preserve live failure and cleanup
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
        raise RawSelectivityEvaluationError("raw-selectivity run returned no result")
    import psycopg

    with psycopg.connect(
        binding.admin_dsn, autocommit=True, application_name="extstats-raw-selectivity-cleanup"
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
            (result.database_name,),
        ).fetchone()
    if exists is not None:
        raise RawSelectivityEvaluationError(f"owned database remains: {result.database_name}")
    return RawSelectivityResult(
        result.binding,
        result.database_name,
        result.relation_oid,
        result.observations,
        result.sandbox_verification,
        True,
    )


def _metrics(result: RawSelectivityResult) -> dict[str, Any]:
    observations = {frozenset(item.membership): item for item in result.observations}
    labels = {
        frozenset(): "empty",
        frozenset((RAW_CANDIDATE_C0,)): "c0",
        frozenset((RAW_CANDIDATE_C1,)): "c1",
        frozenset(RAW_CANDIDATES): "c0_c1",
    }
    selectivities = {
        label: _finite_positive(item.base_selectivity_event["selectivity"], f"{label} selectivity")
        for membership, label in labels.items()
        for item in [observations[membership]]
    }
    raw_rows = {
        label: _finite_positive(item.base_selectivity_event["rows_before_clamp"], f"{label} rows")
        for membership, label in labels.items()
        for item in [observations[membership]]
    }
    relation_rows = {
        label: _finite_positive(item.base_selectivity_event["tuples"], f"{label} tuples")
        for membership, label in labels.items()
        for item in [observations[membership]]
    }
    if max(relation_rows.values()) - min(relation_rows.values()) > 1e-9:
        raise RawSelectivityEvaluationError("relation cardinality multiplier changed across runs")
    delta_selectivity = (
        math.log(selectivities["c0_c1"])
        + math.log(selectivities["empty"])
        - math.log(selectivities["c0"])
        - math.log(selectivities["c1"])
    )
    delta_rows = (
        math.log(raw_rows["c0_c1"])
        + math.log(raw_rows["empty"])
        - math.log(raw_rows["c0"])
        - math.log(raw_rows["c1"])
    )
    predicted_raw_rows = raw_rows["c0"] * raw_rows["c1"] / raw_rows["empty"]
    predicted_selectivity = selectivities["c0"] * selectivities["c1"] / selectivities["empty"]
    return {
        "selectivity_pre_clamp": selectivities,
        "rows_pre_clamp": raw_rows,
        "relation_tuples_multiplier": relation_rows,
        "plan_rows_clamped": {
            label: int(observations[membership].evaluation.formatted_estimates[RAW_QUERY_ID])
            for membership, label in labels.items()
        },
        "predicted_selectivity_c0_c1": predicted_selectivity,
        "predicted_rows_pre_clamp_c0_c1": predicted_raw_rows,
        "delta_selectivity": delta_selectivity,
        "rho_selectivity": math.exp(delta_selectivity),
        "delta_rows_pre_clamp": delta_rows,
        "rho_rows_pre_clamp": math.exp(delta_rows),
        "passes_by_tolerance": {
            str(tolerance): abs(delta_selectivity) <= tolerance
            for tolerance in RAW_RESIDUAL_TOLERANCES
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


def write_raw_selectivity_result_json(result: RawSelectivityResult, destination: Path) -> None:
    """Publish the four raw observations atomically without overwriting."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    configurations = []
    for item in result.observations:
        configurations.append(
            {
                "membership": list(item.membership),
                "activation_membership": list(item.activation_membership),
                "status": item.evaluation.diagnostics.status,
                "selectivity_pre_clamp": item.base_selectivity_event["selectivity"],
                "rows_pre_clamp": item.base_selectivity_event["rows_before_clamp"],
                "relation_tuples": item.base_selectivity_event["tuples"],
                "plan_rows_clamped": item.evaluation.formatted_estimates[RAW_QUERY_ID],
                "precise_plan_rows": item.evaluation.estimates[RAW_QUERY_ID],
                "trace": list(item.trace),
                "diagnostics": item.evaluation.diagnostics.as_dict(),
            }
        )
    payload = {
        "experiment_id": "foundations-raw-selectivity-000146-v1",
        "complete": result.complete,
        "input_binding": {
            "query_id": RAW_QUERY_ID,
            "query_sql": RAW_QUERY_SQL,
            "query_sql_sha256": RAW_QUERY_SQL_SHA256,
            "snapshot_semantic_digest": result.binding.expected_snapshot_digest,
            "candidate_universe_semantic_digest": result.binding.expected_candidate_universe_digest,
            "ground_truth_semantic_digest": result.binding.expected_ground_truth_digest,
            "sample_rows": result.binding.sample_rows,
            "statistics_target": result.binding.statistics_target,
            "global_precedence": list(result.binding.global_precedence),
            "candidates": [
                {
                    "candidate_id": RAW_CANDIDATE_C0,
                    "kind": "postgresql.mcv",
                    "columns": ["slope", "hillshade_noon"],
                },
                {
                    "candidate_id": RAW_CANDIDATE_C1,
                    "kind": "postgresql.mcv",
                    "columns": ["aspect", "hillshade_9am"],
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
