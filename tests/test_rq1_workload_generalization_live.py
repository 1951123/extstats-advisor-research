from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from extstats_advisor_research import rq1_workload_generalization_live as live
from extstats_advisor_research.provenance import read_json

ROOT = Path(__file__).resolve().parents[1]


def _execution() -> dict[str, object]:
    return {
        "advisor_run_manifest_digest": "a" * 64,
        "snapshot_digest": "b" * 64,
        "ground_truth_set_digest": "c" * 64,
        "candidate_universe_digest": "d" * 64,
        "native_repository_digest": "e" * 64,
        "singleton_profile_digest": "f" * 64,
        "optimization_plan_digest": "1" * 64,
        "search_result_digest": "2" * 64,
        "recommendation_digest": "3" * 64,
        "selected_candidate_ids": ["cand_one", "cand_two"],
        "deployment_ordered_candidate_ids": ["cand_one", "cand_two"],
        "search_baseline_objective": 10.0,
        "search_final_objective": 5.0,
        "termination_reason": "local-optimum",
        "design_stage_complete": True,
        "recommendation_sealed": True,
        "advisor_sha": live.FROZEN_ADVISOR_SHA,
        "patched_postgresql_sha": live.FROZEN_PATCHED_POSTGRES_SHA,
    }


def _fake_design_inputs() -> dict[str, object]:
    return {
        "dataset": {
            "dataset_id": live.POWER7,
            "benchmark_id": live.POWER7,
            "content_identity": "dataset-identity",
            "relation": "public.power7",
            "schema_contract_id": "arecel-power7-postgres-schema-v1",
            "rows": 2_075_259,
        },
        "workload": {
            "workload_id": "arecel_power7_valid_v1",
            "sha256": "4" * 64,
            "query_count": 10_000,
            "canonical_source_sha256": "5" * 64,
        },
        "truth": {
            "source_kind": "authoritative-external-exact",
            "collection_contract": "authoritative-external-exact-cardinality-v1",
            "dataset_identity": "dataset-identity",
            "source_revision": "aa52da7768023270bad884232972e0b77ec6534a",
            "observations_sha256": "6" * 64,
            "observations_semantic_digest": "7" * 64,
            "workload_id": "arecel_power7_valid_v1",
            "query_count": 10_000,
            "policy_status": "preregistered",
        },
        "workload_queries": [],
    }


def _sealed_design(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    monkeypatch.setattr(live, "_valid_design_inputs", lambda *args, **kwargs: _fake_design_inputs())
    return live.build_design_artifact(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        execution=_execution(),
    )


def test_design_artifact_is_valid_and_valid_only(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _sealed_design(monkeypatch)
    assert artifact["format_version"] == live.DESIGN_FORMAT
    assert artifact["design_stage"]["input_split"] == "valid"
    assert artifact["design_stage"]["workload"]["workload_id"] == "arecel_power7_valid_v1"
    assert artifact["evaluation_stage"]["test_inputs_accessed"] is False
    assert live.validate_design_artifact(artifact)["status"] == "valid"


def test_design_stage_does_not_need_test_or_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []

    def executor(inputs: dict[str, object]) -> dict[str, object]:
        seen.append(inputs)
        assert "workload_queries" in inputs
        assert "test" not in json.dumps(inputs, sort_keys=True).lower()
        return _execution()

    monkeypatch.setattr(live, "_valid_design_inputs", lambda *args, **kwargs: _fake_design_inputs())
    artifact = live.run_power7_rq1b_design(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        design_executor=executor,
    )
    assert len(seen) == 1
    assert artifact["design_stage"]["recommendation_sealed"] is True


def test_design_stage_resolves_valid_inputs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def inputs() -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _fake_design_inputs()

    monkeypatch.setattr(live, "_valid_design_inputs", lambda *args, **kwargs: inputs())
    live.run_power7_rq1b_design(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        design_executor=lambda value: _execution(),
    )
    assert calls == 1


def test_evaluation_refuses_unsealed_design_before_resolver() -> None:
    called = False

    def resolver() -> dict[str, object]:
        nonlocal called
        called = True
        return {}

    with pytest.raises(live.RQ1BValidationError, match="unsupported RQ1b design"):
        live.prepare_evaluation_stage(design_artifact={"status": "not-sealed"}, resolver=resolver)
    assert called is False


def test_evaluation_resolver_runs_only_after_seal(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _sealed_design(monkeypatch)
    called = False

    def resolver() -> dict[str, object]:
        nonlocal called
        called = True
        return {
            "dataset_id": live.POWER7,
            "evaluation_split": "test",
            "strict_unseen_membership_digest": live.STRICT_UNSEEN_DIGEST,
        }

    result = live.prepare_evaluation_stage(design_artifact=artifact, resolver=resolver)
    assert called is True
    assert result["evaluation_split"] == "test"


def test_recommendation_digest_mutation_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _sealed_design(monkeypatch)
    mutated = copy.deepcopy(artifact)
    mutated["design_stage"]["recommendation_digest"] = "0" * 64
    mutated["semantic_digest"] = live.semantic_digest(mutated)
    with pytest.raises(live.RQ1BValidationError, match="design artifact digest"):
        live.validate_design_artifact(mutated)


def test_strict_unseen_filters_existing_records_without_second_evaluation() -> None:
    membership = read_json(ROOT / live.STRICT_UNSEEN_PATH)
    row = next(item for item in membership["datasets"] if item["dataset_id"] == live.POWER7)
    records = [
        {"query_id": f"arecel_power7_test_{index:06d}", "estimate": 1.0, "truth": 1, "qerror": 1.0}
        for index in range(live.SAMPLE_ROWS)
    ]
    filtered = live.strict_unseen_filter(records, membership)
    assert len(filtered) == row["strict_unseen_count"]
    assert [item["query_id"] for item in filtered] == row["strict_unseen_test_query_ids"]


def test_test_evaluation_requires_canonical_order_and_exact_count() -> None:
    records = [
        {"query_id": f"arecel_power7_test_{index:06d}", "estimate": 1.0, "truth": 1, "qerror": 1.0}
        for index in range(live.SAMPLE_ROWS)
    ]
    assert len(live.validate_test_evaluation_records(records)) == live.SAMPLE_ROWS
    swapped = list(records)
    swapped[0], swapped[1] = swapped[1], swapped[0]
    with pytest.raises(live.RQ1BValidationError, match="query order"):
        live.validate_test_evaluation_records(swapped)


def test_evaluation_runs_once_and_derives_strict_metrics_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    design = _sealed_design(monkeypatch)
    membership = read_json(ROOT / live.STRICT_UNSEEN_PATH)
    records = [
        {"query_id": f"arecel_power7_test_{index:06d}", "estimate": 1.0, "truth": 1, "qerror": 1.0}
        for index in range(live.SAMPLE_ROWS)
    ]
    calls = 0

    def evaluator(inputs: dict[str, object], sealed: dict[str, object]) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        assert sealed["design_stage"]["recommendation_sealed"] is True
        return records

    result = live.run_power7_rq1b_evaluation(
        design_artifact=design,
        evaluation_resolver=lambda: {
            "dataset_id": live.POWER7,
            "evaluation_split": "test",
            "strict_unseen_membership_digest": live.STRICT_UNSEEN_DIGEST,
            "strict_unseen_membership": membership,
        },
        evaluator=evaluator,
        baseline_records={"pg16-default": records, "pg16-target10000": records},
        cleanup_passed=True,
    )
    assert calls == 1
    assert result["evaluation_stage"]["test_evaluated_once"] is True
    assert live.validate_power7_rq1b_result(result)["status"] == "valid"


def test_baseline_reuse_gate_accepts_immutable_power7_evidence() -> None:
    binding = live._baseline_binding(ROOT)
    assert binding["semantic_digest"] == live.RQ1A_POWER7_DIGEST
    assert set(binding["arms"]) == {"pg16-default", "pg16-target10000"}


def test_baseline_reuse_gate_rejects_mutated_source_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    baseline = read_json(ROOT / live.BASELINE_PATH)
    mutated = copy.deepcopy(baseline)
    mutated["workload"]["sha256"] = "0" * 64

    original_read_json = live.read_json

    def fake_read_json(path: Path):
        if path == ROOT / live.BASELINE_PATH:
            return mutated
        return original_read_json(path)

    monkeypatch.setattr(live, "read_json", fake_read_json)
    with pytest.raises(live.RQ1BValidationError, match="Power7 RQ1a source digest"):
        live._baseline_binding(ROOT)


def test_publish_requires_success_and_cleanup(tmp_path: Path) -> None:
    with pytest.raises(live.RQ1BValidationError, match="successful"):
        live.publish_result(result={"status": "timeout"}, output=tmp_path / "result.json")
    with pytest.raises(live.RQ1BValidationError, match="cleanup"):
        live.publish_result(
            result={"status": "success", "cleanup": {"passed": False}},
            output=tmp_path / "result.json",
        )
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    with pytest.raises(live.RQ1BValidationError, match="collision"):
        live.publish_result(
            result={"status": "success", "cleanup": {"passed": True}}, output=existing
        )


def test_preflight_output_collision_is_rejected() -> None:
    output = ROOT / live.PREFLIGHT_PATH
    with pytest.raises(live.RQ1BValidationError, match="clean committed tree"):
        live.build_power7_rq1b_preflight(
            research_root=ROOT,
            producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
            output=output,
        )


def test_formal_tree_gate_allows_only_canonical_preflight(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sha = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"
    preflight = {"research_commit_sha": sha}
    monkeypatch.setattr(
        live,
        "validate_power7_rq1b_preflight",
        lambda value, research_root: {"status": "valid"},
    )

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def clean_run(command: list[str], **kwargs: object) -> Completed:
        return Completed(
            sha if "rev-parse" in command else f"?? {live.PREFLIGHT_PATH.as_posix()}\n"
        )

    monkeypatch.setattr(live.subprocess, "run", clean_run)
    assert (
        live.verify_power7_formal_tree(
            research_root=tmp_path,
            preflight_path=live.PREFLIGHT_PATH,
            preflight=preflight,
        )["status"]
        == "ready"
    )

    def dirty_run(command: list[str], **kwargs: object) -> Completed:
        return Completed(sha if "rev-parse" in command else "?? debug.json\n")

    monkeypatch.setattr(live.subprocess, "run", dirty_run)
    with pytest.raises(live.RQ1BValidationError, match="disallowed changes"):
        live.verify_power7_formal_tree(
            research_root=tmp_path,
            preflight_path=live.PREFLIGHT_PATH,
            preflight=preflight,
        )
