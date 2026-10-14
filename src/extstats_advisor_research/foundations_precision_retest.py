"""Targeted high-precision retest for the historical separability observations.

The module adds only the output-precision diagnostic needed for the five
previously informative quadruples. It reuses the existing isolated database,
sandbox, native repository, planner session, and membership oracle.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .foundations_live_integration import (
    FoundationsOracleServices,
    FoundationsSmokeInput,
    _default_services,
    _raise_with_cleanup,
    _validate_bound_inputs,
)
from .foundations_oracle import BlackBoxMembershipOracle, OracleEvaluation
from .foundations_separability import (
    DisjointCandidatePair,
    MultiplicativeQuadruple,
    multiplicative_quadruple,
    rank_disjoint_candidate_pairs,
)
from .postgres.experiment_database import ExperimentDatabase, ExperimentDatabaseSpec

PRECISION_PAIR_817_019 = (
    "cand_817be8bf7bbbc96c20bde230",
    "cand_01933ab615fb49e2f81b5b92",
)
PRECISION_PAIR_817_599 = (
    "cand_817be8bf7bbbc96c20bde230",
    "cand_599a2efab190b6e2b7457ff1",
)
PRECISION_QUERY_017 = "arecel_forest10_test_000017"
PRECISION_QUERY_IDS = (
    "arecel_forest10_test_000013",
    PRECISION_QUERY_017,
    "arecel_forest10_test_000019",
    "arecel_forest10_test_000139",
)
DEFAULT_PRECISION_TOLERANCES = (1e-12, 1e-9, 1e-6)
HISTORICAL_ARTIFACT_SHA256 = "638cc7b0556315ab1303e3b658df69deb0fe96062bde30f1b7f670c7f2c2d9bd"


class PrecisionRetestInputError(ValueError):
    """Raised when the targeted precision retest is not fully bound."""


@dataclass(frozen=True, slots=True)
class PrecisionRetestBinding:
    smoke: FoundationsSmokeInput
    research_commit_sha: str
    patched_postgres_commit_sha: str
    prior_artifact_path: Path
    prior_artifact_sha256: str

    def __post_init__(self) -> None:
        for label in (
            "research_commit_sha",
            "patched_postgres_commit_sha",
            "prior_artifact_sha256",
        ):
            value = getattr(self, label)
            if not isinstance(value, str) or not value.strip():
                raise PrecisionRetestInputError(f"{label} must be non-empty")
        if tuple(self.smoke.query_ids) != PRECISION_QUERY_IDS:
            raise PrecisionRetestInputError(
                "precision retest requires the four queries used by the five observations"
            )


@dataclass(frozen=True, slots=True)
class PrecisionPairRun:
    pair: DisjointCandidatePair
    query_ids: tuple[str, ...]
    evaluations: tuple[OracleEvaluation, ...]
    sandbox_verification: Mapping[str, Any]
    database_name: str
    lifecycle_completed: bool = True
    shared_evaluation_query_ids: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        return self.lifecycle_completed and all(
            evaluation.diagnostics.status == "complete" for evaluation in self.evaluations
        )


@dataclass(frozen=True, slots=True)
class PrecisionRetestResult:
    binding: PrecisionRetestBinding
    pair_runs: tuple[PrecisionPairRun, ...]
    database_name: str
    sandbox_verification: Mapping[str, Any]
    primary_planner_query_count: int
    distinct_configuration_count: int
    lifecycle_completed: bool = True

    @property
    def complete(self) -> bool:
        return self.lifecycle_completed and all(run.complete for run in self.pair_runs)


def _artifact_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target_pairs(
    universe: Any, prior_artifact_path: Path
) -> dict[frozenset[str], DisjointCandidatePair]:
    if _artifact_sha256(prior_artifact_path) != HISTORICAL_ARTIFACT_SHA256:
        raise PrecisionRetestInputError("historical separability artifact hash does not match")
    with prior_artifact_path.open(encoding="utf-8") as handle:
        historical = json.load(handle)
    historical_pairs = {
        frozenset((item["candidate_a_id"], item["candidate_b_id"]))
        for item in historical.get("selected_pairs", [])
    }
    expected_pairs = {
        frozenset(PRECISION_PAIR_817_019),
        frozenset(PRECISION_PAIR_817_599),
    }
    if not expected_pairs.issubset(historical_pairs):
        raise PrecisionRetestInputError("historical artifact does not bind both target pairs")
    ranked = rank_disjoint_candidate_pairs(universe)
    by_ids = {frozenset(pair.candidate_ids): pair for pair in ranked}
    if not expected_pairs.issubset(by_ids):
        raise PrecisionRetestInputError(
            "current candidate universe does not contain both target pairs"
        )
    if any(not by_ids[pair_ids].joint_query_count for pair_ids in expected_pairs):
        raise PrecisionRetestInputError("target pair is not structurally jointly relevant")
    return {pair_ids: by_ids[pair_ids] for pair_ids in expected_pairs}


def _restrict_evaluation(
    evaluation: OracleEvaluation, query_ids: Sequence[str]
) -> OracleEvaluation:
    selected = tuple(query_ids)
    estimates = None
    if evaluation.estimates is not None:
        estimates = {query_id: evaluation.estimates[query_id] for query_id in selected}
    formatted = None
    if evaluation.formatted_estimates is not None:
        formatted = {query_id: evaluation.formatted_estimates[query_id] for query_id in selected}
    return replace(evaluation, estimates=estimates, formatted_estimates=formatted)


def run_precision_retest(
    binding: PrecisionRetestBinding,
    *,
    services: FoundationsOracleServices | None = None,
    lifecycle_factory: Callable[[ExperimentDatabaseSpec], Any] = ExperimentDatabase,
) -> PrecisionRetestResult:
    """Run one isolated, deduplicated precision retest for the two target pairs."""

    services = services or _default_services(
        binding.smoke.advisor_root, binding.smoke.expected_advisor_sha
    )
    snapshot, universe, ground_truth = _validate_bound_inputs(binding.smoke, services)
    pairs = _target_pairs(universe, binding.prior_artifact_path)
    pair_599 = pairs[frozenset(PRECISION_PAIR_817_599)]
    pair_019 = pairs[frozenset(PRECISION_PAIR_817_019)]
    spec = ExperimentDatabaseSpec(
        admin_dsn=binding.smoke.admin_dsn,
        name_prefix=binding.smoke.name_prefix,
        sample_rows=binding.smoke.sample_rows,
    )
    lifecycle = lifecycle_factory(spec)
    result: PrecisionRetestResult | None = None
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
                binding.smoke.statistics_target,
                binding.smoke.native_batch_size,
            )
            services.prepare_sandbox(
                database.database_dsn,
                snapshot,
                universe,
                repository,
                database.database_name,
                binding.smoke.native_batch_size,
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

            pair_599_queries = tuple(
                query_id
                for query_id in binding.smoke.query_ids
                if query_id in set(PRECISION_QUERY_IDS)
            )
            utility_599 = services.make_utility(snapshot, ground_truth, pair_599_queries)
            oracle_599 = BlackBoxMembershipOracle(
                session=session,
                snapshot=snapshot,
                candidate_universe=universe,
                global_precedence=binding.smoke.global_precedence,
                utility_provider=utility_599,
                native_repository=repository,
                query_ids=pair_599_queries,
                expected_snapshot_digest=binding.smoke.expected_snapshot_digest,
                expected_candidate_universe_digest=binding.smoke.expected_candidate_universe_digest,
            )
            evaluations_599 = tuple(
                oracle_599.evaluate_precise(membership)
                for membership in (
                    (),
                    (pair_599.candidate_ids[0],),
                    (pair_599.candidate_ids[1],),
                    pair_599.candidate_ids,
                )
            )

            utility_019 = services.make_utility(snapshot, ground_truth, (PRECISION_QUERY_017,))
            oracle_019 = BlackBoxMembershipOracle(
                session=session,
                snapshot=snapshot,
                candidate_universe=universe,
                global_precedence=binding.smoke.global_precedence,
                utility_provider=utility_019,
                native_repository=repository,
                query_ids=(PRECISION_QUERY_017,),
                expected_snapshot_digest=binding.smoke.expected_snapshot_digest,
                expected_candidate_universe_digest=binding.smoke.expected_candidate_universe_digest,
            )
            evaluations_019 = (
                _restrict_evaluation(evaluations_599[0], (PRECISION_QUERY_017,)),
                _restrict_evaluation(evaluations_599[1], (PRECISION_QUERY_017,)),
                oracle_019.evaluate_precise((pair_019.candidate_ids[1],)),
                oracle_019.evaluate_precise(pair_019.candidate_ids),
            )
            session.close()
            session = None
            services.destroy_sandbox(database.database_dsn)
            sandbox_prepared = False
            result = PrecisionRetestResult(
                binding,
                (
                    PrecisionPairRun(
                        pair_019,
                        (PRECISION_QUERY_017,),
                        evaluations_019,
                        verification,
                        database.database_name,
                        shared_evaluation_query_ids=pair_599_queries,
                    ),
                    PrecisionPairRun(
                        pair_599,
                        pair_599_queries,
                        evaluations_599,
                        verification,
                        database.database_name,
                    ),
                ),
                database.database_name,
                verification,
                primary_planner_query_count=18,
                distinct_configuration_count=6,
            )
        except BaseException as exc:  # noqa: BLE001 - preserve experiment error
            primary = exc
        finally:
            if session is not None:
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
    if result is None:  # pragma: no cover - defensive invariant
        raise PrecisionRetestInputError("precision retest produced no result")
    return result


def _quadruple_payload(
    pair: DisjointCandidatePair,
    evaluations: Sequence[OracleEvaluation],
    query_id: str,
    tolerances: Sequence[float],
) -> dict[str, Any]:
    by_membership = {frozenset(item.diagnostics.membership): item for item in evaluations}
    ordered = (
        by_membership[frozenset()],
        by_membership[frozenset({pair.candidate_a_id})],
        by_membership[frozenset({pair.candidate_b_id})],
        by_membership[frozenset(pair.candidate_ids)],
    )
    precise = [
        item.estimates[query_id]
        for item in ordered
        if item.estimates is not None and query_id in item.estimates
    ]
    rounded = [
        item.formatted_estimates[query_id]
        for item in ordered
        if item.formatted_estimates is not None and query_id in item.formatted_estimates
    ]
    if len(precise) != 4 or len(rounded) != 4:
        raise PrecisionRetestInputError(
            f"missing precision observation for {pair.candidate_ids} {query_id}"
        )
    diagnostic: MultiplicativeQuadruple = multiplicative_quadruple(
        query_id,
        estimate_empty=precise[0],
        estimate_a=precise[1],
        estimate_b=precise[2],
        estimate_ab=precise[3],
    )
    return {
        "query_id": query_id,
        "candidate_pair": list(pair.candidate_ids),
        "rounded_estimates": dict(zip(("empty", "a", "b", "ab"), rounded, strict=True)),
        "precise_estimates": dict(zip(("empty", "a", "b", "ab"), precise, strict=True)),
        "diagnostic": diagnostic.as_dict(tolerances),
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite value cannot be serialized")
    return value


def write_precision_retest_result_json(
    result: PrecisionRetestResult,
    destination: Path,
    *,
    tolerances: Sequence[float] = DEFAULT_PRECISION_TOLERANCES,
) -> None:
    """Publish the five raw rounded/precise quadruples atomically, once."""

    destination = Path(destination)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite existing result: {destination}")
    binding = result.binding
    pair_payloads = []
    quadruples = []
    for pair_run in result.pair_runs:
        configurations = []
        for evaluation in pair_run.evaluations:
            query_estimates = {
                query_id: evaluation.estimates[query_id]
                for query_id in pair_run.query_ids
                if evaluation.estimates is not None and query_id in evaluation.estimates
            }
            formatted = {
                query_id: evaluation.formatted_estimates[query_id]
                for query_id in pair_run.query_ids
                if evaluation.formatted_estimates is not None
                and query_id in evaluation.formatted_estimates
            }
            configurations.append(
                {
                    "membership": list(evaluation.diagnostics.membership),
                    "status": evaluation.diagnostics.status,
                    "precise_estimates": query_estimates,
                    "rounded_estimates": formatted,
                    "objective": evaluation.objective,
                    "diagnostics": evaluation.diagnostics.as_dict(),
                }
            )
        pair_payloads.append(
            {
                "pair": pair_run.pair.as_dict(),
                "query_ids": list(pair_run.query_ids),
                "shared_evaluation_query_ids": list(pair_run.shared_evaluation_query_ids),
                "database_name": pair_run.database_name,
                "lifecycle_completed": pair_run.lifecycle_completed,
                "configurations": configurations,
            }
        )
        for query_id in pair_run.query_ids:
            quadruples.append(
                _quadruple_payload(pair_run.pair, pair_run.evaluations, query_id, tolerances)
            )
    payload = {
        "classification": "formal-output-only-precision-diagnostic",
        "experiment_id": "foundations-disjoint-multiplicative-separability-precision-v1",
        "source_bindings": {
            "research_commit_sha": binding.research_commit_sha,
            "advisor_commit_sha": binding.smoke.expected_advisor_sha,
            "patched_postgres_commit_sha": binding.patched_postgres_commit_sha,
        },
        "historical_reference": {
            "path": str(binding.prior_artifact_path),
            "sha256": binding.prior_artifact_sha256,
        },
        "artifact_bindings": {
            "snapshot_semantic_digest": binding.smoke.expected_snapshot_digest,
            "candidate_universe_semantic_digest": binding.smoke.expected_candidate_universe_digest,
            "ground_truth_semantic_digest": binding.smoke.expected_ground_truth_digest,
        },
        "experiment_inputs": {
            "dataset_id": binding.smoke.dataset_id,
            "relation_id": binding.smoke.relation_id,
            "relation_schema": binding.smoke.relation_schema,
            "relation_name": binding.smoke.relation_name,
            "workload_id": binding.smoke.workload_id,
            "sample_rows": binding.smoke.sample_rows,
            "statistics_target": binding.smoke.statistics_target,
            "query_ids": list(binding.smoke.query_ids),
            "global_precedence": list(binding.smoke.global_precedence),
        },
        "runtime": {
            "database_name": result.database_name,
            "lifecycle_completed": result.lifecycle_completed,
            "sandbox_verification": dict(result.sandbox_verification),
            "independent_post_cleanup_database_check": False,
        },
        "primary_planner_query_count": result.primary_planner_query_count,
        "distinct_configuration_count": result.distinct_configuration_count,
        "complete": result.complete,
        "pair_runs": pair_payloads,
        "quadruples": quadruples,
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
