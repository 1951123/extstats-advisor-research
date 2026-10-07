from __future__ import annotations

import copy
import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from extstats_advisor.optimization import (
    TERMINATION_ALL_SELECTED,
    TERMINATION_MAX_STATISTICS_COUNT,
)
from extstats_advisor.optimization.singleton import SINGLETON_PRECEDENCE_POLICY

from extstats_advisor_research.cli import _parser
from extstats_advisor_research.provenance import semantic_digest, write_json
from extstats_advisor_research.rq4_ablation import RQ4ValidationError
from extstats_advisor_research.rq4_formal_common import (
    _advisor_modules,
    _build_full_universe_plan,
    load_reusable_source,
)
from extstats_advisor_research.rq4_ks_sensitivity import (
    FIXED_B,
    FORMAL_EXECUTION_WIDTHS,
    FORMAL_REUSED_WIDTH,
    FORMAT_VERSION,
    MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
    PREFLIGHT_FORMAT,
    SCREENING_WIDTHS,
    SEARCH_WALL_CLOCK_SECONDS,
    TOP_K_PROTOCOL_DIGEST,
    _ConfigurationEvaluationGuard,
    _formal_selection_accounting,
    _reused_all_point,
    build_sensitivity_plan,
    candidate_prefix_digest,
    effective_candidate_count,
    normalize_screening_width,
    selection_configuration_evaluation_count,
    validate_all_reuse_gate,
    validate_formal_ks_sensitivity,
    worst_case_live_proposals,
)
from extstats_advisor_research.system_freeze_v2 import (
    FROZEN_ADVISOR_SHA,
    FROZEN_PATCHED_POSTGRES_SHA,
    FROZEN_SYSTEM_FREEZE_V2_DIGEST,
)


def test_protocol_constants_are_preregistered() -> None:
    assert FORMAT_VERSION == "rq4-ks-sensitivity-v1"
    assert PREFLIGHT_FORMAT == "rq4-ks-sensitivity-preflight-v1"
    assert SCREENING_WIDTHS == (4, 8, 16, 32, "all")
    assert FIXED_B == 4
    assert SEARCH_WALL_CLOCK_SECONDS == 300.0
    assert MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS == 2000
    assert FORMAL_EXECUTION_WIDTHS == (4, 8, 16, 32)
    assert FORMAL_REUSED_WIDTH == "all"
    assert SCREENING_WIDTHS == (*FORMAL_EXECUTION_WIDTHS, FORMAL_REUSED_WIDTH)


def test_formal_cli_is_one_dataset_and_has_no_stock_dsn() -> None:
    args = _parser().parse_args(
        [
            "validate",
            "rq4-ks-sensitivity",
            "run",
            "--dataset",
            "arecel-census13",
            "--patched-dsn",
            "patched",
            "--output",
            "out.json",
        ]
    )
    assert args.rq4_ks_command == "run"
    assert args.dataset == "arecel-census13"
    assert args.patched_dsn == "patched"
    assert not hasattr(args, "stock_dsn")

    create_args = _parser().parse_args(
        [
            "validate",
            "rq4-ks-sensitivity",
            "create",
            "--dataset",
            "arecel-power7",
            "--patched-dsn",
            "patched",
            "--output",
            "out.json",
        ]
    )
    assert create_args.rq4_ks_command == "create"
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "validate",
                "rq4-ks-sensitivity",
                "run",
                "--dataset",
                "all",
                "--patched-dsn",
                "patched",
                "--output",
                "out.json",
            ]
        )


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


def test_formal_schedule_executes_only_finite_widths_and_reuses_all() -> None:
    assert FORMAL_REUSED_WIDTH not in FORMAL_EXECUTION_WIDTHS
    assert tuple(width for width in SCREENING_WIDTHS if width != "all") == (
        4,
        8,
        16,
        32,
    )


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
    if "max_statistics_count" not in inspect.signature(modules["OptimizationBudget"]).parameters:
        pytest.skip("installed historical v1 Advisor does not expose max_statistics_count")
    modules = {**modules, "SCREENING_POLICY": modules["SCREENING_POLICY"]}
    old_plan = _build_full_universe_plan(source, modules)
    new_plan = build_sensitivity_plan(source, modules, "all")
    assert old_plan.computed_semantic_digest == new_plan.computed_semantic_digest
    assert old_plan.screened_candidate_ids == new_plan.screened_candidate_ids
    assert old_plan.excluded_actionable_candidate_ids == new_plan.excluded_actionable_candidate_ids
    assert old_plan.budget.to_dict() == new_plan.budget.to_dict()


def test_finite_plan_uses_fixed_b_and_wall_clock() -> None:
    source = _plan_source()
    modules = _advisor_modules(Path("/home/wqts/projects/extstats-advisor"))
    if "max_statistics_count" not in inspect.signature(modules["OptimizationBudget"]).parameters:
        pytest.skip("installed historical v1 Advisor does not expose max_statistics_count")
    plan = build_sensitivity_plan(source, modules, 4)
    assert plan.budget.max_statistics_count == FIXED_B
    assert plan.budget.wall_clock_seconds == SEARCH_WALL_CLOCK_SECONDS
    assert tuple(plan.screened_candidate_ids) == ("a", "b", "c", "d")


def test_configuration_evaluation_accounting_is_mandatory_and_capped() -> None:
    assert (
        selection_configuration_evaluation_count(
            {"proposal_configuration_evaluations": MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS}
        )
        == MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS
    )
    with pytest.raises(RQ4ValidationError, match="proposal_configuration_evaluations"):
        selection_configuration_evaluation_count({})
    with pytest.raises(RQ4ValidationError, match="configuration-evaluation cap"):
        selection_configuration_evaluation_count({"proposal_configuration_evaluations": 2001})

    selection = {
        "proposal_configuration_evaluations": 2,
        "actual_search_planner_calls": 10,
        "reference_search_planner_calls": 20,
        "saved_search_planner_calls": 10,
        "planner_query_reduction_fraction": 0.5,
    }
    assert (
        _formal_selection_accounting(selection, wall_clock_seconds=300.0)[
            "selection_configuration_objective_evaluations"
        ]
        == 2
    )
    with pytest.raises(RQ4ValidationError, match="missing required fields"):
        _formal_selection_accounting(
            {
                key: value
                for key, value in selection.items()
                if key != "actual_search_planner_calls"
            },
            wall_clock_seconds=1.0,
        )


def test_configuration_evaluation_guard_stops_before_the_cap() -> None:
    calls = 0

    def evaluator(_membership: frozenset[str], _deadline: object) -> None:
        nonlocal calls
        calls += 1

    guard = _ConfigurationEvaluationGuard(evaluator, 2)
    guard(frozenset(), None)
    guard(frozenset({"a"}), None)
    from extstats_advisor.errors import SearchBudgetExpired

    with pytest.raises(SearchBudgetExpired):
        guard(frozenset({"b"}), None)
    assert calls == 2
    assert guard.runtime_metadata()["configuration_evaluation_guard"]["hard_guard_enforced"] is True


def _formal_source_fixture() -> dict:
    candidate_ids = ("a", "b", "c", "d", "e")
    profile = SimpleNamespace(
        computed_semantic_digest="p" * 64,
        frozen_ordered_candidate_ids=candidate_ids,
        runtime_metadata={},
    )
    return {
        "eligible_universe": {
            "semantic_digest": "e" * 64,
            "eligible_candidates": [{"candidate_id": item} for item in candidate_ids],
        },
        "singleton_profile": profile,
        "source_artifact_digests": {"singleton_profile": "s" * 64},
    }


def _require_v2_validation_environment() -> None:
    if os.environ.get("EXTSTATS_ADVISOR_VALIDATION") != "v2":
        pytest.skip("historical compatibility job does not assert frozen-v2-only APIs")


def _final_evaluation() -> dict:
    return {
        "performed": True,
        "evaluation_scope": "independent-final-sandbox-evaluation",
        "selection_budget_charged": False,
        "stock_physical_evaluation": False,
        "status": "complete",
    }


def _finite_point(width: int, selected: list[str] | None = None) -> dict:
    prefix = ["a", "b", "c", "d", "e"][: min(width, 5)]
    selected = selected or prefix[:FIXED_B]
    accounting = {
        "proposal_configuration_evaluations": 2,
        "actual_search_planner_calls": 10,
        "reference_search_planner_calls": 20,
        "saved_search_planner_calls": 10,
        "planner_query_reduction_fraction": 0.5,
    }
    return {
        "K_s": width,
        "execution_mode": "executed",
        "effective_candidate_count": len(prefix),
        "candidate_prefix": prefix,
        "candidate_prefix_semantic_digest": candidate_prefix_digest(prefix),
        "B": FIXED_B,
        "status": "complete",
        "selected_candidate_ids": selected,
        "selected_k": len(selected),
        "termination_reason": TERMINATION_MAX_STATISTICS_COUNT,
        "search_result": {"final_ordered_candidate_ids": selected},
        "selection_accounting": accounting,
        **_formal_selection_accounting(accounting, wall_clock_seconds=1.0),
        "selection_wall_clock_seconds": 1.0,
        "source_singleton_profiling_accounting": {"new_work": 0},
        "final_sandbox_evaluation": _final_evaluation(),
    }


def _formal_artifact_fixture() -> tuple[dict, dict, dict]:
    _require_v2_validation_environment()
    source = _formal_source_fixture()
    points = [_finite_point(width) for width in FORMAL_EXECUTION_WIDTHS]
    all_ids = ["a", "b", "c", "d"]
    all_accounting = {
        "proposal_configuration_evaluations": 3,
        "actual_search_planner_calls": 10,
        "reference_search_planner_calls": 20,
        "saved_search_planner_calls": 10,
        "planner_query_reduction_fraction": 0.5,
    }
    all_runtime = {"elapsed_search_seconds": 1.0}
    points.append(
        {
            "K_s": FORMAL_REUSED_WIDTH,
            "execution_mode": "reused",
            "effective_candidate_count": 5,
            "candidate_prefix": ["a", "b", "c", "d", "e"],
            "candidate_prefix_semantic_digest": candidate_prefix_digest(["a", "b", "c", "d", "e"]),
            "B": FIXED_B,
            "status": "complete",
            "selected_candidate_ids": all_ids,
            "selected_k": len(all_ids),
            "termination_reason": TERMINATION_MAX_STATISTICS_COUNT,
            "search_result": {
                "final_ordered_candidate_ids": all_ids,
                "runtime_metadata": all_runtime,
            },
            "selection_accounting": all_accounting,
            **_formal_selection_accounting(all_accounting, wall_clock_seconds=1.0),
            "selection_wall_clock_seconds": 1.0,
            "source_singleton_profiling_accounting": {"new_work": 0},
            "final_sandbox_evaluation": _final_evaluation(),
            "reuse": {
                "source_path": "experiments/fixed-k.json",
                "source_semantic_digest": "r" * 64,
                "source_method": "greedy-ADD",
            },
        }
    )
    artifact = {
        "format_version": FORMAT_VERSION,
        "experiment_id": "rq4-ks-sensitivity-v1",
        "dataset_id": "arecel-census13",
        "status": "complete",
        "research_commit_sha": "c" * 40,
        "advisor_commit_sha": FROZEN_ADVISOR_SHA,
        "patched_postgres_commit_sha": FROZEN_PATCHED_POSTGRES_SHA,
        "system_freeze_semantic_digest": FROZEN_SYSTEM_FREEZE_V2_DIGEST,
        "protocol_path": "paper/top-k-screening-protocol-v2.json",
        "protocol_semantic_digest": TOP_K_PROTOCOL_DIGEST,
        "eligible_universe_semantic_digest": source["eligible_universe"]["semantic_digest"],
        "source_singleton_profile_semantic_digest": source[
            "singleton_profile"
        ].computed_semantic_digest,
        "source_artifact_digests": source["source_artifact_digests"],
        "fixed_B": FIXED_B,
        "screening_widths": list(SCREENING_WIDTHS),
        "search_wall_clock_seconds": SEARCH_WALL_CLOCK_SECONDS,
        "max_configuration_objective_evaluations": MAX_CONFIGURATION_OBJECTIVE_EVALUATIONS,
        "preflight": {"status": "ready-to-run"},
        "points": points,
        "formal_confirmatory_experiment": True,
        "stock_physical_evaluation": {"performed": False},
    }
    artifact["semantic_digest"] = semantic_digest(artifact)
    gate = {
        "source_path": "experiments/fixed-k.json",
        "source_semantic_digest": "r" * 64,
        "source_method": "greedy-ADD",
    }
    return artifact, source, gate


def _redigest(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["semantic_digest"] = semantic_digest(
        {key: item for key, item in result.items() if key != "semantic_digest"}
    )
    return result


def test_synthetic_five_point_artifact_validates_and_censoring_is_allowed() -> None:
    artifact, source, gate = _formal_artifact_fixture()
    censored = artifact["points"][0]
    censored["status"] = "budget-censored"
    censored["termination_reason"] = "budget-expired-incomplete-round"
    censored["selected_candidate_ids"] = ["a", "b"]
    censored["selected_k"] = 2
    censored["search_result"]["final_ordered_candidate_ids"] = ["a", "b"]
    censored["selection_wall_clock_seconds"] = 300.5
    artifact = _redigest(artifact)
    result = validate_formal_ks_sensitivity(artifact, source=source, all_reuse_gate=gate)
    assert result["status"] == "valid"


def test_canonical_all_selected_termination_is_accepted() -> None:
    artifact, source, gate = _formal_artifact_fixture()
    point = artifact["points"][0]
    point["termination_reason"] = TERMINATION_ALL_SELECTED
    point["status"] = "complete"
    point["selected_candidate_ids"] = ["a", "b", "c", "d"]
    point["selected_k"] = FIXED_B
    point["search_result"]["final_ordered_candidate_ids"] = ["a", "b", "c", "d"]
    broken = _redigest(artifact)
    assert (
        validate_formal_ks_sensitivity(broken, source=source, all_reuse_gate=gate)["status"]
        == "valid"
    )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update(protocol_semantic_digest="bad"), "protocol digest"),
        (lambda value: value.update(fixed_B=8), "protocol constants"),
        (
            lambda value: value["points"][-1].update(execution_mode="executed"),
            "reused",
        ),
        (
            lambda value: value["points"][0].update(candidate_prefix=["b", "a", "c", "d"]),
            "prefix is not",
        ),
        (
            lambda value: value["points"][0]["selection_accounting"].pop(
                "proposal_configuration_evaluations"
            ),
            "proposal_configuration_evaluations",
        ),
        (
            lambda value: value["stock_physical_evaluation"].update(performed=True),
            "stock physical",
        ),
        (
            lambda value: value["points"][0].update(termination_reason="all-selected"),
            "illegal termination",
        ),
    ],
)
def test_formal_artifact_mutations_fail_closed(mutation, match: str) -> None:
    artifact, source, gate = _formal_artifact_fixture()
    mutation(artifact)
    if "candidate_prefix" in match:
        artifact["points"][0]["candidate_prefix_semantic_digest"] = candidate_prefix_digest(
            artifact["points"][0]["candidate_prefix"]
        )
    if "proposal_configuration_evaluations" in match:
        artifact["points"][0].pop("proposal_configuration_objective_evaluations")
    broken = _redigest(artifact)
    with pytest.raises(RQ4ValidationError, match=match):
        validate_formal_ks_sensitivity(broken, source=source, all_reuse_gate=gate)


def test_formal_reuse_gate_mismatch_and_provisional_selection_fail_closed() -> None:
    artifact, source, gate = _formal_artifact_fixture()
    with pytest.raises(RQ4ValidationError, match="reuse source"):
        validate_formal_ks_sensitivity(
            artifact,
            source=source,
            all_reuse_gate={**gate, "source_semantic_digest": "x" * 64},
        )

    broken = copy.deepcopy(artifact)
    broken["points"][0]["search_result"]["final_ordered_candidate_ids"] = [
        "a",
        "b",
        "c",
    ]
    broken = _redigest(broken)
    with pytest.raises(RQ4ValidationError, match="committed selection"):
        validate_formal_ks_sensitivity(broken, source=source, all_reuse_gate=gate)


def test_final_evaluation_is_required_even_for_censored_point() -> None:
    artifact, source, gate = _formal_artifact_fixture()
    artifact["points"][0]["status"] = "budget-censored"
    artifact["points"][0]["termination_reason"] = "budget-expired-before-round"
    artifact["points"][0]["final_sandbox_evaluation"] = {"performed": False}
    broken = _redigest(artifact)
    with pytest.raises(RQ4ValidationError, match="final evaluation"):
        validate_formal_ks_sensitivity(broken, source=source, all_reuse_gate=gate)


def _write_reuse_fixture(root: Path, greedy: dict) -> dict:
    write_json(
        root / "child.json",
        {"rq4a": {"artifact": "design.json"}},
    )
    write_json(root / "design.json", {"methods": {"greedy-ADD": greedy}})
    return {
        "source_path": "child.json",
        "source_semantic_digest": "r" * 64,
        "source_method": "greedy-ADD",
    }


def _reuse_greedy_fixture() -> dict:
    accounting = {
        "proposal_configuration_evaluations": 120,
        "actual_search_planner_calls": 468090,
        "reference_search_planner_calls": 1200000,
        "saved_search_planner_calls": 731910,
        "planner_query_reduction_fraction": 0.609925,
    }
    return {
        "search_result": {
            "runtime_metadata": {"elapsed_search_seconds": 185.87962744},
            "final_ordered_candidate_ids": ["a", "b", "c", "d"],
        },
        "termination_reason": TERMINATION_MAX_STATISTICS_COUNT,
        "selected_membership": ["a", "b", "c", "d"],
        "selection_accounting": accounting,
        "final_sandbox_evaluation": _final_evaluation(),
    }


def test_reused_all_projects_canonical_accounting_from_runtime_metadata(tmp_path: Path) -> None:
    _require_v2_validation_environment()
    source = _formal_source_fixture()
    gate = _write_reuse_fixture(tmp_path, _reuse_greedy_fixture())
    point = _reused_all_point(source, tmp_path, gate)
    assert point["execution_mode"] == "reused"
    assert point["selection_configuration_objective_evaluations"] == 120
    assert point["proposal_configuration_objective_evaluations"] == 120
    assert point["incremental_planner_calls"] == 468090
    assert point["reference_search_planner_calls"] == 1200000
    assert point["saved_search_planner_calls"] == 731910
    assert point["planner_call_reduction_fraction"] == 0.609925
    assert point["selection_wall_clock_seconds"] == 185.87962744


@pytest.mark.parametrize(
    "mutation",
    [
        lambda greedy: greedy["search_result"].pop("runtime_metadata"),
        lambda greedy: greedy["search_result"]["runtime_metadata"].pop("elapsed_search_seconds"),
        lambda greedy: greedy["selection_accounting"].pop("proposal_configuration_evaluations"),
    ],
)
def test_reused_all_projection_fails_closed_on_missing_source_accounting(
    tmp_path: Path, mutation
) -> None:
    _require_v2_validation_environment()
    greedy = _reuse_greedy_fixture()
    mutation(greedy)
    source = _formal_source_fixture()
    gate = _write_reuse_fixture(tmp_path, greedy)
    with pytest.raises(RQ4ValidationError):
        _reused_all_point(source, tmp_path, gate)


def test_power7_immutable_reused_all_projection_matches_source() -> None:
    _require_v2_validation_environment()
    root = Path(__file__).resolve().parents[1]
    advisor_root = Path("/home/wqts/projects/extstats-advisor")
    source = load_reusable_source("arecel-power7", root, advisor_root)
    gate = validate_all_reuse_gate("arecel-power7", root, source)
    point = _reused_all_point(source, root, gate)
    assert point["execution_mode"] == "reused"
    assert point["termination_reason"] == TERMINATION_MAX_STATISTICS_COUNT
    assert point["selected_k"] == 4
    assert point["selection_configuration_objective_evaluations"] == 120
    assert point["incremental_planner_calls"] == 468090
    assert point["selection_wall_clock_seconds"] == pytest.approx(185.87962744)
    assert point["final_sandbox_evaluation"]["performed"] is True
    assert point["final_sandbox_evaluation"]["stock_physical_evaluation"] is False
