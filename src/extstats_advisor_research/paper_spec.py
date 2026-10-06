"""Lightweight semantic checks for the versioned paper experiment protocol."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PAPER_SPECIFICATION = "paper-experiment-v1"
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
    "complete",
    "superseded",
}
RESEARCH_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPEC_PATH = RESEARCH_ROOT / "paper" / "paper-experiment-v1.json"


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
        "rq3-primary-mechanism-fidelity",
        "rq3-secondary-build-sanity",
        "rq5-cost-accounting",
    }
    for experiment_id in exact_arecel_ids:
        experiment = by_id.get(experiment_id)
        if experiment is None or experiment.get("datasets") != list(ARECEL_DATASETS):
            raise ValueError(f"{experiment_id} must list exactly the four AreCEL datasets")

    primary = by_id.get("rq3-primary-mechanism-fidelity")
    if primary is not None:
        fixture = primary.get("validation_fixture")
        if fixture not in SYNTHETIC_FIXTURES:
            raise ValueError("RQ3 primary implementation fixture is not declared separately")
        if fixture in primary.get("datasets", []):
            raise ValueError("RQ3 implementation fixture must not be in confirmatory datasets")
        gate = primary.get("implementation_gate")
        if not isinstance(gate, dict) or not gate.get("artifact_format"):
            raise ValueError("RQ3 primary implementation gate is missing")

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
    system_under_test = freeze_gate.get("system_under_test")
    if not isinstance(system_under_test, dict):
        raise TypeError("paper specification is missing frozen system_under_test")
    if system_under_test.get("format_version") != "system-freeze-v1":
        raise ValueError("paper specification must reference system-freeze-v1")
    if system_under_test.get("status") != "frozen":
        raise ValueError("paper specification system_under_test must be frozen")
    if system_under_test.get("path") != "paper/system-freeze-v1.json":
        raise ValueError("paper specification has the wrong system-freeze-v1 path")
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
