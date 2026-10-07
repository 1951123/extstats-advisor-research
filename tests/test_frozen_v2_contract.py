from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from extstats_advisor.optimization import OptimizationBudget
from extstats_advisor.optimization.singleton import SINGLETON_PRECEDENCE_POLICY

from extstats_advisor_research.paper_spec import (
    load_top_k_screening_protocol,
    validate_top_k_screening_protocol,
)
from extstats_advisor_research.rq4_formal_common import _advisor_modules
from extstats_advisor_research.rq4_ks_sensitivity import build_sensitivity_plan


def _require_v2_validation_environment() -> None:
    if os.environ.get("EXTSTATS_ADVISOR_VALIDATION") != "v2":
        pytest.skip("historical compatibility job does not assert frozen-v2-only APIs")


def _source() -> dict:
    ids = tuple("abcdef")
    profiles = tuple(
        SimpleNamespace(
            candidate_id=candidate_id,
            native_state="present",
            singleton_objective=10.0 - position,
            improvement=float(position),
        )
        for position, candidate_id in enumerate(ids, 1)
    )
    profile = SimpleNamespace(
        source_snapshot_semantic_digest="a" * 64,
        candidate_universe_semantic_digest="b" * 64,
        native_stats_repository_semantic_digest="c" * 64,
        ground_truth_semantic_digest="d" * 64,
        computed_semantic_digest="e" * 64,
        utility_contract="weighted-workload-mean-v1",
        loss_contract="qerror-cardinality-floor-1-v1",
        precedence_policy=SINGLETON_PRECEDENCE_POLICY,
        candidate_profiles=profiles,
        frozen_ordered_candidate_ids=ids,
    )
    return {
        "singleton_profile": profile,
        "eligible_universe": {"eligible_candidates": [{"candidate_id": item} for item in ids]},
    }


def test_frozen_v2_budget_exposes_separate_screening_and_selection_caps() -> None:
    _require_v2_validation_environment()
    assert "max_statistics_count" in inspect.signature(OptimizationBudget).parameters
    budget = OptimizationBudget(6, 300.0, 4)
    assert budget.candidate_limit == 6
    assert budget.effective_max_statistics_count == 4

    modules = _advisor_modules(Path("/home/wqts/projects/extstats-advisor"))
    plan = build_sensitivity_plan(_source(), modules, 6)
    assert len(plan.screened_candidate_ids) == 6
    assert plan.budget.max_statistics_count == 4
    assert plan.budget.effective_max_statistics_count == 4


def test_top_k_protocol_digest_is_valid() -> None:
    result = validate_top_k_screening_protocol(load_top_k_screening_protocol())
    assert result["semantic_digest"]
