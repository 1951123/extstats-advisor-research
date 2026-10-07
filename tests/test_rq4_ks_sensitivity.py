from __future__ import annotations

from types import SimpleNamespace

import pytest
from extstats_advisor.optimization.singleton import SINGLETON_PRECEDENCE_POLICY

from extstats_advisor_research.rq4_formal_common import _advisor_modules, _build_full_universe_plan
from extstats_advisor_research.rq4_ks_sensitivity import (
    FIXED_B,
    FORMAT_VERSION,
    MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
    PREFLIGHT_FORMAT,
    SCREENING_WIDTHS,
    SEARCH_WALL_CLOCK_SECONDS,
    build_sensitivity_plan,
    candidate_prefix_digest,
    effective_candidate_count,
    normalize_screening_width,
    worst_case_live_proposals,
)


def test_protocol_constants_are_preregistered() -> None:
    assert FORMAT_VERSION == "rq4-ks-sensitivity-v1"
    assert PREFLIGHT_FORMAT == "rq4-ks-sensitivity-preflight-v1"
    assert SCREENING_WIDTHS == (4, 8, 16, 32, "all")
    assert FIXED_B == 4
    assert SEARCH_WALL_CLOCK_SECONDS == 300.0
    assert MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS == 2000


@pytest.mark.parametrize(
    ("n", "width", "expected"),
    [
        (136, 4, 6),
        (136, 8, 18),
        (136, 16, 42),
        (136, 32, 90),
        (136, "all", 402),
        (42, "all", 120),
        (105, "all", 309),
    ],
)
def test_worst_case_live_proposal_model(n: int, width: int | str, expected: int) -> None:
    assert worst_case_live_proposals(n, width) == expected


def test_prefix_is_the_exact_frozen_order_without_reranking() -> None:
    source = {
        "eligible_universe": {
            "eligible_candidates": [{"candidate_id": item} for item in ("a", "b", "c", "d", "e")]
        },
        "singleton_profile": SimpleNamespace(
            frozen_ordered_candidate_ids=("a", "b", "c", "d", "e")
        ),
    }
    from extstats_advisor_research.rq4_ks_sensitivity import frozen_candidate_prefix

    assert frozen_candidate_prefix(source, 4) == ("a", "b", "c", "d")
    assert frozen_candidate_prefix(source, "all") == ("a", "b", "c", "d", "e")
    assert candidate_prefix_digest(("a", "b")) != candidate_prefix_digest(("b", "a"))


def test_k_s_must_not_be_smaller_than_b() -> None:
    with pytest.raises(ValueError, match="K_s"):
        normalize_screening_width(3)


def test_effective_width_never_exceeds_the_eligible_universe() -> None:
    assert effective_candidate_count(6, 32) == 6
    assert effective_candidate_count(6, "all") == 6


def _plan_source() -> dict:
    ids = ("a", "b", "c", "d", "e", "f")
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
    candidates = [{"candidate_id": candidate_id} for candidate_id in ids]
    return {
        "singleton_profile": profile,
        "eligible_universe": {"eligible_candidates": candidates},
    }


def test_all_width_projection_matches_existing_full_universe_plan() -> None:
    source = _plan_source()
    modules = _advisor_modules(__import__("pathlib").Path("/home/wqts/projects/extstats-advisor"))
    modules = {**modules, "SCREENING_POLICY": modules["SCREENING_POLICY"]}
    old_plan = _build_full_universe_plan(source, modules)
    new_plan = build_sensitivity_plan(source, modules, "all")
    assert old_plan.computed_semantic_digest == new_plan.computed_semantic_digest
    assert old_plan.screened_candidate_ids == new_plan.screened_candidate_ids
    assert old_plan.excluded_actionable_candidate_ids == new_plan.excluded_actionable_candidate_ids
    assert old_plan.budget.to_dict() == new_plan.budget.to_dict()
