from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.rq1_canary import validate_rq1_artifact
from extstats_advisor_research.rq1_matched import run_rq1_matched
from extstats_advisor_research.rq1_rebind import validate_rebound_artifact

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "experiments/arecel-census13/rq1-confirmatory/rq1-matched-comparison-v1.json"
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
