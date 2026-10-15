"""Controlled two-MCV, four-predicate separability experiment.

The diagnostic query is bound in memory from the sealed sample and is evaluated
through the existing Advisor sandbox, planner session, and black-box oracle.
It is deliberately not added to the frozen workload or CandidateUniverse
artifact on disk.
"""

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
from types import SimpleNamespace
from typing import Any

from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
)
from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .pins import verify_git_sha
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

DIAGNOSTIC_QUERY_ID = "foundations_four_predicate_two_mcv_v1"
DIAGNOSTIC_COLUMNS = (
    "aspect",
    "hillshade_3pm",
    "vertical_distance_to_hydrology",
    "hillshade_noon",
)
MCV_CANDIDATE_A = "cand_817be8bf7bbbc96c20bde230"
MCV_CANDIDATE_B = "cand_599a2efab190b6e2b7457ff1"
MCV_CANDIDATE_IDS = (MCV_CANDIDATE_A, MCV_CANDIDATE_B)
DIAGNOSTIC_MEMBERSHIPS = ((), (MCV_CANDIDATE_A,), (MCV_CANDIDATE_B,), MCV_CANDIDATE_IDS)
DEFAULT_SAMPLE_ROWS = 30_000
DEFAULT_STATISTICS_TARGET = 100
SINGLETON_LOG_TOLERANCE = 1e-12
RESIDUAL_TOLERANCES = (1e-12, 1e-9, 1e-6, 1e-3)


class FourPredicateInputError(ValueError):
    """Raised when the diagnostic experiment is not fully bound."""


class FourPredicateEvaluationError(RuntimeError):
    """Raised when the bounded live diagnostic cannot complete safely."""


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FourPredicateInputError(f"sample value is not numeric: {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise FourPredicateInputError(f"sample value is not finite: {value!r}")
    return result


def _nearest_rank(values: Sequence[Any], fraction: float) -> float:
    if not values:
        raise FourPredicateInputError("cannot derive bounds from an empty sample")
    ordered = sorted(_finite(value) for value in values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _sql_number(value: float) -> str:
    if not math.isfinite(value):
        raise FourPredicateInputError("SQL bound must be finite")
    if value == 0:
        return "0"
    rendered = format(value, ".17g")
    return rendered.removesuffix(".0")


def derive_four_predicate_bounds(sample: Any) -> dict[str, tuple[float, float]]:
    """Use inclusive nearest-rank Q1/Q3 bounds, with a declared degenerate fallback."""

    bounds: dict[str, tuple[float, float]] = {}
    for column in DIAGNOSTIC_COLUMNS:
        try:
            values = sample.column(column).to_pylist()
        except AttributeError as exc:
            raise FourPredicateInputError(f"sample does not expose column {column!r}") from exc
        low = _nearest_rank(values, 0.25)
        high = _nearest_rank(values, 0.75)
        if low > high:
            raise FourPredicateInputError(f"invalid quantile bounds for {column}")
        if low == high:
            ordered = sorted(_finite(value) for value in values)
            low, high = ordered[0], ordered[-1]
        bounds[column] = (low, high)
    return bounds


def build_four_predicate_sql(
    bounds: Mapping[str, tuple[float, float]],
    *,
    relation_schema: str = "public",
    relation_name: str = "forest10",
) -> str:
    """Render exactly four conjunctive BETWEEN predicates in fixed column order."""

    if tuple(bounds) != DIAGNOSTIC_COLUMNS:
        raise FourPredicateInputError("bounds must contain the four diagnostic columns in order")
    lines = [
        "SELECT *",
        f"FROM {relation_schema}.{relation_name}",
        "WHERE "
        + "\n  AND ".join(
            f"{column} BETWEEN {_sql_number(float(bounds[column][0]))}"
            f" AND {_sql_number(float(bounds[column][1]))}"
            for column in DIAGNOSTIC_COLUMNS
        )
        + ";",
    ]
    return "\n".join(lines)


def _validate_query_shape(sql: str) -> None:
    normalized = " ".join(sql.lower().split())
    if normalized.count(" between ") != 4 or normalized.count(" and ") != 7:
        raise FourPredicateInputError("diagnostic SQL must contain exactly four BETWEEN predicates")
    if any(token in normalized for token in (" join ", " or ", " not ")):
        raise FourPredicateInputError("diagnostic SQL contains a disallowed query construct")
    if not normalized.startswith("select * from public.forest10 where "):
        raise FourPredicateInputError("diagnostic SQL must target public.forest10")
    positions = [normalized.find(column) for column in DIAGNOSTIC_COLUMNS]
    if any(position < 0 for position in positions) or positions != sorted(positions):
        raise FourPredicateInputError("diagnostic SQL columns are not in the declared order")


@dataclass(frozen=True, slots=True)
class FourPredicateBinding:
    admin_dsn: str
    advisor_root: Path
    postgres_root: Path
    research_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    ground_truth_path: Path
    dataset_id: str
    relation_id: str
    relation_schema: str
    relation_name: str
    workload_id: str
    global_precedence: tuple[str, ...]
    expected_snapshot_digest: str
    expected_candidate_universe_digest: str
    expected_ground_truth_digest: str
    expected_advisor_sha: str
    expected_research_sha: str
    expected_postgres_sha: str
    query_sql: str
    query_sql_sha256: str
    bounds: Mapping[str, tuple[float, float]]
    statistics_target: int = DEFAULT_STATISTICS_TARGET
    sample_rows: int = DEFAULT_SAMPLE_ROWS
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations_mcv"

    def __post_init__(self) -> None:
        if not isinstance(self.admin_dsn, str) or not self.admin_dsn.strip():
            raise FourPredicateInputError("admin_dsn must be explicit")
        if self.query_sql_sha256 != _sha256_text(self.query_sql):
            raise FourPredicateInputError("diagnostic SQL digest does not match SQL")
        _validate_query_shape(self.query_sql)
        if tuple(self.bounds) != DIAGNOSTIC_COLUMNS:
            raise FourPredicateInputError("bounds must contain all diagnostic columns in order")
        if tuple(self.global_precedence).count(MCV_CANDIDATE_A) != 1:
            raise FourPredicateInputError("precedence must contain candidate A exactly once")
        if tuple(self.global_precedence).count(MCV_CANDIDATE_B) != 1:
            raise FourPredicateInputError("precedence must contain candidate B exactly once")
        if self.global_precedence.index(MCV_CANDIDATE_A) > self.global_precedence.index(
            MCV_CANDIDATE_B
        ):
            raise FourPredicateInputError("global precedence must place A before B")
        if self.sample_rows != DEFAULT_SAMPLE_ROWS:
            raise FourPredicateInputError("sample_rows must be the approved 30,000")
        if self.statistics_target != DEFAULT_STATISTICS_TARGET:
            raise FourPredicateInputError("statistics_target must be 100")


def make_binding_from_snapshot(
    *,
    sample: Any,
    admin_dsn: str,
    advisor_root: Path,
    postgres_root: Path,
    research_root: Path,
    snapshot_path: Path,
    candidate_universe_path: Path,
    ground_truth_path: Path,
    global_precedence: Sequence[str],
    expected_snapshot_digest: str,
    expected_candidate_universe_digest: str,
    expected_ground_truth_digest: str,
    expected_advisor_sha: str,
    expected_research_sha: str,
    expected_postgres_sha: str,
    dataset_id: str = "forest10",
    relation_id: str = "forest10",
    relation_schema: str = "public",
    relation_name: str = "forest10",
    workload_id: str = "arecel_forest10_test_v1",
) -> FourPredicateBinding:
    bounds = derive_four_predicate_bounds(sample)
    sql = build_four_predicate_sql(
        bounds, relation_schema=relation_schema, relation_name=relation_name
    )
    return FourPredicateBinding(
        admin_dsn=admin_dsn,
        advisor_root=Path(advisor_root),
        postgres_root=Path(postgres_root),
        research_root=Path(research_root),
        snapshot_path=Path(snapshot_path),
        candidate_universe_path=Path(candidate_universe_path),
        ground_truth_path=Path(ground_truth_path),
        dataset_id=dataset_id,
        relation_id=relation_id,
        relation_schema=relation_schema,
        relation_name=relation_name,
        workload_id=workload_id,
        global_precedence=tuple(global_precedence),
        expected_snapshot_digest=expected_snapshot_digest,
        expected_candidate_universe_digest=expected_candidate_universe_digest,
        expected_ground_truth_digest=expected_ground_truth_digest,
        expected_advisor_sha=expected_advisor_sha,
        expected_research_sha=expected_research_sha,
        expected_postgres_sha=expected_postgres_sha,
        query_sql=sql,
        query_sql_sha256=_sha256_text(sql),
        bounds=bounds,
    )


class _SnapshotQueryView:
    def __init__(self, base: Any, query: Any) -> None:
        from extstats_advisor.snapshot.model import Workload

        self._base = base
        self.workload = Workload(
            base.workload.workload_id,
            (*base.workload.queries, query),
            base.workload.provenance,
        )
        self.semantic_digest = base.semantic_digest

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


class _UniverseQueryView:
    def __init__(self, base: Any, profile: Any) -> None:
        self._base = base
        self.query_profiles = (*base.query_profiles, profile)
        self.semantic_digest = base.semantic_digest

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)


def _query_views(snapshot: Any, universe: Any, binding: FourPredicateBinding) -> tuple[Any, Any]:
    from extstats_advisor.snapshot.model import WorkloadQuery
    from extstats_advisor.workload.analysis import PredicateProfile

    if any(query.query_id == DIAGNOSTIC_QUERY_ID for query in snapshot.workload.queries):
        raise FourPredicateInputError("diagnostic query ID already exists in frozen workload")
    schema = snapshot.schemas[0]
    ordinal_by_name = {column.name: column.ordinal for column in schema.columns}
    try:
        ordinals = tuple(sorted(ordinal_by_name[column] for column in DIAGNOSTIC_COLUMNS))
    except KeyError as exc:
        raise FourPredicateInputError("diagnostic column is absent from snapshot schema") from exc
    query = WorkloadQuery(DIAGNOSTIC_QUERY_ID, binding.query_sql, 1.0)
    profile = PredicateProfile(
        DIAGNOSTIC_QUERY_ID,
        binding.relation_id,
        1.0,
        ordinals,
        tuple(sorted(DIAGNOSTIC_COLUMNS, key=ordinal_by_name.__getitem__)),
        "supported",
    )
    return _SnapshotQueryView(snapshot, query), _UniverseQueryView(universe, profile)


def _validate_bound_inputs(
    binding: FourPredicateBinding, services: FoundationsOracleServices
) -> tuple[Any, Any, Any]:
    snapshot = services.load_snapshot(Path(binding.snapshot_path))
    universe = services.load_candidate_universe(Path(binding.candidate_universe_path), snapshot)
    truth = services.load_ground_truth(Path(binding.ground_truth_path), snapshot)
    if snapshot.semantic_digest != binding.expected_snapshot_digest:
        raise FourPredicateInputError("snapshot digest mismatch")
    if universe.semantic_digest != binding.expected_candidate_universe_digest:
        raise FourPredicateInputError("candidate-universe digest mismatch")
    if getattr(truth, "semantic_digest", None) != binding.expected_ground_truth_digest:
        raise FourPredicateInputError("ground-truth digest mismatch")
    if snapshot.workload.workload_id != binding.workload_id:
        raise FourPredicateInputError("workload identity mismatch")
    if len(snapshot.schemas) != 1 or len(snapshot.samples) != 1:
        raise FourPredicateInputError("exactly one snapshot relation is required")
    schema = snapshot.schemas[0]
    if schema.relation_id != binding.relation_id:
        raise FourPredicateInputError("relation identity mismatch")
    if (
        schema.relation_name.schema != binding.relation_schema
        or schema.relation_name.name != binding.relation_name
    ):
        raise FourPredicateInputError("relation schema/name mismatch")
    sample = snapshot.samples.get(binding.relation_id)
    if sample is None or sample.num_rows != binding.sample_rows:
        raise FourPredicateInputError("approved sample row count mismatch")
    candidate_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if (
        set(binding.global_precedence) != set(candidate_by_id)
        or len(binding.global_precedence) != 57
    ):
        raise FourPredicateInputError("global precedence must cover exactly the 57 candidates")
    for candidate_id, names, rank in (
        (MCV_CANDIDATE_A, ("aspect", "hillshade_3pm"), 3),
        (MCV_CANDIDATE_B, ("vertical_distance_to_hydrology", "hillshade_noon"), 12),
    ):
        candidate = candidate_by_id.get(candidate_id)
        if candidate is None or candidate.kind != "postgresql.mcv":
            raise FourPredicateInputError(f"required MCV candidate is unavailable: {candidate_id}")
        if tuple(candidate.column_names) != names or candidate.static_precedence_rank != rank:
            raise FourPredicateInputError(f"candidate metadata mismatch: {candidate_id}")
    return snapshot, universe, truth


class _RawEstimateUtility:
    def __init__(self, query_id: str) -> None:
        self.query_id = query_id

    def evaluate(self, estimates: Mapping[str, float]) -> Any:
        value = estimates.get(self.query_id)
        if value is None:
            raise FourPredicateEvaluationError("diagnostic estimate is missing")
        return SimpleNamespace(objective=float(value))


@dataclass(frozen=True, slots=True)
class FourPredicateResult:
    binding: FourPredicateBinding
    database_name: str
    evaluations: tuple[OracleEvaluation, ...]
    sandbox_verification: Mapping[str, Any]
    cleanup_verified: bool

    @property
    def complete(self) -> bool:
        return (
            self.cleanup_verified
            and len(self.evaluations) == 4
            and all(evaluation.diagnostics.status == "complete" for evaluation in self.evaluations)
        )


def _database_exists(admin_dsn: str, database_name: str) -> bool:
    import psycopg

    with psycopg.connect(
        admin_dsn, autocommit=True, application_name="extstats-research-cleanup-check"
    ) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s", (database_name,)
            ).fetchone()
            is not None
        )


def run_four_predicate_experiment(
    binding: FourPredicateBinding,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> FourPredicateResult:
    """Execute exactly empty/A/B/AB through one owned sandbox and planner session."""

    verify_git_sha(binding.research_root, binding.expected_research_sha)
    verify_git_sha(binding.postgres_root, binding.expected_postgres_sha)
    services = services or _default_services(binding.advisor_root, binding.expected_advisor_sha)
    snapshot, universe, _truth = _validate_bound_inputs(binding, services)
    planner_snapshot, planner_universe = _query_views(snapshot, universe, binding)
    spec = ExperimentDatabaseSpec(
        admin_dsn=binding.admin_dsn,
        name_prefix=binding.name_prefix,
        sample_rows=binding.sample_rows,
    )
    lifecycle = lifecycle_factory(spec)
    memberships = DIAGNOSTIC_MEMBERSHIPS
    result: FourPredicateResult | None = None
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
            required = set(MCV_CANDIDATE_IDS)
            present = {
                item.candidate_id for item in repository.candidate_models if item.state == "present"
            }
            if not required.issubset(present):
                raise FourPredicateInputError("required MCV candidate payload is not PRESENT")
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
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
            )
            session = services.planner_session(
                database.database_dsn,
                planner_snapshot,
                planner_universe,
                repository,
                database.database_name,
            )
            session.open()
            oracle = BlackBoxMembershipOracle(
                session=session,
                snapshot=planner_snapshot,
                candidate_universe=planner_universe,
                global_precedence=binding.global_precedence,
                utility_provider=_RawEstimateUtility(DIAGNOSTIC_QUERY_ID),
                native_repository=repository,
                query_ids=(DIAGNOSTIC_QUERY_ID,),
                expected_snapshot_digest=binding.expected_snapshot_digest,
                expected_candidate_universe_digest=binding.expected_candidate_universe_digest,
            )
            evaluations = tuple(oracle.evaluate_precise(membership) for membership in memberships)
            result = FourPredicateResult(
                binding,
                database.database_name,
                evaluations,
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
        raise FourPredicateEvaluationError("four-predicate run returned no result")
    cleanup_verified = not _database_exists(binding.admin_dsn, result.database_name)
    if not cleanup_verified:
        raise FourPredicateEvaluationError(
            f"owned experiment database remains after lifecycle cleanup: {result.database_name}"
        )
    return FourPredicateResult(
        result.binding,
        result.database_name,
        result.evaluations,
        result.sandbox_verification,
        cleanup_verified,
    )


def _positive_raw(values: Mapping[str, Any]) -> dict[str, float] | None:
    try:
        result = {key: float(value) for key, value in values.items()}
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value <= 0 for value in result.values()):
        return None
    return result


def rounding_intervals(plan_rows: Mapping[str, int]) -> dict[str, tuple[float, float]]:
    intervals = {}
    for key, value in plan_rows.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("formatted Plan Rows must be non-negative integers")
        intervals[key] = (max(0.0, value - 0.5), value + 0.5)
    return intervals


def rounding_compatible_with_multiplicativity(plan_rows: Mapping[str, int]) -> bool:
    """Check whether nearest-integer intervals contain a positive exact product solution."""

    required = ("empty", "a", "b", "ab")
    if tuple(plan_rows) != required:
        raise ValueError("plan_rows must be ordered empty, a, b, ab")
    intervals = rounding_intervals(plan_rows)
    lower = max(intervals["empty"][0], 1e-300)
    upper = intervals["empty"][1]
    predicted_low = intervals["a"][0] * intervals["b"][0] / upper
    predicted_high = intervals["a"][1] * intervals["b"][1] / lower
    return predicted_high >= intervals["ab"][0] and predicted_low <= intervals["ab"][1]


def classify_observation(
    estimates: Mapping[str, Any],
    *,
    singleton_log_tolerance: float = SINGLETON_LOG_TOLERANCE,
) -> dict[str, Any]:
    raw = _positive_raw(estimates)
    if raw is None:
        return {
            "category": "invalid",
            "valid": False,
            "invalid_reason": "raw estimates must be finite and positive",
        }
    delta = math.log(raw["ab"]) - math.log(raw["a"]) - math.log(raw["b"]) + math.log(raw["empty"])
    rho = math.exp(delta)
    a_effect = abs(math.log(raw["a"] / raw["empty"]))
    b_effect = abs(math.log(raw["b"] / raw["empty"]))
    a_changed = a_effect > singleton_log_tolerance
    b_changed = b_effect > singleton_log_tolerance
    category = (
        "two-sided-informative"
        if a_changed and b_changed
        else ("one-sided" if a_changed or b_changed else "non-informative")
    )
    return {
        "category": category,
        "valid": True,
        "delta": delta,
        "rho": rho,
        "absolute_log_residual": abs(delta),
        "relative_deviation": abs(math.expm1(delta)),
        "singleton_log_effects": {"a": a_effect, "b": b_effect},
        "passes_by_tolerance": {
            str(tolerance): abs(delta) <= tolerance for tolerance in RESIDUAL_TOLERANCES
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


def _evaluation_payload(evaluation: OracleEvaluation) -> dict[str, Any]:
    estimate = (
        None if evaluation.estimates is None else evaluation.estimates.get(DIAGNOSTIC_QUERY_ID)
    )
    formatted = (
        None
        if evaluation.formatted_estimates is None
        else evaluation.formatted_estimates.get(DIAGNOSTIC_QUERY_ID)
    )
    return {
        "membership": list(evaluation.diagnostics.membership),
        "status": evaluation.diagnostics.status,
        "precise_estimate": estimate,
        "formatted_plan_rows": formatted,
        "objective": evaluation.objective,
        "diagnostics": evaluation.diagnostics.as_dict(),
    }


def write_four_predicate_result_json(result: FourPredicateResult, destination: Path) -> None:
    """Publish one final raw-observation artifact atomically without overwrite."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    evaluations = [_evaluation_payload(item) for item in result.evaluations]
    by_membership = {tuple(item["membership"]): item for item in evaluations}
    estimates = {
        label: by_membership[membership]["precise_estimate"]
        for label, membership in (
            ("empty", ()),
            ("a", (MCV_CANDIDATE_A,)),
            ("b", (MCV_CANDIDATE_B,)),
            ("ab", MCV_CANDIDATE_IDS),
        )
    }
    diagnostic = classify_observation(estimates)
    formatted = {
        label: by_membership[membership]["formatted_plan_rows"]
        for label, membership in (
            ("empty", ()),
            ("a", (MCV_CANDIDATE_A,)),
            ("b", (MCV_CANDIDATE_B,)),
            ("ab", MCV_CANDIDATE_IDS),
        )
    }
    payload = {
        "experiment_id": "foundations-four-predicate-two-mcv-v1",
        "complete": result.complete,
        "input_binding": {
            "dataset_id": result.binding.dataset_id,
            "relation_id": result.binding.relation_id,
            "relation_schema": result.binding.relation_schema,
            "relation_name": result.binding.relation_name,
            "workload_id": result.binding.workload_id,
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
        "diagnostic_query": {
            "query_id": DIAGNOSTIC_QUERY_ID,
            "sql": result.binding.query_sql,
            "sql_sha256": result.binding.query_sql_sha256,
            "bound_method": "inclusive nearest-rank Q1/Q3; min/max fallback only for equal quartiles",
            "bounds": {key: list(value) for key, value in result.binding.bounds.items()},
        },
        "runtime": {
            "database_name": result.database_name,
            "sandbox_verification": dict(result.sandbox_verification),
            "cleanup_verified_by_database_catalog_check": result.cleanup_verified,
        },
        "configurations": evaluations,
        "raw_estimates": estimates,
        "formatted_plan_rows": formatted,
        "diagnostic": {
            **diagnostic,
            "rounding_compatible": rounding_compatible_with_multiplicativity(formatted),
            "rounding_intervals": {
                key: list(value) for key, value in rounding_intervals(formatted).items()
            },
        },
    }
    encoded = json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False)
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
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
