"""Small real-PostgreSQL RQ4 integration harness.

This module is intentionally an adapter, not a second estimator.  Snapshot,
native payload, hypothetical registration, planner estimates, and q-error all
come from the frozen advisor APIs.  The research repository only selects the
query fixture, computes non-leaking signal metadata, and records accounting.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Self

from . import FROZEN_ADVISOR_SHA
from .pins import verify_git_sha
from .provenance import semantic_digest
from .rq4_ablation import (
    ConfigurationEvaluation,
    ConfigurationEvaluationBackend,
    EvaluationBudget,
    build_eligible_universe,
    run_rq4_ablation,
)


@dataclass(frozen=True)
class RQ4ArtifactPaths:
    snapshot: Path
    candidate_universe: Path
    native_repository: Path
    ground_truth: Path


REAL_SMOKE_FORMAT = "rq4-real-backend-smoke-v1"


class FrozenPostgresRQ4Backend(AbstractContextManager["FrozenPostgresRQ4Backend"]):
    """Evaluate smoke configurations through the frozen PostgreSQL planner."""

    def __init__(
        self,
        *,
        advisor_root: Path,
        patched_dsn: str,
        paths: RQ4ArtifactPaths,
        query_ids: Sequence[str],
    ) -> None:
        self.advisor_root = Path(advisor_root).resolve()
        self.patched_dsn = patched_dsn
        self.paths = paths
        self.query_ids = tuple(query_ids)
        self._session: Any = None
        self.snapshot: Any = None
        self.candidate_universe: Any = None
        self.native_repository: Any = None
        self.ground_truth: Any = None
        self.utility: Any = None
        self._planner: Any = None

    def open(self) -> Self:
        verify_git_sha(self.advisor_root, FROZEN_ADVISOR_SHA)
        advisor_src = str(self.advisor_root / "src")
        if advisor_src not in sys.path:
            sys.path.insert(0, advisor_src)
        from extstats_advisor.candidates import load_candidate_universe
        from extstats_advisor.dbms.postgres.planner import PostgresPlannerSession
        from extstats_advisor.ground_truth import load_ground_truth_set
        from extstats_advisor.ground_truth.provider import ArtifactGroundTruthProvider
        from extstats_advisor.native_stats import load_native_stats_repository
        from extstats_advisor.snapshot.bundle import load_snapshot
        from extstats_advisor.snapshot.model import Workload, WorkloadQuery
        from extstats_advisor.utility import WeightedWorkloadUtility
        from extstats_advisor.utility.loss import QErrorLoss

        self.snapshot = load_snapshot(self.paths.snapshot)
        self.candidate_universe = load_candidate_universe(
            self.paths.candidate_universe, self.snapshot
        )
        self.native_repository = load_native_stats_repository(self.paths.native_repository)
        self.ground_truth = load_ground_truth_set(self.paths.ground_truth, self.snapshot)
        workload_queries = {query.query_id: query for query in self.snapshot.workload.queries}
        missing = [query_id for query_id in self.query_ids if query_id not in workload_queries]
        if missing:
            raise ValueError(f"smoke query IDs are absent from snapshot: {missing}")
        if not self.query_ids:
            raise ValueError("real-backend smoke needs at least one query")
        smoke_queries = tuple(
            WorkloadQuery(
                query_id,
                workload_queries[query_id].sql,
                workload_queries[query_id].weight,
            )
            for query_id in self.query_ids
        )
        smoke_workload = Workload(
            self.snapshot.workload.workload_id,
            smoke_queries,
            {**self.snapshot.workload.provenance, "rq4_smoke_query_subset": True},
        )
        smoke_truths = tuple(
            truth for truth in self.ground_truth.truths if truth.query_id in set(self.query_ids)
        )
        smoke_ground_truth = type(self.ground_truth)(
            self.ground_truth.source_snapshot_semantic_digest,
            self.ground_truth.workload_id,
            self.ground_truth.source,
            smoke_truths,
            self.ground_truth.collection_contract,
            self.ground_truth.created_at,
            {**self.ground_truth.runtime_metadata, "rq4_smoke_query_subset": True},
        )
        self.utility = WeightedWorkloadUtility(
            smoke_workload,
            ArtifactGroundTruthProvider(smoke_ground_truth),
            QErrorLoss(),
        )
        self._planner = PostgresPlannerSession(
            self.patched_dsn,
            self.snapshot,
            self.candidate_universe,
            self.native_repository,
        )
        self._planner.open()
        self._session = self._planner
        return self

    def close(self) -> None:
        if self._planner is not None:
            self._planner.close()
        self._planner = None
        self._session = None

    def __enter__(self) -> Self:
        return self.open()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def evaluate(
        self, ordered_candidate_ids: tuple[str, ...], *, purpose: str
    ) -> ConfigurationEvaluation:
        if self._planner is None or self.utility is None:
            raise RuntimeError("RQ4 PostgreSQL backend is not open")
        started = time.perf_counter()
        from extstats_advisor.dbms.postgres.planner import PostgresStatisticsConfiguration

        self._planner.activate(PostgresStatisticsConfiguration(ordered_candidate_ids))
        estimates = {
            query_id: self._planner.estimate_query(query_id).estimated_rows
            for query_id in self.query_ids
        }
        utility = self.utility.evaluate(estimates)
        return ConfigurationEvaluation(
            float(utility.objective),
            planner_query_calls=len(self.query_ids),
            wall_clock_seconds=time.perf_counter() - started,
            metadata={
                "purpose": purpose,
                "utility_contract": self.utility.utility_contract,
                "loss_contract": self.utility.loss_contract,
                "query_count": len(self.query_ids),
                "active_backend_oid_count": len(self._planner.active_backend_oids()),
                "activation_order_verified": True,
                # Replay compares these planner observations directly.  They
                # are evidence emitted by the frozen planner, not a second
                # estimator implemented in the research harness.
                "planner_estimates": dict(estimates),
            },
        )

    def eligible_universe(self) -> dict[str, Any]:
        candidates = [candidate.to_dict() for candidate in self.candidate_universe.candidates]
        payloads = {
            candidate.candidate_id: {"available": candidate.state == "present"}
            for candidate in self.native_repository.candidate_models
        }
        return build_eligible_universe(
            {
                "format_version": "candidate-universe-v1",
                "source_snapshot_semantic_digest": self.candidate_universe.source_snapshot_semantic_digest,
                "semantic_digest": self.candidate_universe.semantic_digest,
                "candidates": candidates,
            },
            payloads,
        )

    def sample_side_signals(self, eligible: Mapping[str, Any]) -> dict[str, dict[str, float]]:
        """Return signals that do not inspect truth or planner outcomes."""

        weights = {query.query_id: float(query.weight) for query in self.snapshot.workload.queries}
        frequency = {
            item["candidate_id"]: sum(
                max(weights.get(query_id, 0.0), 0.0)
                for query_id in self.candidate_universe.query_ids_for_candidate(
                    item["candidate_id"]
                )
            )
            for item in eligible["eligible_candidates"]
        }
        native_by_id = {item.candidate_id: item for item in self.native_repository.candidate_models}
        payload_size = {
            item["candidate_id"]: float(native_by_id[item["candidate_id"]].payload_size)
            for item in eligible["eligible_candidates"]
        }
        return {
            "workload_frequency": frequency,
            "dependency_correlation": payload_size,
        }


def profile_singleton_utility(
    backend: ConfigurationEvaluationBackend, candidate_ids: Sequence[str]
) -> tuple[dict[str, float], dict[str, Any]]:
    """Profile singleton utility with observed configuration/query accounting."""

    started = time.perf_counter()
    baseline_raw = backend.evaluate((), purpose="singleton-profile-baseline")
    baseline = (
        baseline_raw.objective
        if isinstance(baseline_raw, ConfigurationEvaluation)
        else float(baseline_raw)
    )
    configuration_evaluations = 1
    planner_query_calls = (
        baseline_raw.planner_query_calls if isinstance(baseline_raw, ConfigurationEvaluation) else 0
    )
    backend_seconds = (
        baseline_raw.wall_clock_seconds
        if isinstance(baseline_raw, ConfigurationEvaluation)
        else 0.0
    )
    scores: dict[str, float] = {}
    for candidate_id in candidate_ids:
        raw = backend.evaluate((candidate_id,), purpose="singleton-profile")
        objective = raw.objective if isinstance(raw, ConfigurationEvaluation) else float(raw)
        scores[candidate_id] = baseline - objective
        configuration_evaluations += 1
        if isinstance(raw, ConfigurationEvaluation):
            planner_query_calls += raw.planner_query_calls
            backend_seconds += raw.wall_clock_seconds
    return scores, {
        "source": "frozen-postgres-planner-and-bound-ground-truth-v1",
        "configuration_objective_evaluations": configuration_evaluations,
        "postgresql_planner_query_calls": planner_query_calls,
        "backend_wall_clock_seconds": backend_seconds,
        "elapsed_wall_clock_seconds": time.perf_counter() - started,
    }


def build_real_backend_smoke_artifact(
    *,
    advisor_root: Path,
    patched_dsn: str,
    paths: RQ4ArtifactPaths,
    system_freeze: Mapping[str, Any],
    research_commit_sha: str,
    query_count: int = 3,
    physical_deployment_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a deliberately small real-backend smoke, never a formal RQ4 run."""

    if query_count <= 0:
        raise ValueError("query_count must be positive")
    from extstats_advisor.snapshot.bundle import load_snapshot

    snapshot = load_snapshot(paths.snapshot)
    query_ids = tuple(query.query_id for query in snapshot.workload.queries if query.weight > 0)[
        :query_count
    ]
    with FrozenPostgresRQ4Backend(
        advisor_root=advisor_root,
        patched_dsn=patched_dsn,
        paths=paths,
        query_ids=query_ids,
    ) as backend:
        eligible = backend.eligible_universe()
        candidate_ids = tuple(item["candidate_id"] for item in eligible["eligible_candidates"])
        signals = backend.sample_side_signals(eligible)
        singleton, singleton_accounting = profile_singleton_utility(backend, candidate_ids)
        comparison = run_rq4_ablation(
            eligible_universe=eligible,
            backend=backend,
            fixed_k=4,
            mode="fixed_k_quality",
            budget=EvaluationBudget(2000, 300.0),
            random_seed=123,
            singleton_profile_accounting=singleton_accounting,
            singleton_utility=singleton,
            **signals,
        )
        backend_evidence = {
            "advisor_sha": FROZEN_ADVISOR_SHA,
            "snapshot_semantic_digest": backend.snapshot.semantic_digest,
            "candidate_universe_semantic_digest": backend.candidate_universe.semantic_digest,
            "native_repository_semantic_digest": backend.native_repository.semantic_digest,
            "ground_truth_semantic_digest": backend.ground_truth.computed_semantic_digest,
            "hypothetical_registration": "backend-local-catalogless-postgresql",
            "query_count": len(query_ids),
            "query_ids": list(query_ids),
            "qerror_source": "frozen-advisor-WeightedWorkloadUtility-QErrorLoss",
        }
    artifact: dict[str, Any] = {
        "format_version": REAL_SMOKE_FORMAT,
        "experiment_id": "rq4-real-backend-integration-smoke-v1",
        "status": "real-backend-integration-smoke",
        "formal_confirmatory_experiment": False,
        "research_commit_sha": research_commit_sha,
        "system_freeze": dict(system_freeze),
        "system_freeze_semantic_digest": semantic_digest(system_freeze),
        "dataset": {"dataset_id": "real-artifact-fixture", "source_paths": [str(paths.snapshot)]},
        "smoke_fixture": {
            "query_count": len(query_ids),
            "query_ids": list(query_ids),
            "full_workload_evaluation": False,
            "formal_rq4_comparison": False,
        },
        "backend_evidence": backend_evidence,
        "eligible_universe": eligible,
        "comparison": comparison,
        "signals": {
            "workload_frequency_source": "frozen-workload-predicate-incidence",
            "dependency_correlation_source": "fixed-sample-native-payload-size-only",
            "singleton_utility_source": singleton_accounting,
        },
        "physical_deployment_validation": dict(
            physical_deployment_evidence
            or {
                "status": "available-via-frozen-advisor-deployment-api",
                "not_run_in_smoke": True,
                "reason": "The smoke validates planner activation and utility; formal per-method stock deployment is a separate RQ4 phase.",
            }
        ),
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    return artifact


def validate_real_backend_smoke(path: Path) -> dict[str, Any]:
    from .provenance import read_json

    artifact = read_json(path)
    if artifact.get("format_version") != REAL_SMOKE_FORMAT:
        raise ValueError("unsupported real-backend smoke format")
    expected = semantic_digest(
        {key: value for key, value in artifact.items() if key != "semantic_digest"}
    )
    if artifact.get("semantic_digest") != expected:
        raise ValueError("real-backend smoke semantic digest mismatch")
    if artifact.get("formal_confirmatory_experiment") is not False:
        raise ValueError("real-backend smoke cannot be formal confirmatory evidence")
    comparison = artifact.get("comparison", {})
    if comparison.get("method_order") != [
        "random-k",
        "workload-frequency-top-k",
        "dependency-correlation-top-k",
        "singleton-utility-top-k",
        "greedy-ADD",
    ]:
        raise ValueError("real-backend smoke did not use the primary RQ4 methods")
    return {
        "status": "valid",
        "format_version": REAL_SMOKE_FORMAT,
        "semantic_digest": expected,
        "formal_confirmatory_experiment": False,
    }


def inspect_real_backend_smoke(path: Path) -> dict[str, Any]:
    """Return a compact, validation-backed summary for an integration artifact."""

    from .provenance import read_json

    artifact = read_json(path)
    validation = validate_real_backend_smoke(path)
    comparison = artifact["comparison"]
    return {
        **validation,
        "artifact": str(path.resolve()),
        "experiment_id": artifact["experiment_id"],
        "dataset": artifact["dataset"],
        "query_count": artifact["smoke_fixture"]["query_count"],
        "method_count": len(comparison["methods"]),
        "physical_deployment_status": artifact["physical_deployment_validation"]["status"],
    }


__all__ = [
    "REAL_SMOKE_FORMAT",
    "FrozenPostgresRQ4Backend",
    "RQ4ArtifactPaths",
    "build_real_backend_smoke_artifact",
    "inspect_real_backend_smoke",
    "profile_singleton_utility",
    "validate_real_backend_smoke",
]
