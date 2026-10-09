import copy
import inspect
import json
from importlib.util import find_spec
from pathlib import Path

import pytest

try:
    _artifact_spec = find_spec("extstats_advisor.deployment.artifact")
    if _artifact_spec is None:
        _HAS_FROZEN_V2_ARTIFACT_API = False
    else:
        from extstats_advisor.deployment import artifact as _deployment_artifact

        _HAS_FROZEN_V2_ARTIFACT_API = "selected_candidate_count" in inspect.getsource(
            _deployment_artifact.deployment_result_summary
        )
except ModuleNotFoundError:  # Historical Advisor CI intentionally has no frozen-v2 API.
    _HAS_FROZEN_V2_ARTIFACT_API = False
if not _HAS_FROZEN_V2_ARTIFACT_API:
    pytestmark = pytest.mark.skip(
        reason="RQ1b synthesis validation requires frozen-v2 DeploymentResult API"
    )

from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq1_workload_generalization_live import RQ1BValidationError
from extstats_advisor_research.rq1b_synthesis import (
    DATASET_ORDER,
    SPECS,
    SUMMARY_FORMAT,
    validate_cross_dataset_synthesis,
    validate_observation_alignment,
)

ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "experiments/rq1-workload-generalization-cross-dataset-v1.json"


def _summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def test_published_rq1b_synthesis_recomputes_all_children_offline():
    value = _summary()
    assert value["format_version"] == SUMMARY_FORMAT
    assert [row["dataset_id"] for row in value["datasets"]] == list(DATASET_ORDER)
    assert validate_cross_dataset_synthesis(SUMMARY, ROOT)["status"] == "valid"


def test_synthesis_binds_frozen_result_digests_and_populations():
    value = _summary()
    assert [row["published_result_digest"] for row in value["datasets"]] == [
        "9cb84a6eaa1f35f3761f90853a519de36be6448f9eafe3db389c5e27acffb41b",
        "106809bb0ad45ee6aa0ac0a238eb22299a17e0f4d396e883ef68718d8234d8dd",
        "1af0a8d65a78f6c6c19e63bcf2a8f1f8ab39f6a9a24dc0f00a8266e44508e9a3",
        "f7aabb8ac7839457cb49bd3dd21e57e482a9967141ef45174293b5c06422b53c",
    ]
    assert [row["strict_unseen"]["query_count"] for row in value["datasets"]] == [
        9386,
        10000,
        10000,
        8138,
    ]
    assert all(row["validation"]["evidence_eligible"] for row in value["datasets"])


def test_summary_rejects_digest_and_dataset_mutations(tmp_path):
    value = _summary()
    value["datasets"][0]["published_result_digest"] = "0" * 64
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    path = tmp_path / "mutated.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RQ1BValidationError, match="does not match recomputed evidence"):
        validate_cross_dataset_synthesis(path, ROOT)

    value = _summary()
    value["datasets"][0]["dataset_id"] = "arecel-forest10"
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RQ1BValidationError, match="does not match recomputed evidence"):
        validate_cross_dataset_synthesis(path, ROOT)


def test_observation_alignment_rejects_missing_duplicate_and_truth_drift():
    # Use the published Power7 rows; the validator must fail before any live path.
    per_query = (
        ROOT
        / "experiments/arecel-power7/rq1-workload-generalization-v1/pg16-advisor-valid-to-test-per-query-v1.jsonl"
    )
    rows = [json.loads(line) for line in per_query.read_text().splitlines()]
    baseline = json.loads((ROOT / SPECS["arecel-power7"].baseline_path).read_text())
    arms = {
        arm: baseline["per_arm"][arm]["per_query"] for arm in ("pg16-default", "pg16-target10000")
    }
    with pytest.raises(RQ1BValidationError):
        validate_observation_alignment(rows[:-1], arms, SPECS["arecel-power7"])
    duplicate = rows.copy()
    duplicate[1] = copy.deepcopy(duplicate[0])
    with pytest.raises(RQ1BValidationError):
        validate_observation_alignment(
            duplicate, arms, SPECS["power7"] if "power7" in SPECS else SPECS["arecel-power7"]
        )
    mismatched = {key: [dict(row) for row in value] for key, value in arms.items()}
    mismatched["pg16-default"][0]["truth"] += 1
    with pytest.raises(RQ1BValidationError):
        validate_observation_alignment(rows, mismatched, SPECS["arecel-power7"])


def test_identifier_comparison_is_not_claimed_as_physical_definition_comparison():
    value = _summary()
    for row in value["datasets"]:
        comparison = row["recommendation_comparison"]
        assert comparison["comparison_level"] == "identifier-only"
        assert comparison["physical_definition_comparison"]["status"] == "not-derived"
