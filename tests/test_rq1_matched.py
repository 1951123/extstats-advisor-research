from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq1_canary import validate_rq1_artifact
from extstats_advisor_research.rq1_matched import _external_truth, run_rq1_matched
from extstats_advisor_research.rq1_rebind import validate_rebound_artifact

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json"
FOREST_CANONICAL = (
    ROOT / "experiments/arecel-forest10/rq1-confirmatory/rq1-matched-comparison-v1.json"
)
HISTORICAL = ROOT / "experiments/arecel-census13/rq1-canary/rq1-matched-comparison-v1.json"


def _read(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_census_rebound_preserves_all_metrics_and_rebinds_truth() -> None:
    old = _read(HISTORICAL)
    new = _read(CANONICAL)
    assert validate_rebound_artifact(CANONICAL)["status"] == "valid"
    assert new["truth"]["source_kind"] == "authoritative-external-exact"
    assert new["provenance"]["planner_reexecuted"] is False
    assert new["provenance"]["exact_counts_reexecuted"] is False
    for arm_id in old["arms"]:
        assert new["per_arm"][arm_id]["summary"] == old["per_arm"][arm_id]["summary"]
        assert new["per_arm"][arm_id]["per_query"] == old["per_arm"][arm_id]["per_query"]
    assert new["paired_comparison"] == old["paired_comparison"]


def test_census_truth_rebind_rejects_provenance_only_policy(tmp_path: Path, monkeypatch) -> None:
    from extstats_advisor_research import rq1_rebind

    policy_path = ROOT / "paper/benchmark-truth-policy-v1.json"
    original_read_json = rq1_rebind.read_json

    def read_json_with_provenance_only(path: Path) -> dict[str, object]:
        value = original_read_json(path)
        if path == policy_path:
            value = copy.deepcopy(value)
            census = next(
                item for item in value["datasets"] if item["dataset_id"] == "arecel-census13"
            )
            census["status"] = "validated-provenance"
        return value

    monkeypatch.setattr(rq1_rebind, "read_json", read_json_with_provenance_only)
    with pytest.raises(ValueError, match="provenance validation"):
        rq1_rebind.canonicalize_census13(
            historical_artifact=HISTORICAL,
            observations_path=ROOT
            / "truth/arecel/census13/authoritative-cardinality-observations-v1.json",
            equivalence_artifact=ROOT
            / "experiments/arecel-census13/arecel-truth-equivalence-v1.json",
            policy_path=policy_path,
            output=tmp_path / "rebound.json",
        )


def test_rq1_generic_runner_rejects_unknown_dataset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unsupported RQ1 dataset"):
        run_rq1_matched(
            dataset_id="arecel-unknown",
            stock_dsn="unused",
            patched_dsn="unused",
            output=tmp_path / "result.json",
        )


def test_rq1_rejects_truth_identity_mismatch_between_arms() -> None:
    artifact = _read(CANONICAL)
    broken = copy.deepcopy(artifact)
    broken["per_arm"]["pg16-target10000"]["truth_identity"]["observations_sha256"] = "0" * 64
    broken["semantic_digest"] = artifact["semantic_digest"]
    with pytest.raises(ValueError, match="truth identity differs"):
        validate_rq1_artifact(broken)


def test_dataset_complete_requires_verified_stock_deployment() -> None:
    artifact = _read(CANONICAL)
    broken = copy.deepcopy(artifact)
    broken["per_arm"]["pg16-advisor"]["deployment_result"]["verified"] = False
    with pytest.raises(ValueError, match="verified stock deployment"):
        validate_rq1_artifact(broken)


def test_dataset_complete_requires_current_dataset_progress_complete() -> None:
    artifact = _read(FOREST_CANONICAL)
    broken = copy.deepcopy(artifact)
    broken["dataset_progress"]["arecel-forest10"] = "planned"
    with pytest.raises(ValueError, match="dataset progress complete"):
        validate_rq1_artifact(broken)


def test_power7_artifact_records_only_requested_dataset_run() -> None:
    power = _read(
        ROOT / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json"
    )
    assert power["dataset_progress"] == {
        "arecel-census13": "complete",
        "arecel-dmv11": "planned",
        "arecel-forest10": "complete",
        "arecel-power7": "complete",
    }
    assert power["provenance"]["unrequested_dataset_runs"] == []
    assert "power7_and_dmv11_run" not in power["provenance"]


def test_confirmatory_parameter_basis_is_dataset_specific() -> None:
    power = _read(
        ROOT / "experiments/arecel-power7/rq1-confirmatory/rq1-matched-comparison-v1.json"
    )
    dmv = _read(ROOT / "experiments/arecel-dmv11/rq1-confirmatory/rq1-matched-comparison-v1.json")
    assert power["provenance"]["parameter_selection_basis"] == (
        "pre-existing Power7 canonical K=8 protocol"
    )
    assert dmv["provenance"]["parameter_selection_basis"] == (
        "pre-existing DMV11 canonical K=8 protocol"
    )


def test_external_truth_import_fails_closed_without_observations(
    tmp_path: Path, monkeypatch
) -> None:
    from extstats_advisor_research import rq1_matched

    missing = tmp_path / "missing-observations.json"
    monkeypatch.setattr(
        rq1_matched,
        "authoritative_truth_spec",
        lambda dataset_id, research_root: {
            "observations_path": missing,
            "observations_sha256": "a" * 64,
            "authority": "test",
            "dataset_identity": "dataset",
            "source_revision": "revision",
            "policy_status": "validated-provenance",
        },
    )
    with pytest.raises(FileNotFoundError):
        _external_truth("arecel-forest10", ROOT)
