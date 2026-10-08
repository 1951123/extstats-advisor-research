"""Lightweight semantic checks for the versioned paper experiment protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provenance import semantic_digest

PAPER_SPECIFICATION = "paper-experiment-v1"
CURRENT_SYSTEM_FREEZE_FORMAT = "system-freeze-v2"
ARECEL_DATASETS = (
    "arecel-census13",
    "arecel-forest10",
    "arecel-power7",
    "arecel-dmv11",
)
SYNTHETIC_FIXTURES = {"rq3-synthetic-mcv-fd-v1", "rq3-synthetic-build-sanity-v1"}
ALLOWED_STATUSES = {
    "pilot",
    "planned",
    "implementation-needed",
    "ready-to-run",
    "preregistered",
    "complete",
    "superseded",
}
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = RESEARCH_ROOT / "paper" / "paper-experiment-v1.json"
TOP_K_PROTOCOL_IDENTITY = "top-k-screening-protocol-v2"
TOP_K_PROTOCOL_PATH = RESEARCH_ROOT / "paper" / "top-k-screening-protocol-v2.json"


def validate_top_k_screening_protocol(value: Any) -> dict[str, Any]:
    """Validate the preregistered K_s protocol and its content digest."""

    if (
        not isinstance(value, dict)
        or value.get("specification_identity") != TOP_K_PROTOCOL_IDENTITY
    ):
        raise ValueError("unsupported top-k screening protocol")
    if value.get("status") != "preregistered":
        raise ValueError("top-k screening protocol must remain preregistered")
    if value.get("formal_sweep_executed") is not False:
        raise ValueError("top-k screening protocol cannot claim a formal sweep")
    if value.get("grid") != [4, 8, 16, 32, "all"]:
        raise ValueError("top-k screening grid drifted")
    if value.get("fixed_B") != 4:
        raise ValueError("top-k screening B drifted")
    if value.get("search_wall_clock_seconds") != 300.0:
        raise ValueError("top-k screening wall cap drifted")
    if value.get("max_configuration_objective_evaluations") != 2000:
        raise ValueError("top-k screening evaluation cap drifted")
    digest = value.get("semantic_digest")
    expected = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    if digest != expected:
        raise ValueError("top-k screening protocol semantic digest mismatch")
    return {
        "status": "valid",
        "specification_identity": TOP_K_PROTOCOL_IDENTITY,
        "semantic_digest": expected,
    }


def load_top_k_screening_protocol(path: Path = TOP_K_PROTOCOL_PATH) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    validate_top_k_screening_protocol(value)
    return value


def load_paper_spec(path: Path = DEFAULT_SPEC_PATH) -> dict[str, Any]:
    """Load and semantically validate one paper-experiment-v1 document."""

    value = json.loads(path.read_text(encoding="utf-8"))
    validate_paper_spec(value)
    return value


def _nonempty_string_list(value: Any, label: str) -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{label} must be a non-empty list of strings")


def validate_paper_spec(spec: Any) -> dict[str, Any]:
    """Validate cross-field protocol invariants without a JSON-Schema dependency."""

    if not isinstance(spec, dict):
        raise TypeError("paper experiment specification must be an object")
    if spec.get("specification_identity") != PAPER_SPECIFICATION:
        raise ValueError("unsupported paper experiment specification")
    ledger = spec.get("status_ledger")
    if not isinstance(ledger, dict):
        raise TypeError("paper specification is missing status_ledger")
    allowed = ledger.get("allowed_statuses")
    if not isinstance(allowed, list) or set(allowed) != ALLOWED_STATUSES:
        raise ValueError("status_ledger.allowed_statuses does not match the frozen vocabulary")

    experiments = spec.get("experiments")
    if not isinstance(experiments, list) or not experiments:
        raise ValueError("paper specification needs experiments")
    experiment_ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    for experiment in experiments:
        if not isinstance(experiment, dict):
            raise TypeError("each paper experiment must be an object")
        experiment_id = experiment.get("experiment_id")
        if not isinstance(experiment_id, str) or not experiment_id:
            raise ValueError("each paper experiment needs a non-empty experiment_id")
        if experiment_id in experiment_ids:
            raise ValueError(f"duplicate experiment ID: {experiment_id}")
        experiment_ids.add(experiment_id)
        by_id[experiment_id] = experiment
        status = experiment.get("status")
        if status not in allowed:
            raise ValueError(f"experiment {experiment_id} has invalid status {status!r}")
        _nonempty_string_list(
            experiment.get("expected_paper_outputs"),
            f"experiment {experiment_id} expected_paper_outputs",
        )
        datasets = experiment.get("datasets", [])
        if not isinstance(datasets, list) or any(
            not isinstance(item, str) or not item for item in datasets
        ):
            raise ValueError(f"experiment {experiment_id} datasets must be a list of strings")
        if any(item in SYNTHETIC_FIXTURES for item in datasets) and experiment.get("rq") in {
            "RQ1",
            "RQ2a",
            "RQ2b",
            "RQ4",
            "RQ5",
        }:
            raise ValueError(
                f"synthetic fixture must not replace a confirmatory dataset in {experiment_id}"
            )

    datasets = spec.get("datasets")
    if not isinstance(datasets, list) or {item.get("dataset_id") for item in datasets} != set(
        ARECEL_DATASETS
    ):
        raise ValueError("paper specification must enumerate the four AreCEL datasets")
    for dataset in datasets:
        if dataset.get("truth_source") != "authoritative-external-exact from audited AreCEL labels":
            raise ValueError("AreCEL confirmatory truth source is not the audited external policy")
        if dataset.get("truth_policy") != "paper/benchmark-truth-policy-v1.json":
            raise ValueError("AreCEL dataset is missing benchmark-truth-policy-v1")

    exact_arecel_ids = {
        "rq1-confirmatory-matched-baselines",
        "rq2a-confirmatory-transfer",
        "rq2b-sample-full-utility",
        "rq5-cost-accounting",
    }
    for experiment_id in exact_arecel_ids:
        experiment = by_id.get(experiment_id)
        if experiment is None or experiment.get("datasets") != list(ARECEL_DATASETS):
            raise ValueError(f"{experiment_id} must list exactly the four AreCEL datasets")

    rq4 = by_id.get("rq4-fixed-k-ablations")
    if rq4 is not None:
        if rq4.get("methods") != [
            "random-k",
            "workload-frequency-top-k",
            "native-payload-size-top-k",
            "singleton-utility-top-k",
            "greedy-ADD",
        ]:
            raise ValueError("RQ4 fixed-k method registry is not canonical")
        gate = rq4.get("dependency_baseline_definition_gate")
        if not isinstance(gate, dict):
            raise ValueError("RQ4 baseline-definition gate is missing")
        if gate.get("historical_audit") != "blocked":
            raise ValueError("RQ4 historical blocker status was rewritten")
        if gate.get("resolution") != "resolved":
            raise ValueError("RQ4 baseline-definition resolution is missing")
        if gate.get("formal_execution_allowed") is not True:
            raise ValueError("RQ4 formal execution is not enabled after resolution")
        if gate.get("resolution_artifact") != (
            "experiments/rq4-dependency-baseline-definition-resolution-v1.json"
        ):
            raise ValueError("RQ4 resolution artifact path is not canonical")
        aliases = rq4.get("historical_method_aliases")
        if aliases != {"dependency-correlation-top-k": "native-payload-size-top-k"}:
            raise ValueError("RQ4 historical method alias is missing or incorrect")

    screening = by_id.get("rq4-ks-sensitivity")
    if screening is not None:
        if screening.get("protocol_identity") != "paper/top-k-screening-protocol-v2.json":
            raise ValueError("RQ4 K_s sensitivity protocol path is not canonical")
        if screening.get("datasets") != [
            "arecel-census13",
            "arecel-power7",
            "arecel-dmv11",
        ]:
            raise ValueError("RQ4 K_s sensitivity datasets are not canonical")
        if screening.get("excluded_datasets") != ["arecel-forest10"]:
            raise ValueError("RQ4 K_s sensitivity must exclude Forest10")
        if screening.get("grid") != [4, 8, 16, 32, "all"]:
            raise ValueError("RQ4 K_s sensitivity grid drifted")
        if screening.get("fixed_B") != 4:
            raise ValueError("RQ4 K_s sensitivity B drifted")
        if screening.get("search_wall_clock_seconds") != 300:
            raise ValueError("RQ4 K_s sensitivity wall cap drifted")
        if screening.get("max_configuration_objective_evaluations") != 2000:
            raise ValueError("RQ4 K_s sensitivity evaluation cap drifted")
        if screening.get("formal_arecel_runs") is not False:
            raise ValueError("RQ4 K_s sensitivity must remain unexecuted")
        protocol = load_top_k_screening_protocol()
        if screening.get("protocol_semantic_digest") != protocol["semantic_digest"]:
            raise ValueError("paper specification does not bind top-k protocol digest")

    primary = by_id.get("rq3-primary-mechanism-fidelity")
    if primary is not None:
        fixture = primary.get("execution_fixture")
        if fixture not in SYNTHETIC_FIXTURES:
            raise ValueError("RQ3 primary execution fixture is not declared")
        if primary.get("execution_scope") != "controlled-synthetic":
            raise ValueError("RQ3 primary execution scope must be controlled-synthetic")
        if primary.get("datasets") != [fixture]:
            raise ValueError("RQ3 primary datasets must identify only its synthetic fixture")
        gate = primary.get("implementation_gate")
        if not isinstance(gate, dict) or not gate.get("artifact_format"):
            raise ValueError("RQ3 primary implementation gate is missing")

    secondary = by_id.get("rq3-secondary-build-sanity")
    if secondary is not None:
        fixture = secondary.get("execution_fixture")
        if fixture != "rq3-synthetic-build-sanity-v1":
            raise ValueError("RQ3 secondary execution fixture is not declared")
        if secondary.get("execution_scope") != "controlled-synthetic":
            raise ValueError("RQ3 secondary execution scope must be controlled-synthetic")
        if secondary.get("datasets") != [fixture]:
            raise ValueError("RQ3 secondary datasets must identify only its synthetic fixture")

    entries = ledger.get("entries")
    if not isinstance(entries, list):
        raise TypeError("status_ledger.entries must be a list")
    ledger_ids: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise TypeError("each status ledger entry must be an object")
        experiment_id = entry.get("experiment_id")
        if experiment_id in ledger_ids:
            raise ValueError(f"duplicate status ledger experiment ID: {experiment_id}")
        ledger_ids.add(experiment_id)
        if entry.get("status") not in allowed:
            raise ValueError(f"status ledger has invalid status for {experiment_id}")
        experiment = by_id.get(experiment_id)
        if experiment is not None and experiment.get("status") != entry.get("status"):
            raise ValueError(
                f"experiment and status ledger status mismatch for {experiment_id}: "
                f"{experiment.get('status')!r} != {entry.get('status')!r}"
            )

    freeze_gate = spec.get("freeze_gate")
    if not isinstance(freeze_gate, dict):
        raise TypeError("paper specification is missing freeze_gate")
    if freeze_gate.get("status") != "sut-v2-frozen-research-per-experiment":
        raise ValueError("paper specification freeze gate must select system-freeze-v2")
    system_under_test = freeze_gate.get("system_under_test")
    if not isinstance(system_under_test, dict):
        raise TypeError("paper specification is missing frozen system_under_test")
    if system_under_test.get("format_version") != CURRENT_SYSTEM_FREEZE_FORMAT:
        raise ValueError("paper specification must reference system-freeze-v2")
    if system_under_test.get("status") != "frozen":
        raise ValueError("paper specification system_under_test must be frozen")
    if system_under_test.get("path") != "paper/system-freeze-v2.json":
        raise ValueError("paper specification has the wrong system-freeze-v2 path")
    historical_policy = freeze_gate.get("historical_artifact_freeze_policy")
    if not isinstance(historical_policy, dict):
        raise TypeError("paper specification is missing historical_artifact_freeze_policy")
    if historical_policy.get("historical_system_freeze") != "system-freeze-v1":
        raise ValueError("historical artifacts must remain bound to system-freeze-v1")
    if historical_policy.get("rule") != (
        "Existing RQ1 and Forest10 RQ4 artifacts retain their embedded system-freeze-v1 provenance and are not retroactively reassigned to v2."
    ):
        raise ValueError("historical artifact freeze policy is not explicit")
    research_identity = freeze_gate.get("research_harness_identity")
    if not isinstance(research_identity, dict):
        raise TypeError("paper specification is missing research_harness_identity")
    if research_identity.get("policy") != "per-experiment committed SHA":
        raise ValueError("research harness identity must be per-experiment committed SHA")

    return {
        "status": "valid",
        "specification_identity": PAPER_SPECIFICATION,
        "experiment_count": len(experiments),
        "experiment_ids": sorted(experiment_ids),
        "ledger_entry_count": len(entries),
    }
