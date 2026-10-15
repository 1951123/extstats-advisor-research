"""Homogeneous pre-clamp measurements and exact 12-MCV re-enumeration."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
)
from .foundations_oracle import BlackBoxMembershipOracle
from .foundations_pessimistic_enumeration import enumerate_maximal_matchings
from .foundations_raw_selectivity import (
    _finite_positive,
    _parse_trace,
    _RawEstimateUtility,
    _target_base_event,
)
from .pins import verify_git_sha
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

RAW_MCV_CANDIDATES = (
    "cand_1537a6ae8ef67c379c8d0fa0",
    "cand_c2737dfb5bfbb3e415dfb793",
    "cand_817be8bf7bbbc96c20bde230",
    "cand_4de3778bb8674287b6dd5b29",
    "cand_cbf4af3acd6feaa65760c6d1",
    "cand_76679921292e8c769dbbd7bc",
    "cand_0deae0d5de82a5a1fdc34111",
    "cand_77c3e92fff291d4d9515dc69",
    "cand_5ba9cc937a696a76876f820d",
    "cand_bafa53ec095d804233d42826",
    "cand_01933ab615fb49e2f81b5b92",
    "cand_599a2efab190b6e2b7457ff1",
)
RAW_MCV_QUERY_SUFFIXES = (
    0,
    1,
    2,
    3,
    4,
    5,
    6,
    7,
    8,
    9,
    10,
    11,
    12,
    13,
    14,
    15,
    17,
    19,
    21,
    22,
    23,
    30,
    43,
    51,
    61,
    67,
    81,
    111,
    139,
    146,
    169,
    197,
)
RAW_MCV_QUERY_IDS = tuple(f"arecel_forest10_test_{suffix:06d}" for suffix in RAW_MCV_QUERY_SUFFIXES)
RAW_MCV_ARTIFACT_ID = "foundations-pessimistic-12mcv-raw-unit-v1"
ENUMERATION_TOLERANCE = 1e-12


class RawMcvMatrixError(ValueError):
    """Raised when raw-matrix binding, measurement, or reconstruction is invalid."""


@dataclass(frozen=True, slots=True)
class RawMcvMatrixBinding:
    admin_dsn: str
    advisor_root: Path
    postgres_root: Path
    research_root: Path
    snapshot_path: Path
    candidate_universe_path: Path
    ground_truth_path: Path
    historical_optimization_path: Path
    historical_permutation_path: Path
    earlier_raw_path: Path
    global_precedence: tuple[str, ...]
    expected_snapshot_digest: str
    expected_candidate_universe_digest: str
    expected_ground_truth_digest: str
    expected_advisor_sha: str
    expected_research_sha: str
    expected_postgres_sha: str
    historical_optimization_sha256: str
    historical_permutation_sha256: str
    earlier_raw_sha256: str
    statistics_target: int = 100
    sample_rows: int = 30_000
    native_batch_size: int = 256
    name_prefix: str = "exp_foundations_raw_mcv_matrix"

    def __post_init__(self) -> None:
        if len(self.global_precedence) != 57 or len(set(self.global_precedence)) != 57:
            raise RawMcvMatrixError("global precedence must contain 57 unique candidates")
        if set(RAW_MCV_CANDIDATES) - set(self.global_precedence):
            raise RawMcvMatrixError("global precedence is missing an MCV candidate")
        if self.statistics_target != 100 or self.sample_rows != 30_000:
            raise RawMcvMatrixError("raw MCV matrix requires target 100 and 30,000 rows")


@dataclass(frozen=True, slots=True)
class RawMcvObservation:
    membership: tuple[str, ...]
    activation_membership: tuple[str, ...]
    query_id: str
    selectivity_pre_clamp: float
    rows_pre_clamp: float
    relation_tuples: float
    plan_rows_clamped: int
    precise_plan_rows: float
    trace: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class RawMcvMatrixResult:
    binding: RawMcvMatrixBinding
    database_name: str
    relation_oid: int
    observations: tuple[RawMcvObservation, ...]
    sandbox_verification: Mapping[str, Any]
    cleanup_verified: bool

    @property
    def complete(self) -> bool:
        return self.cleanup_verified and len(self.observations) == 13 * 32


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("nonfinite value cannot be serialized")
    return value


def _load_historical_inputs(binding: RawMcvMatrixBinding) -> tuple[dict[str, Any], dict[str, Any]]:
    for path, expected in (
        (binding.historical_optimization_path, binding.historical_optimization_sha256),
        (binding.historical_permutation_path, binding.historical_permutation_sha256),
        (binding.earlier_raw_path, binding.earlier_raw_sha256),
    ):
        if _sha256(path) != expected:
            raise RawMcvMatrixError(f"historical artifact hash mismatch: {path}")
    optimization = json.loads(
        Path(binding.historical_optimization_path).read_text(encoding="utf-8")
    )
    permutation = json.loads(Path(binding.historical_permutation_path).read_text(encoding="utf-8"))
    earlier_raw = json.loads(Path(binding.earlier_raw_path).read_text(encoding="utf-8"))
    if optimization.get("complete") is not True or permutation.get("complete") is not True:
        raise RawMcvMatrixError("historical optimization/permutation artifact is incomplete")
    if earlier_raw.get("complete") is not True:
        raise RawMcvMatrixError("earlier raw diagnostic artifact is incomplete")
    model = optimization.get("model", {})
    if tuple(model.get("candidate_ids", ())) != RAW_MCV_CANDIDATES:
        raise RawMcvMatrixError("historical model does not bind the original 12 MCV candidates")
    if tuple(model.get("query_ids", ())) != RAW_MCV_QUERY_IDS:
        raise RawMcvMatrixError("historical model does not bind the fixed 32-query workload")
    if set(model.get("applicable_mcv_candidates_by_query", {})) != set(RAW_MCV_QUERY_IDS):
        raise RawMcvMatrixError("historical applicability metadata is incomplete")
    return optimization, permutation


def _validate_inputs(
    binding: RawMcvMatrixBinding,
    services: FoundationsOracleServices,
) -> tuple[Any, Any, Any, dict[str, Any], dict[str, Any]]:
    optimization, permutation = _load_historical_inputs(binding)
    snapshot = services.load_snapshot(Path(binding.snapshot_path))
    universe = services.load_candidate_universe(Path(binding.candidate_universe_path), snapshot)
    truth = services.load_ground_truth(Path(binding.ground_truth_path), snapshot)
    if snapshot.semantic_digest != binding.expected_snapshot_digest:
        raise RawMcvMatrixError("snapshot digest mismatch")
    if universe.semantic_digest != binding.expected_candidate_universe_digest:
        raise RawMcvMatrixError("candidate-universe digest mismatch")
    if truth.semantic_digest != binding.expected_ground_truth_digest:
        raise RawMcvMatrixError("ground-truth digest mismatch")
    if snapshot.workload.workload_id != "arecel_forest10_test_v1":
        raise RawMcvMatrixError("workload identity mismatch")
    query_map = {query.query_id: query for query in snapshot.workload.queries}
    if not set(RAW_MCV_QUERY_IDS).issubset(query_map):
        raise RawMcvMatrixError("snapshot workload is missing a fixed 32-query member")
    selected_in_snapshot_order = tuple(
        query.query_id
        for query in snapshot.workload.queries
        if query.query_id in set(RAW_MCV_QUERY_IDS)
    )
    if selected_in_snapshot_order != RAW_MCV_QUERY_IDS:
        raise RawMcvMatrixError("fixed 32 queries are not in the frozen evaluation order")
    candidate_map = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if set(binding.global_precedence) != set(candidate_map):
        raise RawMcvMatrixError("global precedence does not cover CandidateUniverse exactly")
    expected_order = tuple(
        candidate.candidate_id
        for candidate in sorted(universe.candidates, key=lambda item: item.static_precedence_rank)
    )
    if tuple(binding.global_precedence) != expected_order:
        raise RawMcvMatrixError("global precedence differs from frozen static precedence")
    historical_candidates = {
        item["candidate_id"]: item for item in optimization["candidate_metadata"]
    }
    for candidate_id in RAW_MCV_CANDIDATES:
        actual = candidate_map.get(candidate_id)
        expected = historical_candidates.get(candidate_id)
        if actual is None or expected is None:
            raise RawMcvMatrixError(f"missing MCV candidate metadata: {candidate_id}")
        if actual.kind != "postgresql.mcv" or tuple(actual.column_names) != tuple(
            expected["column_names"]
        ):
            raise RawMcvMatrixError(f"candidate metadata mismatch: {candidate_id}")
    return snapshot, universe, truth, optimization, permutation


def _ordered_membership(membership: Sequence[str], precedence: Sequence[str]) -> tuple[str, ...]:
    selected = set(membership)
    return tuple(candidate_id for candidate_id in precedence if candidate_id in selected)


def _matrix_memberships() -> tuple[tuple[str, ...], ...]:
    return ((),) + tuple((candidate_id,) for candidate_id in RAW_MCV_CANDIDATES)


def run_raw_mcv_matrix(
    binding: RawMcvMatrixBinding,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> RawMcvMatrixResult:
    """Measure exactly 13 configurations × 32 fixed workload queries."""

    verify_git_sha(binding.research_root, binding.expected_research_sha)
    verify_git_sha(binding.postgres_root, binding.expected_postgres_sha)
    services = services or _default_services(binding.advisor_root, binding.expected_advisor_sha)
    snapshot, universe, _truth, _optimization, _permutation = _validate_inputs(binding, services)
    lifecycle = lifecycle_factory(
        ExperimentDatabaseSpec(
            admin_dsn=binding.admin_dsn,
            name_prefix=binding.name_prefix,
            sample_rows=binding.sample_rows,
        )
    )
    result: RawMcvMatrixResult | None = None
    with lifecycle as database:
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
            candidate_models = {item.candidate_id: item for item in repository.candidate_models}
            for candidate_id in RAW_MCV_CANDIDATES:
                candidate = candidate_models.get(candidate_id)
                payload = repository.payloads.get(candidate_id)
                if (
                    candidate is None
                    or candidate.state != "present"
                    or not isinstance(payload, bytes)
                    or not payload
                    or hashlib.sha256(payload).hexdigest() != candidate.payload_sha256
                ):
                    raise RawMcvMatrixError(f"native payload validation failed: {candidate_id}")
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
            memberships = _matrix_memberships()
            observations: list[RawMcvObservation] = []
            for query_id in RAW_MCV_QUERY_IDS:
                oracle = BlackBoxMembershipOracle(
                    session=session,
                    snapshot=snapshot,
                    candidate_universe=universe,
                    global_precedence=binding.global_precedence,
                    utility_provider=_RawEstimateUtility(query_id),
                    native_repository=repository,
                    query_ids=(query_id,),
                    expected_snapshot_digest=binding.expected_snapshot_digest,
                    expected_candidate_universe_digest=binding.expected_candidate_universe_digest,
                )
                oid_to_candidate = {
                    oid: candidate_id for candidate_id, oid in session.registered_oids.items()
                }
                for membership in memberships:
                    ordered = _ordered_membership(membership, binding.global_precedence)
                    notices.clear()
                    evaluation = oracle.evaluate_precise(ordered)
                    if evaluation.diagnostics.status != "complete":
                        raise RawMcvMatrixError(
                            f"Planner evaluation failed for {membership}/{query_id}: "
                            f"{evaluation.diagnostics.error}"
                        )
                    final_rows = int(evaluation.formatted_estimates[query_id])
                    trace: list[Mapping[str, Any]] = []
                    for event in notices:
                        copied = dict(event)
                        if copied.get("event") == "mcv_application":
                            copied["candidate_id"] = oid_to_candidate.get(copied.get("stat_oid"))
                        trace.append(copied)
                    base_event = _target_base_event(trace, relation_oid, final_rows)
                    observations.append(
                        RawMcvObservation(
                            tuple(membership),
                            ordered,
                            query_id,
                            _finite_positive(
                                base_event.get("selectivity"), "pre-clamp selectivity"
                            ),
                            _finite_positive(base_event.get("rows_before_clamp"), "pre-clamp rows"),
                            _finite_positive(base_event.get("tuples"), "relation tuples"),
                            final_rows,
                            _finite_positive(evaluation.estimates[query_id], "precise Plan Rows"),
                            tuple(trace),
                        )
                    )
                del oracle
            if len(observations) != 13 * 32:
                raise RawMcvMatrixError("measurement grid is incomplete")
            result = RawMcvMatrixResult(
                binding,
                database.database_name,
                relation_oid,
                tuple(observations),
                verification,
                False,
            )
            session.close()
            session = None
            services.destroy_sandbox(database.database_dsn)
            sandbox_prepared = False
        except BaseException as exc:  # noqa: BLE001 - preserve primary live failure
            primary = exc
        finally:
            if session is not None:
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
        raise RawMcvMatrixError("raw MCV matrix produced no result")
    import psycopg

    with psycopg.connect(
        binding.admin_dsn, autocommit=True, application_name="foundations-raw-mcv-cleanup"
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
            (result.database_name,),
        ).fetchone()
    if exists is not None:
        raise RawMcvMatrixError(f"owned database remains: {result.database_name}")
    return RawMcvMatrixResult(
        result.binding,
        result.database_name,
        result.relation_oid,
        result.observations,
        result.sandbox_verification,
        True,
    )


def _observations_by_key(
    result: RawMcvMatrixResult,
) -> dict[tuple[frozenset[str], str], RawMcvObservation]:
    value = {(frozenset(item.membership), item.query_id): item for item in result.observations}
    if len(value) != len(result.observations) or len(value) != 13 * 32:
        raise RawMcvMatrixError("duplicate or incomplete raw observation matrix")
    return value


def _postgres_clamp_row_estimate(value: float) -> float:
    """Match pinned PostgreSQL clamp_row_est for the finite observed regime."""

    if not math.isfinite(value) or value <= 0:
        raise RawMcvMatrixError("model rows must be finite and positive")
    if value <= 1.0:
        return 1.0
    # PostgreSQL calls libc rint: nearest integer, ties to even under the
    # laboratory's default rounding mode. Python round has the same rule.
    return float(round(value))


def _loss(value: float, truth: float, *, plan: bool) -> float:
    target = max(1.0, float(truth))
    predicted = _postgres_clamp_row_estimate(value) if plan else value
    return abs(math.log(predicted) - math.log(target))


def _local_tables(
    *,
    candidate_columns: Mapping[str, Sequence[str]],
    query_ids: Sequence[str],
    applicable: Mapping[str, Sequence[str]],
    base_rows: Mapping[str, float],
    corrections: Mapping[str, Mapping[str, float]],
    truths: Mapping[str, float],
    plan: bool,
) -> tuple[dict[str, dict[tuple[str, ...], dict[str, Any]]], int, int, int]:
    tables: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}
    subset_count = matching_count = maximum_size = 0
    for query_id in query_ids:
        local_ids = tuple(sorted(applicable[query_id]))
        table: dict[tuple[str, ...], dict[str, Any]] = {}
        for mask in range(1 << len(local_ids)):
            subset = tuple(item for index, item in enumerate(local_ids) if mask & (1 << index))
            matchings = enumerate_maximal_matchings(subset, candidate_columns)
            losses = []
            for matching in matchings:
                raw_prediction = base_rows[query_id] * math.exp(
                    sum(corrections[candidate_id][query_id] for candidate_id in matching)
                )
                losses.append(_loss(raw_prediction, truths[query_id], plan=plan))
            worst = max(losses)
            table[subset] = {
                "loss": worst,
                "worst_matchings": [
                    list(matching)
                    for matching, loss in zip(matchings, losses, strict=True)
                    if abs(loss - worst) <= 0.0
                ],
                "maximal_matchings": [list(matching) for matching in matchings],
            }
            subset_count += 1
            matching_count += len(matchings)
        tables[query_id] = table
        maximum_size = max(maximum_size, len(table))
    return tables, subset_count, matching_count, maximum_size


def _enumerate_budgets(
    *,
    candidate_ids: Sequence[str],
    candidate_columns: Mapping[str, Sequence[str]],
    query_ids: Sequence[str],
    query_weights: Mapping[str, float],
    applicable: Mapping[str, Sequence[str]],
    tables: Mapping[str, Mapping[tuple[str, ...], Mapping[str, Any]]],
    budgets: Sequence[int],
    convention: str,
    candidate_costs: Mapping[str, float],
    max_tied_sets: int = 10_000,
) -> dict[str, Any]:
    started = time.perf_counter_ns()
    ids = tuple(sorted(candidate_ids))
    all_subsets = []
    for mask in range(1 << len(ids)):
        membership = tuple(item for index, item in enumerate(ids) if mask & (1 << index))
        cost = sum(candidate_costs[item] for item in membership)
        all_subsets.append((membership, cost))
    budget_results = []
    for budget in budgets:
        feasible = [item for item in all_subsets if item[1] <= budget]
        evaluated = []
        for membership, cost in feasible:
            selected = set(membership)
            entries = {}
            objective = 0.0
            for query_id in query_ids:
                local = tuple(sorted(selected & set(applicable[query_id])))
                entry = tables[query_id][local]
                entries[query_id] = entry
                objective += query_weights[query_id] * float(entry["loss"])
            evaluated.append((membership, cost, objective, entries))
        best = min(item[2] for item in evaluated)
        ties = [item for item in evaluated if abs(item[2] - best) <= ENUMERATION_TOLERANCE]
        representative = ties[0]
        budget_results.append(
            {
                "budget": budget,
                "objective": best,
                "representative_membership": list(representative[0]),
                "representative_cardinality": len(representative[0]),
                "representative_cost": representative[1],
                "optimal_set_count": len(ties),
                "optimal_memberships": (
                    [list(item[0]) for item in ties] if len(ties) <= max_tied_sets else None
                ),
                "overlap": {
                    "has_overlapping_candidate_pair": any(
                        set(candidate_columns[left]) & set(candidate_columns[right])
                        for index, left in enumerate(representative[0])
                        for right in representative[0][index + 1 :]
                    ),
                    "query_matching_ambiguity": {
                        query_id: len(representative[3][query_id]["maximal_matchings"])
                        for query_id in query_ids
                        if len(representative[3][query_id]["maximal_matchings"]) > 1
                    },
                },
                "per_query": {
                    query_id: {
                        "loss": representative[3][query_id]["loss"],
                        "worst_matchings": representative[3][query_id]["worst_matchings"],
                    }
                    for query_id in query_ids
                },
                "budget_feasible_subsets": len(feasible),
            }
        )
    return {
        "convention": convention,
        "budgets": budget_results,
        "enumeration_statistics": {
            "global_subsets_checked": 1 << len(ids),
            "maximum_budget_feasible_subsets": max(
                item["budget_feasible_subsets"] for item in budget_results
            ),
            "elapsed_ns": time.perf_counter_ns() - started,
        },
    }


def build_analysis_payload(
    result: RawMcvMatrixResult,
    *,
    optimization_artifact: Mapping[str, Any],
    permutation_artifact: Mapping[str, Any],
    truth_by_query: Mapping[str, float],
) -> dict[str, Any]:
    """Build raw coefficients, both loss conventions, and fixed-set comparisons."""

    observations = _observations_by_key(result)
    query_ids = RAW_MCV_QUERY_IDS
    candidate_ids = RAW_MCV_CANDIDATES
    model = optimization_artifact["model"]
    weights = {query_id: float(model["query_weights"][query_id]) for query_id in query_ids}
    applicable = {
        query_id: tuple(model["applicable_mcv_candidates_by_query"][query_id])
        for query_id in query_ids
    }
    candidate_columns = {
        item["candidate_id"]: tuple(item["column_names"])
        for item in optimization_artifact["candidate_metadata"]
    }
    base_rows = {
        query_id: observations[(frozenset(), query_id)].rows_pre_clamp for query_id in query_ids
    }
    base_selectivity = {
        query_id: observations[(frozenset(), query_id)].selectivity_pre_clamp
        for query_id in query_ids
    }
    corrections = {
        candidate_id: {
            query_id: (
                math.log(observations[(frozenset((candidate_id,)), query_id)].rows_pre_clamp)
                - math.log(base_rows[query_id])
            )
            for query_id in query_ids
        }
        for candidate_id in candidate_ids
    }
    selectivity_corrections = {
        candidate_id: {
            query_id: (
                math.log(observations[(frozenset((candidate_id,)), query_id)].selectivity_pre_clamp)
                - math.log(base_selectivity[query_id])
            )
            for query_id in query_ids
        }
        for candidate_id in candidate_ids
    }
    ineligible_changes = []
    for query_id in query_ids:
        for candidate_id in candidate_ids:
            if (
                candidate_id not in applicable[query_id]
                and abs(corrections[candidate_id][query_id]) > 1e-12
            ):
                ineligible_changes.append(
                    {
                        "query_id": query_id,
                        "candidate_id": candidate_id,
                        "raw_log_correction": corrections[candidate_id][query_id],
                    }
                )
    if ineligible_changes:
        raise RawMcvMatrixError(
            "ineligible singleton produced a nonzero raw correction: "
            + repr(ineligible_changes[:3])
        )
    budgets = tuple(range(len(candidate_ids) + 1))
    candidate_costs = {candidate_id: 1.0 for candidate_id in candidate_ids}
    raw_tables, raw_subsets, raw_matchings, raw_max = _local_tables(
        candidate_columns=candidate_columns,
        query_ids=query_ids,
        applicable=applicable,
        base_rows=base_rows,
        corrections=corrections,
        truths=truth_by_query,
        plan=False,
    )
    plan_tables, plan_subsets, plan_matchings, plan_max = _local_tables(
        candidate_columns=candidate_columns,
        query_ids=query_ids,
        applicable=applicable,
        base_rows=base_rows,
        corrections=corrections,
        truths=truth_by_query,
        plan=True,
    )
    raw_enum = _enumerate_budgets(
        candidate_ids=candidate_ids,
        candidate_columns=candidate_columns,
        query_ids=query_ids,
        query_weights=weights,
        applicable=applicable,
        tables=raw_tables,
        budgets=budgets,
        convention="raw_pre_clamp_absolute_log",
        candidate_costs=candidate_costs,
    )
    plan_enum = _enumerate_budgets(
        candidate_ids=candidate_ids,
        candidate_columns=candidate_columns,
        query_ids=query_ids,
        query_weights=weights,
        applicable=applicable,
        tables=plan_tables,
        budgets=budgets,
        convention="postgresql_plan_rows_absolute_log",
        candidate_costs=candidate_costs,
    )
    old_membership = tuple(
        optimization_artifact["enumeration"]["budgets"][4]["representative_membership"]
    )
    old_local = {}
    old_raw = old_uniform = old_plan = old_plan_uniform = 0.0
    for query_id in query_ids:
        local = tuple(sorted(set(old_membership) & set(applicable[query_id])))
        matchings = raw_tables[query_id][local]["maximal_matchings"]
        raw_losses = []
        plan_losses = []
        raw_predictions = []
        plan_predictions = []
        for matching in matchings:
            prediction = base_rows[query_id] * math.exp(
                sum(corrections[candidate_id][query_id] for candidate_id in matching)
            )
            raw_predictions.append(prediction)
            plan_predictions.append(_postgres_clamp_row_estimate(prediction))
            raw_losses.append(_loss(prediction, truth_by_query[query_id], plan=False))
            plan_losses.append(_loss(prediction, truth_by_query[query_id], plan=True))
        old_raw += weights[query_id] * max(raw_losses)
        old_uniform += weights[query_id] * (sum(raw_losses) / len(raw_losses))
        old_plan += weights[query_id] * max(plan_losses)
        old_plan_uniform += weights[query_id] * (sum(plan_losses) / len(plan_losses))
        observed = []
        for permutation in permutation_artifact["permutations"]:
            item = permutation["estimates"][query_id]
            observed.append(
                {
                    "permutation_id": permutation["permutation_id"],
                    "precise": item["precise"],
                    "plan_rows": item["formatted"],
                    "loss": permutation["per_query_losses"][query_id],
                }
            )
        old_local[query_id] = {
            "applicable_membership": list(local),
            "maximal_matchings": matchings,
            "raw_predictions": raw_predictions,
            "plan_predictions": plan_predictions,
            "raw_losses": raw_losses,
            "plan_losses": plan_losses,
            "raw_pessimistic_loss": max(raw_losses),
            "raw_uniform_loss": sum(raw_losses) / len(raw_losses),
            "plan_pessimistic_loss": max(plan_losses),
            "plan_uniform_loss": sum(plan_losses) / len(plan_losses),
            "observed_permutations": observed,
            "observed_plan_rows": sorted({item["plan_rows"] for item in observed}),
            "observed_mean_loss": sum(item["loss"] for item in observed) / len(observed),
            "observed_min_loss": min(item["loss"] for item in observed),
            "observed_max_loss": max(item["loss"] for item in observed),
        }
    earlier = json.loads(Path(result.binding.earlier_raw_path).read_text(encoding="utf-8"))
    earlier_by_membership = {
        frozenset(item["membership"]): item for item in earlier["configurations"]
    }
    diagnostic_query = "arecel_forest10_test_000146"
    predicted_146 = base_rows[diagnostic_query] * math.exp(
        corrections["cand_0deae0d5de82a5a1fdc34111"][diagnostic_query]
        + corrections["cand_1537a6ae8ef67c379c8d0fa0"][diagnostic_query]
    )
    return {
        "experiment_id": RAW_MCV_ARTIFACT_ID,
        "complete": result.complete,
        "input_binding": {
            "snapshot_semantic_digest": result.binding.expected_snapshot_digest,
            "candidate_universe_semantic_digest": result.binding.expected_candidate_universe_digest,
            "ground_truth_semantic_digest": result.binding.expected_ground_truth_digest,
            "sample_rows": result.binding.sample_rows,
            "statistics_target": result.binding.statistics_target,
            "query_ids": list(query_ids),
            "query_sql": {
                query_id: next(
                    query["sql"]
                    for query in optimization_artifact.get("queries", [])
                    if query.get("query_id") == query_id
                )
                if optimization_artifact.get("queries")
                else None
                for query_id in query_ids
            },
            "workload_query_sql_digest": optimization_artifact["experiment_inputs"][
                "query_sql_digest"
            ],
            "global_precedence": list(result.binding.global_precedence),
            "candidate_ids": list(candidate_ids),
        },
        "source_artifacts": {
            "historical_optimization_path": str(result.binding.historical_optimization_path),
            "historical_optimization_sha256": result.binding.historical_optimization_sha256,
            "historical_permutation_path": str(result.binding.historical_permutation_path),
            "historical_permutation_sha256": result.binding.historical_permutation_sha256,
            "earlier_raw_path": str(result.binding.earlier_raw_path),
            "earlier_raw_sha256": result.binding.earlier_raw_sha256,
        },
        "source_commits": {
            "research": result.binding.expected_research_sha,
            "advisor": result.binding.expected_advisor_sha,
            "postgres": result.binding.expected_postgres_sha,
        },
        "candidate_metadata": optimization_artifact["candidate_metadata"],
        "query_weights": weights,
        "truths": {query_id: truth_by_query[query_id] for query_id in query_ids},
        "applicable_mcv_candidates_by_query": {
            query_id: list(applicable[query_id]) for query_id in query_ids
        },
        "raw_coefficients": {
            "base_rows_pre_clamp": base_rows,
            "base_selectivity_pre_clamp": base_selectivity,
            "alpha": {
                query_id: math.log(base_rows[query_id])
                - math.log(max(1.0, truth_by_query[query_id]))
                for query_id in query_ids
            },
            "b_rows": corrections,
            "b_selectivity": selectivity_corrections,
            "ineligible_singleton_changes": ineligible_changes,
        },
        "observations": [
            {
                "membership": list(item.membership),
                "activation_membership": list(item.activation_membership),
                "query_id": item.query_id,
                "selectivity_pre_clamp": item.selectivity_pre_clamp,
                "rows_pre_clamp": item.rows_pre_clamp,
                "relation_tuples": item.relation_tuples,
                "plan_rows_clamped": item.plan_rows_clamped,
                "precise_plan_rows": item.precise_plan_rows,
                "trace": list(item.trace),
            }
            for item in result.observations
        ],
        "focused_000146": {
            "historical_four_configuration_observations": earlier_by_membership,
            "new_empty_rows": base_rows[diagnostic_query],
            "new_c0_rows": observations[
                (frozenset(("cand_0deae0d5de82a5a1fdc34111",)), diagnostic_query)
            ].rows_pre_clamp,
            "new_c1_rows": observations[
                (frozenset(("cand_1537a6ae8ef67c379c8d0fa0",)), diagnostic_query)
            ].rows_pre_clamp,
            "predicted_joint_rows": predicted_146,
            "predicted_joint_plan_rows": _postgres_clamp_row_estimate(predicted_146),
            "historical_rounded_factor_prediction": 9.0,
        },
        "enumeration": {
            "candidate_costs": {candidate_id: 1.0 for candidate_id in candidate_ids},
            "budgets": list(range(13)),
            "raw": raw_enum,
            "plan": plan_enum,
            "local_table_statistics": {
                "raw_query_local_subsets": raw_subsets,
                "raw_maximal_matchings": raw_matchings,
                "raw_maximum_local_table_size": raw_max,
                "plan_query_local_subsets": plan_subsets,
                "plan_maximal_matchings": plan_matchings,
                "plan_maximum_local_table_size": plan_max,
            },
        },
        "historical_optimal_set_comparison": {
            "membership": list(old_membership),
            "raw_pessimistic_objective": old_raw,
            "raw_uniform_objective": old_uniform,
            "plan_pessimistic_objective": old_plan,
            "plan_uniform_objective": old_plan_uniform,
            "historical_saved_pessimistic_objective": optimization_artifact["enumeration"][
                "budgets"
            ][4]["objective"],
            "historical_saved_uniform_objective": permutation_artifact["summary"][
                "theoretical_uniform_objective"
            ],
            "saved_permutation_mean": permutation_artifact["summary"]["mean_objective"],
            "saved_permutation_min": permutation_artifact["summary"]["minimum_objective"],
            "saved_permutation_max": permutation_artifact["summary"]["maximum_objective"],
            "per_query": old_local,
        },
        "runtime": {
            "database_name": result.database_name,
            "relation_oid": result.relation_oid,
            "cleanup_verified": result.cleanup_verified,
            "sandbox_verification": result.sandbox_verification,
            "planner_query_evaluations": len(result.observations),
        },
    }


def write_raw_mcv_matrix_json(payload: Mapping[str, Any], destination: Path) -> None:
    """Atomically publish one immutable raw-matrix result."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(_json_safe(payload), indent=2, sort_keys=False, allow_nan=False) + "\n"
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(f"refusing to overwrite result: {destination}")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
