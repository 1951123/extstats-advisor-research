"""Exhaustive creation-order evaluation for one frozen MCV membership.

This module is deliberately narrower than the Foundations Oracle.  The normal
Oracle canonicalizes a membership to the complete frozen precedence.  This
experiment must preserve an explicitly supplied order, so it uses the
existing PlannerSession directly and verifies the backend's active OID array.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .foundations_landscape import FoundationsLandscapeInput
from .foundations_live_integration import (
    FoundationsOracleServices,
    _default_services,
    _raise_with_cleanup,
    _validate_bound_inputs,
)
from .foundations_pessimistic_enumeration import enumerate_maximal_matchings
from .foundations_surrogate_milp import write_surrogate_result_json
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

EXPECTED_BUDGET = 4
EXPECTED_OBJECTIVE = 13.404890550101985
ORDERED_MEMBERSHIP = (
    "cand_0deae0d5de82a5a1fdc34111",
    "cand_1537a6ae8ef67c379c8d0fa0",
    "cand_5ba9cc937a696a76876f820d",
    "cand_817be8bf7bbbc96c20bde230",
)
ORDER_PERMUTATION_COUNT = 24
ORDER_EXPERIMENT_ID = "foundations-pessimistic-12mcv-order-v1"


class OrderPermutationError(ValueError):
    """Raised when the frozen order experiment cannot be bound safely."""


def enumerate_creation_permutations(
    candidate_ids: Sequence[str] = ORDERED_MEMBERSHIP,
) -> tuple[tuple[str, ...], ...]:
    """Return all permutations in deterministic input-order enumeration."""

    ids = tuple(candidate_ids)
    if len(ids) != 4 or len(ids) != len(set(ids)) or any(not item for item in ids):
        raise OrderPermutationError("exactly four distinct candidate IDs are required")
    result = tuple(itertools.permutations(ids))
    if len(result) != ORDER_PERMUTATION_COUNT or len(set(result)) != len(result):
        raise OrderPermutationError("permutation enumeration is incomplete or duplicated")
    return result


def _finite_positive(value: Any, label: str) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise OrderPermutationError(f"{label} must be finite and positive")
    return result


def _loss(estimate: float, truth: float) -> float:
    return abs(math.log(_finite_positive(estimate, "estimate")) - math.log(max(1.0, truth)))


def _artifact_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_optimization_artifact(path: Path, expected_sha256: str) -> dict[str, Any]:
    path = Path(path)
    if _artifact_sha256(path) != expected_sha256:
        raise OrderPermutationError("optimization artifact SHA256 does not match the frozen input")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("complete") is not True:
        raise OrderPermutationError("optimization artifact is not complete")
    budgets = value.get("enumeration", {}).get("budgets", [])
    selected = [item for item in budgets if item.get("budget") == EXPECTED_BUDGET]
    if len(selected) != 1:
        raise OrderPermutationError("optimization artifact lacks exactly one budget-4 result")
    budget = selected[0]
    if budget.get("optimal_set_count") != 1:
        raise OrderPermutationError("budget-4 optimum is not unique")
    if tuple(budget.get("representative_membership", ())) != tuple(sorted(ORDERED_MEMBERSHIP)):
        raise OrderPermutationError("budget-4 membership differs from the frozen optimum")
    if not math.isclose(float(budget["objective"]), EXPECTED_OBJECTIVE, abs_tol=1e-12):
        raise OrderPermutationError("budget-4 objective differs from the frozen optimum")
    return value


def theoretical_references(
    optimization_artifact: Mapping[str, Any],
    membership: Sequence[str] = ORDERED_MEMBERSHIP,
) -> dict[str, Any]:
    """Compute pessimistic and uniform maximal-matching losses in memory."""

    membership_set = set(membership)
    metadata = {item["candidate_id"]: item for item in optimization_artifact["candidate_metadata"]}
    columns = {candidate_id: tuple(item["column_names"]) for candidate_id, item in metadata.items()}
    model = optimization_artifact["model"]
    query_ids = tuple(model["query_ids"])
    weights = {query_id: float(value) for query_id, value in model["query_weights"].items()}
    alpha = {query_id: float(value) for query_id, value in model["alpha"].items()}
    corrections = model["b"]
    applicability = model["applicable_mcv_candidates_by_query"]
    per_query: dict[str, dict[str, Any]] = {}
    pessimistic = 0.0
    uniform = 0.0
    for query_id in query_ids:
        local = tuple(sorted(membership_set & set(applicability[query_id])))
        matchings = enumerate_maximal_matchings(local, columns)
        losses = tuple(
            abs(
                alpha[query_id]
                + sum(float(corrections[candidate_id][query_id]) for candidate_id in matching)
            )
            for matching in matchings
        )
        worst = max(losses)
        mean = sum(losses) / len(losses)
        worst_matchings = [
            list(matching)
            for matching, loss in zip(matchings, losses, strict=True)
            if loss == worst
        ]
        per_query[query_id] = {
            "applicable_membership": list(local),
            "maximal_matching_count": len(matchings),
            "uniform_loss": mean,
            "pessimistic_loss": worst,
            "maximal_matchings": [list(matching) for matching in matchings],
            "pessimistic_witnesses": worst_matchings,
        }
        pessimistic += weights[query_id] * worst
        uniform += weights[query_id] * mean
    return {
        "pessimistic_objective": pessimistic,
        "uniform_objective": uniform,
        "per_query": per_query,
    }


def _database_exists(admin_dsn: str, database_name: str) -> bool:
    import psycopg

    with psycopg.connect(
        admin_dsn, autocommit=True, application_name="foundations-order-cleanup"
    ) as connection:
        return (
            connection.execute(
                "SELECT 1 FROM pg_catalog.pg_database WHERE datname = %s",
                (database_name,),
            ).fetchone()
            is not None
        )


def _payload_inventory(repository: Any) -> dict[str, dict[str, Any]]:
    inventory = {}
    for candidate in repository.candidate_models:
        if candidate.candidate_id not in ORDERED_MEMBERSHIP:
            continue
        payload = repository.payloads.get(candidate.candidate_id)
        if candidate.state != "present" or not isinstance(payload, bytes) or not payload:
            raise OrderPermutationError(
                f"selected payload is unavailable: {candidate.candidate_id}"
            )
        digest = hashlib.sha256(payload).hexdigest()
        if digest != candidate.payload_sha256:
            raise OrderPermutationError(
                f"selected payload digest mismatch: {candidate.candidate_id}"
            )
        inventory[candidate.candidate_id] = {
            "sha256": digest,
            "size": len(payload),
            "kind": candidate.kind,
            "columns": list(candidate.column_names),
        }
    if set(inventory) != set(ORDERED_MEMBERSHIP):
        raise OrderPermutationError("selected payload inventory is incomplete")
    return inventory


def _aggregate_permutation(
    per_query: Mapping[str, Mapping[str, Any]], weights: Mapping[str, float]
) -> float:
    return sum(
        float(weights[query_id]) * float(item["loss"]) for query_id, item in per_query.items()
    )


def _build_summary(
    permutations: Sequence[Mapping[str, Any]],
    query_ids: Sequence[str],
    weights: Mapping[str, float],
    theoretical: Mapping[str, Any],
) -> dict[str, Any]:
    objectives = [float(item["objective"]) for item in permutations]
    vectors = [
        tuple(float(item["estimates"][query_id]["precise"]) for query_id in query_ids)
        for item in permutations
    ]
    vector_counts = Counter(vectors)
    objective_counts = Counter(objectives)
    per_query = {}
    changed_queries = []
    for query_id in query_ids:
        estimates = [float(item["estimates"][query_id]["precise"]) for item in permutations]
        losses = [float(item["per_query_losses"][query_id]) for item in permutations]
        changed = len(set(estimates)) > 1
        if changed:
            changed_queries.append(query_id)
        per_query[query_id] = {
            "changed_across_permutations": changed,
            "distinct_estimate_count": len(set(estimates)),
            "minimum_estimate": min(estimates),
            "maximum_estimate": max(estimates),
            "mean_estimate": sum(estimates) / len(estimates),
            "minimum_loss": min(losses),
            "maximum_loss": max(losses),
            "mean_loss": sum(losses) / len(losses),
            "theoretical_pessimistic_loss": theoretical["per_query"][query_id]["pessimistic_loss"],
            "theoretical_uniform_loss": theoretical["per_query"][query_id]["uniform_loss"],
        }
    frequencies = []
    for vector, count in sorted(vector_counts.items(), key=lambda item: item[0]):
        frequencies.append({"estimates": list(vector), "frequency": count})
    return {
        "mean_objective": sum(objectives) / len(objectives),
        "population_standard_deviation": statistics.pstdev(objectives),
        "minimum_objective": min(objectives),
        "maximum_objective": max(objectives),
        "median_objective": statistics.median(objectives),
        "distinct_complete_estimate_vector_count": len(vector_counts),
        "distinct_objective_count": len(objective_counts),
        "objective_frequencies": [
            {"objective": objective, "frequency": count}
            for objective, count in sorted(objective_counts.items())
        ],
        "estimate_vector_frequencies": frequencies,
        "best_permutations": [
            item["permutation_id"] for item in permutations if item["objective"] == min(objectives)
        ],
        "worst_permutations": [
            item["permutation_id"] for item in permutations if item["objective"] == max(objectives)
        ],
        "changed_query_ids": changed_queries,
        "per_query": per_query,
        "theoretical_pessimistic_objective": theoretical["pessimistic_objective"],
        "theoretical_uniform_objective": theoretical["uniform_objective"],
        "weights_sum": sum(float(weights[query_id]) for query_id in query_ids),
    }


def run_foundations_order_permutations(
    inputs: FoundationsLandscapeInput,
    *,
    optimization_artifact: Path,
    optimization_artifact_sha256: str,
    destination: Path,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Any = ExperimentDatabase,
) -> Mapping[str, Any]:
    """Run exactly 24 ordered activations and publish one final artifact."""

    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    artifact = _load_optimization_artifact(
        Path(optimization_artifact), optimization_artifact_sha256
    )
    theoretical = theoretical_references(artifact)
    if not math.isclose(theoretical["pessimistic_objective"], EXPECTED_OBJECTIVE, abs_tol=1e-12):
        raise OrderPermutationError("recomputed pessimistic objective differs from frozen result")
    services = services or _default_services(inputs.advisor_root, inputs.expected_advisor_sha)
    snapshot, universe, ground_truth = _validate_bound_inputs(inputs, services)
    candidate_by_id = {candidate.candidate_id: candidate for candidate in universe.candidates}
    if set(ORDERED_MEMBERSHIP) - set(candidate_by_id):
        raise OrderPermutationError("frozen order set is absent from CandidateUniverse")
    query_ids = tuple(inputs.query_ids)
    profile_weights = {
        profile.query_id: float(profile.weight) for profile in universe.query_profiles
    }
    weights = {query_id: profile_weights[query_id] for query_id in query_ids}
    truth_by_id = {
        truth.query_id: float(truth.cardinality)
        for truth in ground_truth.truths
        if truth.query_id in query_ids
    }
    if set(truth_by_id) != set(query_ids):
        raise OrderPermutationError("ground truth does not cover the frozen query workload")
    permutations = enumerate_creation_permutations()
    lifecycle = lifecycle_factory(
        ExperimentDatabaseSpec(
            admin_dsn=inputs.admin_dsn,
            name_prefix="exp_foundations_order",
            sample_rows=inputs.sample_rows,
        )
    )
    result: dict[str, Any] | None = None
    database_name = ""
    verification: Mapping[str, Any] = {}
    session: Any | None = None
    sandbox_prepared = False
    with lifecycle as database:
        database_name = database.database_name
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
            payloads = _payload_inventory(repository)
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
            from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

            registered_oids = session.registered_oids
            if any(candidate_id not in registered_oids for candidate_id in ORDERED_MEMBERSHIP):
                raise OrderPermutationError("selected candidate lacks a registered virtual OID")
            records = []
            for index, order in enumerate(permutations):
                session.activate(PostgresStatisticsConfiguration(order))
                active_oids = tuple(session.active_backend_oids())
                expected_oids = tuple(registered_oids[candidate_id] for candidate_id in order)
                if active_oids != expected_oids:
                    raise OrderPermutationError(
                        f"backend did not preserve permutation {index:02d}: {active_oids!r} != {expected_oids!r}"
                    )
                per_query = {}
                for query_id in query_ids:
                    estimate = session.estimate_query_precise(query_id)
                    precise = _finite_positive(estimate.estimated_rows, f"estimate {query_id}")
                    per_query[query_id] = {
                        "precise": precise,
                        "formatted": int(estimate.formatted_estimated_rows),
                        "loss": _loss(precise, truth_by_id[query_id]),
                    }
                records.append(
                    {
                        "permutation_id": f"perm-{index:02d}",
                        "creation_order": list(order),
                        "membership": list(ORDERED_MEMBERSHIP),
                        "registered_oids_by_candidate": {
                            candidate_id: registered_oids[candidate_id]
                            for candidate_id in ORDERED_MEMBERSHIP
                        },
                        "active_virtual_oids": list(active_oids),
                        "order_preserved": True,
                        "payload_sha256_by_candidate": {
                            candidate_id: payloads[candidate_id]["sha256"] for candidate_id in order
                        },
                        "estimates": {
                            query_id: {
                                "precise": item["precise"],
                                "formatted": item["formatted"],
                            }
                            for query_id, item in per_query.items()
                        },
                        "per_query_losses": {
                            query_id: item["loss"] for query_id, item in per_query.items()
                        },
                        "objective": _aggregate_permutation(per_query, weights),
                    }
                )
                session.activate(PostgresStatisticsConfiguration(()))
                if session.active_backend_oids() != ():
                    raise OrderPermutationError(
                        "active statistics state leaked between permutations"
                    )
            result = {
                "experiment_id": ORDER_EXPERIMENT_ID,
                "complete": True,
                "source_bindings": {
                    "research_commit_sha": inputs.research_commit_sha,
                    "advisor_commit_sha": inputs.expected_advisor_sha,
                    "patched_postgres_build_source_sha": inputs.patched_postgres_commit_sha,
                    "patched_postgres_compatibility_reference_sha": "6d7f5c9cd6cf1b0f73e84a4bacc45a31d1cb0cd6",
                    "backend_contract": "postgresql-pgextadv-16.14-v1",
                },
                "artifact_bindings": {
                    "snapshot_semantic_digest": inputs.expected_snapshot_digest,
                    "candidate_universe_semantic_digest": inputs.expected_candidate_universe_digest,
                    "ground_truth_semantic_digest": inputs.expected_ground_truth_digest,
                    "optimization_artifact": str(Path(optimization_artifact)),
                    "optimization_artifact_sha256": optimization_artifact_sha256,
                },
                "experiment_inputs": {
                    "dataset_id": inputs.dataset_id,
                    "relation_id": inputs.relation_id,
                    "relation_schema": inputs.relation_schema,
                    "relation_name": inputs.relation_name,
                    "workload_id": inputs.workload_id,
                    "sample_rows": inputs.sample_rows,
                    "statistics_target": inputs.statistics_target,
                    "query_ids": list(query_ids),
                    "global_precedence": list(inputs.global_precedence),
                    "fixed_membership": list(ORDERED_MEMBERSHIP),
                    "permutation_count": len(records),
                    "primary_planner_query_count": len(records) * len(query_ids),
                },
                "candidate_metadata": [
                    {
                        "candidate_id": candidate_id,
                        "kind": candidate_by_id[candidate_id].kind,
                        "columns": list(candidate_by_id[candidate_id].column_names),
                        "precedence_rank": candidate_by_id[candidate_id].static_precedence_rank,
                        "payload": payloads[candidate_id],
                    }
                    for candidate_id in ORDERED_MEMBERSHIP
                ],
                "theoretical_references": theoretical,
                "permutations": records,
                "summary": _build_summary(records, query_ids, weights, theoretical),
                "runtime": {
                    "database_name": database_name,
                    "sandbox_verification": dict(verification),
                    "database_lifecycle_completed": True,
                    "active_state_reset_between_permutations": True,
                },
            }
        except BaseException as exc:  # noqa: BLE001 - preserve live failure
            primary = exc
        finally:
            if session is not None:
                try:
                    session.close()
                except BaseException as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
                session = None
            if sandbox_prepared:
                try:
                    services.destroy_sandbox(database.database_dsn)
                except BaseException as exc:  # noqa: BLE001
                    cleanup_errors.append(exc)
            _raise_with_cleanup(primary, cleanup_errors)
    if result is None:
        raise OrderPermutationError("order experiment returned no result")
    result["runtime"]["cleanup_verified_by_database_catalog_check"] = not _database_exists(
        inputs.admin_dsn, database_name
    )
    if not result["runtime"]["cleanup_verified_by_database_catalog_check"]:
        raise OrderPermutationError(f"owned database remains after cleanup: {database_name}")
    write_surrogate_result_json(result, destination)
    return result
