"""Offline protocol and readiness checks for Native ANALYZE Stability v1.

This module deliberately contains no database or workload execution code.  It
binds the proposed study to the already committed RQ4 fixed-k evidence and
checks that the protocol does not silently normalize historical v1 evidence to
the current v2 freeze.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provenance import semantic_digest, sha256_file

DECISION_FORMAT = "rq4-fixed-evaluation-budget-scope-decision-v1"
PROTOCOL_FORMAT = "native-analyze-stability-protocol-v1"
READINESS_FORMAT = "native-analyze-stability-readiness-review-v1"
DECISION_ID = "rq4-fixed-evaluation-budget-scope-decision-v1"
PROTOCOL_ID = "native-analyze-stability-v1"
READINESS_ID = "native-analyze-stability-readiness-review-v1"

DATASET_SOURCES = {
    "arecel-forest10": {
        "design": "experiments/arecel-forest10/rq4-fixed-k/rq4-design-evaluation-v1.json",
        "ablation": "experiments/arecel-forest10/rq4-fixed-k/rq4-ablation-v1.json",
        "shared": "experiments/arecel-forest10/rq4-fixed-k/physical/dependency-correlation-top-k.json",
        "freeze": "paper/system-freeze-v1.json",
        "stratum": "historical-v1",
        "method_alias": {"dependency-correlation-top-k": "native-payload-size-top-k"},
    },
    "arecel-census13": {
        "design": "experiments/arecel-census13/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
        "ablation": "experiments/arecel-census13/rq4-fixed-k-v2/rq4-ablation-v2.json",
        "shared": "experiments/arecel-census13/rq4-fixed-k-v2/rq4-stock-shared-realization-v2.json",
        "freeze": "paper/system-freeze-v2.json",
        "stratum": "current-v2",
        "method_alias": {},
    },
    "arecel-dmv11": {
        "design": "experiments/arecel-dmv11/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
        "ablation": "experiments/arecel-dmv11/rq4-fixed-k-v2/rq4-ablation-v2.json",
        "shared": "experiments/arecel-dmv11/rq4-fixed-k-v2/rq4-stock-shared-realization-v2.json",
        "freeze": "paper/system-freeze-v2.json",
        "stratum": "current-v2",
        "method_alias": {},
    },
    "arecel-power7": {
        "design": "experiments/arecel-power7/rq4-fixed-k-v2/rq4-design-evaluation-v2.json",
        "ablation": "experiments/arecel-power7/rq4-fixed-k-v2/rq4-ablation-v2.json",
        "shared": "experiments/arecel-power7/rq4-fixed-k-v2/rq4-stock-shared-realization-v2.json",
        "freeze": "paper/system-freeze-v2.json",
        "stratum": "current-v2",
        "method_alias": {},
    },
}

PRIMARY_DATASETS = ("arecel-forest10", "arecel-census13", "arecel-dmv11")
SENSITIVITY_DATASETS = ("arecel-power7",)
METHOD_ORDER = (
    "random-k-seed-1",
    "random-k-seed-2",
    "random-k-seed-3",
    "random-k-seed-4",
    "random-k-seed-5",
    "workload-frequency-top-k",
    "native-payload-size-top-k",
    "singleton-utility-top-k",
    "greedy-ADD",
)


def _without_digest(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "semantic_digest"}


def _read(root: Path, relative: str) -> dict[str, Any]:
    return json.loads((root / relative).read_text(encoding="utf-8"))


def _source_ref(root: Path, relative: str) -> dict[str, str]:
    path = root / relative
    value = _read(root, relative)
    expected = semantic_digest(_without_digest(value))
    # RQ4 physical artifacts use the established runtime-free semantic
    # projection because measured timings are intentionally excluded.
    if value.get("semantic_digest") is not None and value.get("semantic_digest") != expected:
        from .rq4_physical import _runtime_free

        expected = semantic_digest(_runtime_free(_without_digest(value)))
    stored = value.get("semantic_digest")
    if stored is not None and stored != expected:
        raise ValueError(f"source semantic digest mismatch: {relative}")
    return {
        "path": relative,
        "semantic_digest": stored or expected,
        "sha256": sha256_file(path),
    }


def _method_memberships(design: dict[str, Any]) -> tuple[list[str], dict[str, list[str]]]:
    if design["format_version"] == "rq4-design-evaluation-v1":
        raw_methods = design["comparison"]["methods"]
        order = design["comparison"]["method_order"]
    else:
        raw_methods = design["methods"]
        order = design["method_order"]
    memberships: dict[str, list[str]] = {}
    for method in order:
        canonical_method = method
        source_method = method
        if method == "dependency-correlation-top-k":
            canonical_method = "native-payload-size-top-k"
        if method == "native-payload-size-top-k" and method not in raw_methods:
            source_method = "dependency-correlation-top-k"
        item = raw_methods[source_method]
        ids = item.get("selected_membership")
        if ids is None:
            ids = item.get("deployment_order")
        if not isinstance(ids, list) or not ids:
            raise ValueError(f"missing fixed membership for {method}")
        memberships[canonical_method] = list(ids)
    if set(memberships) != set(METHOD_ORDER):
        raise ValueError("fixed-k method roster does not match stability protocol")
    return list(METHOD_ORDER), memberships


def _candidate_definitions(
    design: dict[str, Any], memberships: dict[str, list[str]]
) -> dict[str, dict[str, Any]]:
    candidates = {
        item["candidate_id"]: item for item in design["eligible_universe"]["eligible_candidates"]
    }
    selected = {candidate_id for ids in memberships.values() for candidate_id in ids}
    missing = selected - set(candidates)
    if missing:
        raise ValueError(f"selected candidates absent from eligible universe: {sorted(missing)}")
    return {candidate_id: candidates[candidate_id] for candidate_id in sorted(selected)}


def _dataset_binding(root: Path, dataset_id: str) -> dict[str, Any]:
    source = DATASET_SOURCES[dataset_id]
    design = _read(root, source["design"])
    shared = _read(root, source["shared"])
    freeze = _read(root, source["freeze"])
    _, memberships = _method_memberships(design)
    definitions = _candidate_definitions(design, memberships)
    if design.get("dataset_id", design.get("dataset", {}).get("dataset_id")) != dataset_id:
        raise ValueError(f"dataset identity mismatch in {dataset_id}")
    system_digest = semantic_digest(_without_digest(freeze))
    if design.get("system_freeze_semantic_digest") != system_digest:
        raise ValueError(f"system freeze mismatch in {dataset_id}")
    shared_stats = shared.get("shared_realization", {})
    targets = {
        int(item["statistics_target"])
        for item in shared_stats.get("physical_statistics", [])
        if "statistics_target" in item
    }
    if targets != {100}:
        raise ValueError(f"shared source does not prove statistics target 100 for {dataset_id}")
    truth = design.get("truth_binding", {})
    workload = design.get("workload", {})
    if not workload:
        workload = {"workload_id": design["workload_id"], "query_count": design["query_count"]}
    if workload.get("query_count", truth.get("query_count")) != 10000:
        raise ValueError(f"fixed-k source query count is not 10000 for {dataset_id}")
    return {
        "dataset_id": dataset_id,
        "comparability_stratum": source["stratum"],
        "source_method_aliases": source["method_alias"],
        "design_artifact": _source_ref(root, source["design"]),
        "ablation_artifact": _source_ref(root, source["ablation"]),
        "shared_realization_artifact": _source_ref(root, source["shared"]),
        "system_freeze_artifact": _source_ref(root, source["freeze"]),
        "research_commit_sha": design.get("research_commit_sha"),
        "system_freeze_semantic_digest": system_digest,
        "stock_postgresql": {
            "commit_sha": freeze["stock_postgresql"]["source_commit_sha"]
            if "source_commit_sha" in freeze["stock_postgresql"]
            else freeze["stock_postgresql"]["commit_sha"],
            "postgres_version": freeze["stock_postgresql"]["postgres_version"],
        },
        "workload": {
            "workload_id": workload["workload_id"],
            "query_count": workload["query_count"],
        },
        "truth_binding": {
            key: truth[key]
            for key in (
                "authoritative_observations_semantic_digest",
                "authoritative_observations_sha256",
                "bound_ground_truth_set_semantic_digest",
                "cardinality_vector_digest",
                "dataset_identity",
                "policy_semantic_digest",
                "source_kind",
                "workload_id",
            )
            if key in truth
        },
        "statistics_target": 100,
        "fixed_k": int(design["fixed_k"]),
        "method_order": list(METHOD_ORDER),
        "method_memberships": memberships,
        "candidate_definitions": definitions,
        "selection_status": {
            method: (
                design["methods"].get(method, {}).get("status")
                if design["format_version"] != "rq4-design-evaluation-v1"
                else design["comparison"]["methods"]
                .get(source["method_alias"].get(method, method), {})
                .get("termination_status")
            )
            for method in METHOD_ORDER
        },
        "historical_selection_is_not_recomputed": True,
        "shared_source_analyze_count": shared_stats.get("analyze_count"),
    }


def build_protocol(root: Path, producer_sha: str) -> dict[str, Any]:
    datasets = {
        dataset_id: _dataset_binding(root, dataset_id)
        for dataset_id in (*PRIMARY_DATASETS, *SENSITIVITY_DATASETS)
    }
    value: dict[str, Any] = {
        "format_version": PROTOCOL_FORMAT,
        "experiment_id": PROTOCOL_ID,
        "status": "preregistered-not-executed",
        "producer_sha": producer_sha,
        "scientific_questions": {
            "SQ1": "For fixed membership, quantify native ANALYZE payload, ordinary-statistics, Plan Rows and q-error variability across independent realizations.",
            "SQ2": "Within each shared realization, assess whether method rankings remain stable.",
            "SQ3": "Assess whether realization-induced differences change the interpretation of RQ4b physical comparisons.",
        },
        "admissible_claims": [
            "descriptive operational variability under independent stock PostgreSQL ANALYZE realizations",
            "within-realization method ranking stability under shared native realization",
        ],
        "prohibited_claims": [
            "deterministic ANALYZE reproducibility",
            "extended-statistics-only causal decomposition when ordinary statistics also vary",
            "global method superiority or broad probability guarantees from five realizations",
        ],
        "datasets": {
            "primary": list(PRIMARY_DATASETS),
            "sensitivity_extension": list(SENSITIVITY_DATASETS),
            "cross_dataset_pooling": "prohibited; report dataset-scoped strata because Forest10 retains system-freeze-v1",
        },
        "method_roster": list(METHOD_ORDER),
        "realizations": {
            "count": 5,
            "ids": [f"realization-{index:02d}" for index in range(1, 6)],
            "independence": "fresh stock parent state and exactly one native ANALYZE per realization",
            "setseed_policy": "may be recorded where required by source setup; never treated as ANALYZE reproducibility control",
        },
        "source_bindings": {
            "system_freeze_v2": _source_ref(root, "paper/system-freeze-v2.json"),
            "rq4_fixed_k_summary": _source_ref(
                root, "experiments/rq4-fixed-k-cross-dataset-summary-v1.json"
            ),
            "time_budget_audit": _source_ref(
                root, "experiments/rq4-time-budget-fairness-audit-v1.json"
            ),
        },
        "dataset_bindings": datasets,
        "realization_design": {
            "parent": [
                "fresh declared physical data snapshot",
                "create union of all selected definitions in lexicographic candidate_id order, matching rq4_physical.union_selected_memberships",
                "record actual object definitions, OIDs, relative OID order, payload bytes/digests and ordinary-statistics fingerprint",
                "run exactly one stock ANALYZE",
            ],
            "method_clones": [
                "clone analyzed parent",
                "drop only unselected statistics",
                "run no ANALYZE after DROP",
                "verify retained payload and ordinary-statistics fingerprints against parent",
                "evaluate identical workload and truth mapping",
            ],
        },
        "primary_estimand": "combined operational impact of independent native ANALYZE realizations under otherwise fixed conditions",
        "secondary_diagnostics": [
            "extended-statistics payload variation",
            "ordinary-statistics fingerprint variation",
            "relative OID/order variation",
            "method ranking and paired q-error differences",
        ],
        "metrics": {
            "per_method_realization": [
                "mean",
                "p50",
                "p95",
                "p99",
                "max",
                "changed_plan_rows_fraction",
            ],
            "ranking": ["within_realization_rank", "first_or_tie_count", "pairwise_win_tie_loss"],
            "paired": [
                "greedy_minus_singleton_mean_qerror",
                "paired_query_improved_unchanged_worsened",
            ],
            "across_realizations": [
                "min",
                "max",
                "descriptive_standard_deviation",
                "payload_fingerprint_difference_fraction",
                "ordinary_fingerprint_difference",
            ],
            "definitions": {
                "qerror": "qerror-cardinality-floor-1-v1",
                "lower_is_better": True,
                "query_weighting": "the frozen RQ4 workload weighting within each realization",
                "independence_unit": "ANALYZE realization, not query",
            },
        },
        "failure_rules": [
            "stop and preserve append-only failure evidence for missing definitions, payloads, kind mismatch, workload/truth mismatch, cleanup failure or source revision mismatch",
            "stop or qualify if actual OID/relative order cannot be observed; do not infer order from CREATE statement sequence",
            "classify sibling ordinary-statistics or retained-payload mismatch as invalid-control-evidence",
            "never replace a failed realization without recording its failure and applying a predeclared exclusion rule",
            "no formal result is complete unless every declared method arm and every required query observation is present",
        ],
        "raw_artifacts": [
            "dataset/realization manifest",
            "parent and clone catalog controls",
            "payload fingerprints and ordinary-statistics fingerprints",
            "per-query Plan Rows/truth/q-error records",
            "ANALYZE and EXPLAIN accounting",
            "append-only failure/cleanup record",
        ],
        "execution_boundary": {
            "formal_execution_during_preregistration": False,
            "rq1b_test_access": "not used by this protocol; only the RQ4 fixed-k workload/truth binding may be used after formal authorization",
            "formal_invocation_count": 0,
        },
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def build_decision(root: Path, producer_sha: str, decision_date: str) -> dict[str, Any]:
    audit = _source_ref(root, "experiments/rq4-time-budget-fairness-audit-v1.json")
    value: dict[str, Any] = {
        "format_version": DECISION_FORMAT,
        "decision_id": DECISION_ID,
        "decision_date": decision_date,
        "decision_authority": "research-team",
        "former_planned_experiment": {
            "experiment_id": "rq4-fixed-evaluation-budget-ablations",
            "prior_status": "implementation-needed",
            "protocol_identity": "paper/paper-experiment-v1.json::rq4-fixed-evaluation-budget-ablations",
            "historical_protocol_preserved": True,
        },
        "lifecycle_disposition": "cancelled-by-research-decision",
        "decision_producer_sha": producer_sha,
        "source_audit": audit,
        "reasoning": [
            "The 300-second boundary is meaningful within Greedy/K_s search scope.",
            "It does not establish equal end-to-end selection expenditure across methods.",
            "Configuration-objective evaluations are not a uniform unit of PostgreSQL planner work.",
            "Existing fixed-k evidence supports descriptive cost-quality comparisons with explicit qualifications.",
            "A 2,000-evaluation cap alone would not establish resource-normalized fairness.",
        ],
        "claims_remaining_supported": [
            "qualified fixed-k quality comparisons",
            "qualified K_s screening-width quality/cost observations",
            "search-scope timing and recorded planner-call accounting where available",
        ],
        "claims_not_supported": [
            "equal total computational expenditure across methods",
            "end-to-end resource-normalized superiority",
            "results from a fixed-evaluation-budget comparison",
        ],
        "results": {
            "fixed_evaluation_budget_results_generated": False,
            "formal_invocations": 0,
            "historical_artifacts_modified": False,
        },
        "reconsideration_conditions": [
            "a new versioned study defines a comparable resource vector beyond configuration count",
            "planner calls, preprocessing, profiling, backend time and final evaluation are measured for every method",
            "the revised protocol receives independent methodological review before execution",
        ],
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def build_readiness(root: Path, protocol: dict[str, Any], producer_sha: str) -> dict[str, Any]:
    value: dict[str, Any] = {
        "format_version": READINESS_FORMAT,
        "review_id": READINESS_ID,
        "review_producer_sha": producer_sha,
        "protocol": {
            "path": "paper/native-analyze-stability-protocol-v1.json",
            "semantic_digest": protocol["semantic_digest"],
        },
        "status": "readiness-review-complete-formal-execution-not-authorized",
        "formal_execution": {
            "authorized": False,
            "invocation_count": 0,
            "results_available": False,
        },
        "source_coverage": {
            "fixed_k_summary": _source_ref(
                root, "experiments/rq4-fixed-k-cross-dataset-summary-v1.json"
            ),
            "time_budget_audit": _source_ref(
                root, "experiments/rq4-time-budget-fairness-audit-v1.json"
            ),
            "system_freeze_v2": _source_ref(root, "paper/system-freeze-v2.json"),
        },
        "comparability_findings": {
            "forest10": "historical system-freeze-v1 and dependency-correlation identifier; preserve as a dataset-scoped stratum and bind physical definitions from its own artifacts",
            "census13_dmv11": "current system-freeze-v2, fixed-k v2 evidence; Greedy memberships remain budget-censored where recorded and are not reselected",
            "cross_dataset_candidate_ids": "not assumed comparable; only within-dataset physical definitions are bound",
            "common_stock_contract": "PostgreSQL 16.14 and stock source commit are common; system-freeze lineage remains qualified",
        },
        "reusable_implementation": {
            "rq4_physical.build_shared_stock_realization": "one shared union ANALYZE and no-ANALYZE clones",
            "rq4_physical.validate_shared_stock_realization": "single-realization freshness, payload preservation and query-count checks",
            "rq4_postgres": "existing catalog/planner and physical evidence helpers",
        },
        "missing_features": [
            "five-realization orchestrator with unique realization and arm identities",
            "cross-realization variance/ranking aggregation and validator",
            "explicit observed OID relative-order validation across sibling arms",
            "append-only partial-realization and cleanup failure provenance",
            "formal full-workload completion gate for all method arms",
        ],
        "gates": {
            "protocol_validated_offline": True,
            "source_memberships_bound": True,
            "shared_single_realization_reusable": True,
            "five_realization_orchestrator_ready": False,
            "oid_order_control_ready": False,
            "payload_and_ordinary_cross_realization_validator_ready": False,
            "integration_tested": False,
            "formal_execution_authorized": False,
        },
        "resource_estimate": {
            "measured": [
                "existing RQ4 physical children use 1 shared ANALYZE and 9 method arms per dataset",
                "existing fixed-k physical evaluation accounts for 10,000 planner query calls per method arm",
            ],
            "extrapolated": {
                "primary_datasets": 3,
                "realizations_per_dataset": 5,
                "parent_analyze_operations": 15,
                "method_arms": 135,
                "full_workload_explain_calls": 1350000,
                "power7_extension_parent_analyze_operations": 5,
                "power7_extension_method_arms": 45,
                "power7_extension_explain_calls": 450000,
            },
            "dominant_stages": [
                "stock EXPLAIN evaluation",
                "database cloning/storage",
                "native ANALYZE",
            ],
            "workload_scope": "10,000 RQ4 fixed-k queries per method arm; no diagnostic shrinkage is authorized by this readiness record",
        },
        "implementation_plan": [
            "stage-1: offline protocol-bound harness and append-only validators",
            "stage-2: synthetic/mocked correctness tests for union, OID/order controls, payload equality, and failure semantics",
            "stage-3: separately authorized isolated integration smoke",
            "stage-4: separately authorized formal execution",
        ],
        "unresolved_blockers": [
            "No formal execution is authorized by this artifact.",
            "The existing single-realization executor is not an implementation of the five-realization study.",
            "A formal run must add observed OID/order and append-only realization-level controls before execution.",
        ],
    }
    value["semantic_digest"] = semantic_digest(value)
    return value


def _validate_digest(value: dict[str, Any], label: str) -> None:
    if value.get("semantic_digest") != semantic_digest(_without_digest(value)):
        raise ValueError(f"{label} semantic digest mismatch")


def validate_decision(value: dict[str, Any], root: Path) -> dict[str, str]:
    if value.get("format_version") != DECISION_FORMAT or value.get("decision_id") != DECISION_ID:
        raise ValueError("unsupported fixed-evaluation-budget decision")
    _validate_digest(value, "decision")
    if value.get("lifecycle_disposition") != "cancelled-by-research-decision":
        raise ValueError("fixed-evaluation-budget disposition is not cancellation")
    if value.get("results", {}).get("fixed_evaluation_budget_results_generated") is not False:
        raise ValueError("decision claims fixed-evaluation-budget results")
    source = value.get("source_audit", {})
    actual = _source_ref(root, source.get("path", ""))
    if (
        source.get("semantic_digest") != actual["semantic_digest"]
        or source.get("sha256") != actual["sha256"]
    ):
        raise ValueError("decision source audit binding mismatch")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def validate_protocol(value: dict[str, Any], root: Path) -> dict[str, str]:
    if value.get("format_version") != PROTOCOL_FORMAT or value.get("experiment_id") != PROTOCOL_ID:
        raise ValueError("unsupported native ANALYZE stability protocol")
    _validate_digest(value, "protocol")
    if value.get("status") != "preregistered-not-executed":
        raise ValueError("protocol is not preregistered and unexecuted")
    if value.get("execution_boundary", {}).get("formal_invocation_count") != 0:
        raise ValueError("native stability protocol records a formal invocation")
    if value.get("realizations", {}).get("count") != 5:
        raise ValueError("native stability realization count drifted")
    if tuple(value.get("method_roster", ())) != METHOD_ORDER:
        raise ValueError("native stability method roster drifted")
    for source in value.get("source_bindings", {}).values():
        actual = _source_ref(root, source["path"])
        if source != actual:
            raise ValueError(f"protocol source binding mismatch: {source['path']}")
    for dataset_id, binding in value.get("dataset_bindings", {}).items():
        if dataset_id not in DATASET_SOURCES:
            raise ValueError(f"unknown stability dataset: {dataset_id}")
        expected = _dataset_binding(root, dataset_id)
        if binding != expected:
            raise ValueError(f"dataset binding drifted: {dataset_id}")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


def validate_readiness(
    value: dict[str, Any], protocol: dict[str, Any], root: Path
) -> dict[str, str]:
    if value.get("format_version") != READINESS_FORMAT or value.get("review_id") != READINESS_ID:
        raise ValueError("unsupported native stability readiness review")
    _validate_digest(value, "readiness review")
    if value.get("protocol", {}).get("semantic_digest") != protocol.get("semantic_digest"):
        raise ValueError("readiness does not bind the protocol")
    if value.get("formal_execution", {}).get("invocation_count") != 0:
        raise ValueError("readiness records a formal invocation")
    if value.get("formal_execution", {}).get("authorized") is not False:
        raise ValueError("readiness authorizes formal execution")
    if value.get("gates", {}).get("five_realization_orchestrator_ready") is not False:
        raise ValueError("readiness overstates implementation readiness")
    for source in value.get("source_coverage", {}).values():
        actual = _source_ref(root, source["path"])
        if source != actual:
            raise ValueError(f"readiness source binding mismatch: {source['path']}")
    return {"status": "valid", "semantic_digest": value["semantic_digest"]}


__all__ = [
    "DECISION_FORMAT",
    "PROTOCOL_FORMAT",
    "READINESS_FORMAT",
    "build_decision",
    "build_protocol",
    "build_readiness",
    "validate_decision",
    "validate_protocol",
    "validate_readiness",
]
