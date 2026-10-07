from __future__ import annotations

import copy

import pytest

from extstats_advisor_research.paper_spec import (
    ARECEL_DATASETS,
    load_paper_spec,
    validate_paper_spec,
)


def test_current_spec_keeps_confirmatory_datasets_and_separates_fixture() -> None:
    spec = load_paper_spec()
    assert spec["freeze_gate"]["status"] == "sut-v2-frozen-research-per-experiment"
    assert spec["freeze_gate"]["system_under_test"]["format_version"] == "system-freeze-v2"
    assert spec["freeze_gate"]["system_under_test"]["path"] == "paper/system-freeze-v2.json"
    assert (
        spec["freeze_gate"]["historical_artifact_freeze_policy"]["historical_system_freeze"]
        == "system-freeze-v1"
    )
    experiments = {item["experiment_id"]: item for item in spec["experiments"]}
    for experiment_id in (
        "rq1-confirmatory-matched-baselines",
        "rq2a-confirmatory-transfer",
        "rq2b-sample-full-utility",
        "rq3-primary-mechanism-fidelity",
        "rq3-secondary-build-sanity",
        "rq5-cost-accounting",
    ):
        assert experiments[experiment_id]["datasets"] == list(ARECEL_DATASETS)
    assert (
        experiments["rq3-primary-mechanism-fidelity"]["validation_fixture"]
        not in experiments["rq3-primary-mechanism-fidelity"]["datasets"]
    )


def test_synthetic_fixture_in_rq1_is_rejected() -> None:
    spec = load_paper_spec()
    broken = copy.deepcopy(spec)
    broken["experiments"][0]["datasets"] = ["rq3-synthetic-mcv-fd-v1"]
    with pytest.raises(ValueError, match="synthetic fixture"):
        validate_paper_spec(broken)


def test_duplicate_id_invalid_status_and_empty_output_are_rejected() -> None:
    spec = load_paper_spec()
    duplicate = copy.deepcopy(spec)
    duplicate["experiments"].append(copy.deepcopy(duplicate["experiments"][0]))
    with pytest.raises(ValueError, match="duplicate experiment ID"):
        validate_paper_spec(duplicate)

    invalid_status = copy.deepcopy(spec)
    invalid_status["experiments"][0]["status"] = "not-a-status"
    with pytest.raises(ValueError, match="invalid status"):
        validate_paper_spec(invalid_status)

    empty_output = copy.deepcopy(spec)
    empty_output["experiments"][0]["expected_paper_outputs"] = []
    with pytest.raises(ValueError, match="expected_paper_outputs"):
        validate_paper_spec(empty_output)


def test_experiment_and_status_ledger_mismatch_is_rejected() -> None:
    spec = load_paper_spec()
    broken = copy.deepcopy(spec)
    entry = next(
        item
        for item in broken["status_ledger"]["entries"]
        if item["experiment_id"] == "rq3-secondary-build-sanity"
    )
    entry["status"] = "implementation-needed"
    with pytest.raises(ValueError, match="status ledger status mismatch"):
        validate_paper_spec(broken)
