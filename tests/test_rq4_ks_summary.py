from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import semantic_digest
from extstats_advisor_research.rq4_ks_summary import (
    SUMMARY_FORMAT,
    validate_cross_dataset_summary,
)

ROOT = Path(__file__).resolve().parents[1]
SUMMARY_PATH = ROOT / "experiments/rq4-ks-sensitivity-cross-dataset-summary-v1.json"


def _summary() -> dict:
    return json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))


def _redigest(value: dict) -> dict:
    value["semantic_digest"] = semantic_digest(
        {key: item for key, item in value.items() if key != "semantic_digest"}
    )
    return value


def test_summary_is_derived_from_three_immutable_children() -> None:
    artifact = _summary()
    assert validate_cross_dataset_summary(artifact, research_root=ROOT)["status"] == "valid"
    assert artifact["format_version"] == SUMMARY_FORMAT
    assert artifact["dataset_count"] == 3
    assert artifact["smallest_best_finite_widths"] == {
        "arecel-census13": 8,
        "arecel-power7": 16,
        "arecel-dmv11": 32,
    }
    assert artifact["datasets"]["arecel-power7"]["all_reference_semantics"] == (
        "completed-full-universe-greedy-reference"
    )
    assert artifact["datasets"]["arecel-census13"]["all_reference_semantics"] == (
        "bounded-full-universe-incumbent"
    )
    assert artifact["datasets"]["arecel-dmv11"]["all_reference_semantics"] == (
        "bounded-full-universe-incumbent"
    )


def test_summary_validator_rejects_child_digest_change() -> None:
    broken = copy.deepcopy(_summary())
    broken["datasets"]["arecel-power7"]["artifact_semantic_digest"] = "0" * 64
    with pytest.raises(ValueError, match="summary digest mismatch|does not match"):
        validate_cross_dataset_summary(broken, research_root=ROOT)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (
            ("datasets", "arecel-census13", "comparisons_vs_all", "8", "objective_delta_vs_all"),
            99.0,
        ),
        (
            ("datasets", "arecel-power7", "all_reference_semantics"),
            "bounded-full-universe-incumbent",
        ),
        (("datasets", "arecel-dmv11", "smallest_width_matching_best_finite_objective"), 8),
        (("datasets", "arecel-power7", "points"), []),
        (
            (
                "datasets",
                "arecel-dmv11",
                "comparisons_vs_all",
                "16",
                "planner_calls_reduction_vs_all",
            ),
            0.0,
        ),
    ],
)
def test_summary_validator_rejects_derived_mutations(path: tuple[str, ...], value: object) -> None:
    broken = copy.deepcopy(_summary())
    target = broken
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    _redigest(broken)
    with pytest.raises(ValueError, match="does not match immutable child-derived aggregation"):
        validate_cross_dataset_summary(broken, research_root=ROOT)


def test_summary_validator_rejects_fake_forest10_dataset() -> None:
    broken = copy.deepcopy(_summary())
    broken["datasets"]["arecel-forest10"] = copy.deepcopy(broken["datasets"]["arecel-power7"])
    _redigest(broken)
    with pytest.raises(ValueError, match="does not match immutable child-derived aggregation"):
        validate_cross_dataset_summary(broken, research_root=ROOT)


def test_summary_cli_has_offline_summarize_command() -> None:
    args = _parser().parse_args(
        ["validate", "rq4-ks-sensitivity", "summarize", "--output", "summary.json"]
    )
    assert args.rq4_ks_command == "summarize"
    assert args.output == Path("summary.json")
