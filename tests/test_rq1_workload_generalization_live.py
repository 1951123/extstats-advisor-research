from __future__ import annotations

import copy
import json
import sys
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


def _built_result(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    design = _sealed_design(monkeypatch)
    membership = read_json(ROOT / live.STRICT_UNSEEN_PATH)
    records = [
        {
            "query_id": f"arecel_power7_test_{index:06d}",
            "estimate": 1.0,
            "truth": 1,
            "qerror": 1.0,
            "weight": 1.0,
        }
        for index in range(live.SAMPLE_ROWS)
    ]
    return live.build_power7_rq1b_result(
        design_artifact=design,
        evaluation_inputs={
            "dataset_id": live.POWER7,
            "evaluation_split": "test",
            "strict_unseen_membership_digest": live.STRICT_UNSEEN_DIGEST,
            "strict_unseen_membership": membership,
            "source_audit": {
                "path": live.SOURCE_AUDIT_V2_PATH.as_posix(),
                "semantic_digest": live.SOURCE_AUDIT_DIGEST,
            },
            "test_workload": {
                "workload_id": "arecel_power7_test_v1",
                "sha256": "a" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
            "test_truth": {
                "workload_id": "arecel_power7_test_v1",
                "observations_sha256": "b" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
            "stock_postgresql": {
                "source_commit_sha": live.FROZEN_STOCK_POSTGRES_SHA,
                "postgres_version": "16.14",
                "evaluation_mode": "stock-full-data-deployment",
                "ordinary_statistics_target": 100,
            },
            "advisor": {
                "source_commit_sha": live.FROZEN_ADVISOR_SHA,
                "command": "mock-advisor",
                "source_checkout_verified": True,
            },
            "deployment": {
                "logical_path": live.DEPLOYMENT_PATH.as_posix(),
                "semantic_digest": "c" * 64,
            },
            "per_query_artifact": {
                "logical_path": live.PER_QUERY_PATH.as_posix(),
                "sha256": "d" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
            "s_test_candidate_ids": ["cand_historical"],
        },
        advisor_records=records,
        baseline_records={"pg16-default": records, "pg16-target10000": records},
        cleanup_passed=True,
    )


def test_design_artifact_is_valid_and_valid_only(monkeypatch: pytest.MonkeyPatch) -> None:
    artifact = _sealed_design(monkeypatch)
    assert artifact["format_version"] == live.DESIGN_FORMAT
    assert artifact["design_stage"]["input_split"] == "valid"
    assert artifact["design_stage"]["workload"]["workload_id"] == "arecel_power7_valid_v1"
    assert artifact["evaluation_stage"]["test_inputs_accessed"] is False
    assert artifact["source_audit"] == {
        "path": live.SOURCE_AUDIT_V2_PATH.as_posix(),
        "semantic_digest": live.SOURCE_AUDIT_DIGEST,
    }
    assert live.validate_design_artifact(artifact)["status"] == "valid"


def test_recommendation_contract_allows_distinct_deployment_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _execution()
    execution["deployment_ordered_candidate_ids"] = ["cand_two", "cand_one"]
    monkeypatch.setattr(live, "_valid_design_inputs", lambda *args, **kwargs: _fake_design_inputs())
    artifact = live.build_design_artifact(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        execution=execution,
    )
    assert (
        artifact["design_stage"]["selected_candidate_ids"]
        != artifact["design_stage"]["deployment_ordered_candidate_ids"]
    )
    assert live.validate_design_artifact(artifact)["status"] == "valid"


def test_live_design_adapter_binds_valid_split_without_live_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    observed: dict[str, object] = {}

    monkeypatch.setattr(live, "_valid_design_inputs", lambda *args, **kwargs: _fake_design_inputs())
    monkeypatch.setattr(
        live,
        "verify_frozen_systems_v2",
        lambda *args, **kwargs: {
            "advisor_commit_sha": live.FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": live.FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_commit_sha": live.FROZEN_STOCK_POSTGRES_SHA,
        },
    )

    def fake_canonical(**kwargs: object) -> dict[str, object]:
        observed.update(kwargs)
        return {
            "status": "complete",
            "run_directory": str(runtime),
            "execution": _execution(),
        }

    import extstats_advisor_research.canonical_runner as canonical

    monkeypatch.setattr(canonical, "_run_canonical", fake_canonical)
    output = tmp_path / "design-v1.json"
    result = live.execute_power7_rq1b_design_live(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        production_dsn="mock-production",
        planner_dsn="mock-planner",
        advisor_root=tmp_path / "advisor",
        patched_postgres_root=tmp_path / "patched",
        stock_postgres_root=tmp_path / "stock",
        output_root=tmp_path / "run",
        design_output=output,
    )
    assert observed["workload_split"] == "valid"
    assert observed["run_truth_sanity_check"] is False
    assert observed["historical_evidence"] is False
    assert result["design_artifact"]["design_stage"]["workload"]["workload_id"] == (
        "arecel_power7_valid_v1"
    )
    assert output.is_file()


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
            "source_audit": {
                "path": live.SOURCE_AUDIT_V2_PATH.as_posix(),
                "semantic_digest": live.SOURCE_AUDIT_DIGEST,
            },
            "test_workload": {
                "workload_id": "arecel_power7_test_v1",
                "sha256": "a" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
            "test_truth": {
                "workload_id": "arecel_power7_test_v1",
                "observations_sha256": "b" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
            "advisor": {
                "source_commit_sha": live.FROZEN_ADVISOR_SHA,
                "command": "extstats-advisor",
                "source_checkout_verified": True,
            },
            "stock_postgresql": {
                "source_commit_sha": live.FROZEN_STOCK_POSTGRES_SHA,
                "postgres_version": "16.14",
                "evaluation_mode": "stock-full-data-deployment",
                "ordinary_statistics_target": 100,
            },
            "deployment": {
                "logical_path": live.DEPLOYMENT_PATH.as_posix(),
                "semantic_digest": "c" * 64,
            },
            "per_query_artifact": {
                "logical_path": live.PER_QUERY_PATH.as_posix(),
                "sha256": "d" * 64,
                "query_count": live.SAMPLE_ROWS,
            },
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


def test_publish_result_does_not_double_hash_existing_digest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _built_result(monkeypatch)
    original_digest = result["semantic_digest"]
    first = live.publish_result(result=result, output=tmp_path / "first.json")
    second = live.publish_result(result=dict(result), output=tmp_path / "second.json")
    first_loaded = read_json(tmp_path / "first.json")
    second_loaded = read_json(tmp_path / "second.json")
    assert first["semantic_digest"] == original_digest
    assert second["semantic_digest"] == original_digest
    assert first_loaded["semantic_digest"] == live._digest_body(first_loaded)
    assert second_loaded["semantic_digest"] == live._digest_body(second_loaded)
    assert live.validate_power7_rq1b_result(first_loaded)["status"] == "valid"
    assert (tmp_path / "first.json").read_bytes() == (tmp_path / "second.json").read_bytes()


def test_result_digest_correction_changes_only_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    built = _built_result(monkeypatch)
    raw = copy.deepcopy(built)
    raw["semantic_digest"] = live.semantic_digest(raw)
    corrected = copy.deepcopy(raw)
    corrected["semantic_digest"] = built["semantic_digest"]
    assert [key for key in raw if raw[key] != corrected[key]] == ["semantic_digest"]
    assert live.semantic_digest(
        {key: value for key, value in corrected.items() if key != "semantic_digest"}
    ) == corrected["semantic_digest"]


def test_frozen_deployment_contract_excludes_runtime_metadata(tmp_path: Path) -> None:
    artifact = pytest.importorskip("extstats_advisor.deployment.artifact")
    raw_path = (
        ROOT
        / "experiments/arecel-power7/rq1-workload-generalization-v1/"
        / "attempt-006-raw/deployment-result-v1.json"
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    baseline = artifact.load_deployment_result(raw_path)
    assert baseline.semantic_digest == baseline.computed_semantic_digest
    for index, fields in enumerate(
        (
            {"created_at": "2099-01-01T00:00:00Z"},
            {"runtime_metadata": {"temporary": "different"}},
            {
                "created_at": "2099-01-01T00:00:00Z",
                "runtime_metadata": {"temporary": "different"},
            },
        )
    ):
        mutated = copy.deepcopy(raw)
        mutated.update(fields)
        path = tmp_path / f"runtime-{index}.json"
        path.write_text(json.dumps(mutated), encoding="utf-8")
        loaded = artifact.load_deployment_result(path)
        assert loaded.computed_semantic_digest == baseline.computed_semantic_digest

    semantic_mutation = copy.deepcopy(raw)
    semantic_mutation["server"]["server_version"] = "16.15"
    semantic_path = tmp_path / "semantic.json"
    semantic_path.write_text(json.dumps(semantic_mutation), encoding="utf-8")
    with pytest.raises(Exception, match="semantic digest"):
        artifact.load_deployment_result(semantic_path)


def test_rq1b_deployment_validation_uses_frozen_contract() -> None:
    pytest.importorskip("extstats_advisor.deployment.artifact")
    path = (
        ROOT
        / "experiments/arecel-power7/rq1-workload-generalization-v1/"
        / "attempt-006-raw/deployment-result-v1.json"
    )
    result = live.validate_power7_rq1b_deployment_artifact(
        path,
        expected_digest="bc10496c283ba4b6b0a1c0e2bdbba589ca5683e9757288fc4e67f98913424236",
    )
    assert result["semantic_digest"] == (
        "bc10496c283ba4b6b0a1c0e2bdbba589ca5683e9757288fc4e67f98913424236"
    )


def test_preflight_output_collision_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"
    output = tmp_path / live.PREFLIGHT_PATH
    output.parent.mkdir(parents=True)
    output.write_text("{}", encoding="utf-8")

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda command, **kwargs: Completed("" if "status" in command else producer),
    )
    with pytest.raises(live.RQ1BValidationError, match="output already exists"):
        live.build_power7_rq1b_preflight(
            research_root=tmp_path,
            producer_sha=producer,
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


def _fake_design_source_spec() -> dict[str, object]:
    return {
        "dataset_id": live.POWER7,
        "benchmark_id": live.POWER7,
        "dataset_content_identity": "a" * 64,
        "relation": "public.power7",
        "schema_contract_id": "arecel-power7-postgres-schema-v1",
        "rows": 2_075_259,
        "design_workload": {
            "source_split": "valid",
            "workload_id": "arecel_power7_valid_v1",
            "sha256": "b" * 64,
            "query_count": 10_000,
            "canonical_source_sha256": "c" * 64,
        },
        "design_truth": {
            "source_split": "valid",
            "path": live.VALID_OBSERVATIONS_PATH.as_posix(),
            "observations_sha256": "d" * 64,
            "workload_id": "arecel_power7_valid_v1",
            "query_count": 10_000,
            "dataset_identity": "a" * 64,
        },
    }


def test_preflight_contains_only_opaque_evaluation_bindings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda command, **kwargs: Completed("" if "status" in command else producer),
    )
    monkeypatch.setattr(
        live, "_design_source_spec", lambda *args, **kwargs: _fake_design_source_spec()
    )
    monkeypatch.setattr(
        live,
        "_system_binding",
        lambda root: {"semantic_digest": live.FROZEN_SYSTEM_FREEZE_V2_DIGEST},
    )
    value = live.build_power7_rq1b_preflight(
        research_root=tmp_path,
        producer_sha=producer,
    )
    assert value["campaign_attempt_index"] == 6
    assert value["prior_failed_attempt"] == {
        "path": live.PRIOR_FAILED_ATTEMPT_PATH.as_posix(),
        "semantic_digest": live.PRIOR_FAILED_ATTEMPT_DIGEST,
        "status": live.PRIOR_FAILED_ATTEMPT_STATUS,
        "failure_class": live.PRIOR_FAILED_ATTEMPT_CLASS,
        "evidence_eligible": False,
    }
    serialized = json.dumps(value, sort_keys=True)
    assert "evaluation_bindings" in value
    assert value["evaluation_bindings"] == live._opaque_evaluation_bindings()
    assert value["advisor_execution_policy"] == {
        "mode": "frozen-checkout-runtime-launcher",
        "source_checkout_sha": live.FROZEN_ADVISOR_SHA,
        "launcher_source": "verified advisor_root/src",
        "path_lookup_allowed": False,
    }
    for forbidden in (
        "arecel_power7_test_",
        "s_test_candidate_ids",
        "strict_unseen_test_query_ids",
        "seen_in_valid_count",
        "strict_unseen_count",
        "pg16-default-per-query",
        "pg16-target10000-per-query",
    ):
        assert forbidden not in serialized
    assert "baseline_reuse" not in value


def test_design_safe_preflight_never_calls_evaluation_resolvers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"

    class Completed:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda command, **kwargs: Completed("" if "status" in command else producer),
    )
    monkeypatch.setattr(
        live, "_design_source_spec", lambda *args, **kwargs: _fake_design_source_spec()
    )
    monkeypatch.setattr(
        live,
        "_system_binding",
        lambda root: {"semantic_digest": live.FROZEN_SYSTEM_FREEZE_V2_DIGEST},
    )
    for name in ("_validate_immutable_inputs", "_source_spec", "_baseline_binding"):
        monkeypatch.setattr(
            live,
            name,
            lambda *args, _name=name, **kwargs: pytest.fail(f"pre-seal resolver called: {_name}"),
        )
    value = live.build_power7_rq1b_preflight(
        research_root=tmp_path,
        producer_sha=producer,
    )
    assert live.validate_power7_rq1b_preflight(value, research_root=tmp_path)["status"] == "valid"


def _fake_frozen_advisor_checkout(root: Path) -> Path:
    source = root / "src" / "extstats_advisor"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("__version__ = 'fake'\n", encoding="utf-8")
    (source / "cli.py").write_text(
        "import sys\n"
        "def main():\n"
        "    if sys.argv[1:] == ['--version']:\n"
        "        print('fake-advisor 1')\n"
        "    return 0\n",
        encoding="utf-8",
    )
    return root


def test_frozen_advisor_launcher_uses_verified_checkout_and_absolute_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    advisor_root = _fake_frozen_advisor_checkout(tmp_path / "advisor")
    monkeypatch.setattr(live, "verify_git_sha", lambda root, expected: expected)

    value = live.prepare_frozen_advisor_launcher(
        advisor_root=advisor_root,
        runtime_root=tmp_path / "runtime",
    )
    launcher = Path(value["launcher_path"])
    assert launcher.is_absolute()
    assert launcher.is_file()
    assert not launcher.is_symlink()
    assert launcher.stat().st_mode & 0o777 == 0o700
    text = launcher.read_text(encoding="utf-8")
    assert text.startswith(f"#!{Path(sys.executable).resolve()}\n")
    assert f"sys.path.insert(0, {str(advisor_root / 'src')!r})" in text
    assert Path(value["python_executable"]).is_absolute()
    assert (
        Path(value["imported_module"])
        .resolve()
        .is_relative_to((advisor_root / "src" / "extstats_advisor").resolve())
    )
    assert value["probe_stdout"] == "fake-advisor 1"


def test_frozen_advisor_launcher_rejects_symlink_and_site_packages_import(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    advisor_root = _fake_frozen_advisor_checkout(tmp_path / "advisor")
    monkeypatch.setattr(live, "verify_git_sha", lambda root, expected: expected)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    target = tmp_path / "target"
    target.write_text("placeholder", encoding="utf-8")
    (runtime / "frozen-extstats-advisor").symlink_to(target)
    with pytest.raises(live.RQ1BValidationError, match="launcher"):
        live.prepare_frozen_advisor_launcher(advisor_root=advisor_root, runtime_root=runtime)

    outside = tmp_path / "site-packages" / "extstats_advisor" / "__init__.py"
    monkeypatch.setattr(
        live.subprocess,
        "run",
        lambda *args, **kwargs: type(
            "Completed", (), {"returncode": 0, "stdout": str(outside), "stderr": ""}
        )(),
    )
    with pytest.raises(live.RQ1BValidationError, match="outside frozen checkout"):
        live.prepare_frozen_advisor_launcher(
            advisor_root=advisor_root,
            runtime_root=tmp_path / "runtime-import",
        )


def test_valid_truth_design_resolver_does_not_open_mixed_evaluation_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_read_json = live.read_json
    forbidden = {
        (ROOT / live.SOURCE_AUDIT_V2_PATH).resolve(),
        (ROOT / live.TRUTH_POLICY_PATH).resolve(),
        (ROOT / live.STRICT_UNSEEN_PATH).resolve(),
        (ROOT / live.BASELINE_PATH).resolve(),
    }

    def guarded_read_json(path: Path) -> object:
        if Path(path).resolve() in forbidden:
            pytest.fail(f"pre-seal evaluation artifact opened: {path}")
        return original_read_json(path)

    monkeypatch.setattr(live, "read_json", guarded_read_json)
    monkeypatch.setattr(
        live,
        "authoritative_truth_spec_for_split",
        lambda *args, **kwargs: pytest.fail("mixed truth-policy resolver called"),
    )
    identity = live._valid_truth_identity(ROOT)
    assert identity["workload_id"] == live.VALID_WORKLOAD_ID


def test_postseal_evaluation_resolver_reads_content_after_design_validation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    artifact = _sealed_design(monkeypatch)
    events: list[str] = []
    original_validate = live.validate_design_artifact

    def validating(value: object, **kwargs: object) -> dict[str, object]:
        events.append("design-artifact-validated")
        return original_validate(value, **kwargs)

    monkeypatch.setattr(live, "validate_design_artifact", validating)
    monkeypatch.setattr(
        live,
        "_baseline_binding",
        lambda root: events.append("baseline-opened") or {"arms": {}, "s_test_candidate_ids": []},
    )
    monkeypatch.setattr(
        live,
        "_source_spec",
        lambda root: (
            events.append("source-audit-opened")
            or {
                "test_workload_id": "arecel_power7_test_v1",
                "test_workload_sha256": "a" * 64,
                "test_observations_sha256": "b" * 64,
            }
        ),
    )
    monkeypatch.setattr(
        live,
        "authoritative_truth_spec_for_split",
        lambda *args, **kwargs: (
            events.append("test-truth-opened")
            or {
                "observations_path": tmp_path / "test-truth.json",
                "observations_sha256": "b" * 64,
            }
        ),
    )
    (tmp_path / "test-truth.json").write_text(
        json.dumps({"workload_id": "arecel_power7_test_v1", "truths": []}), encoding="utf-8"
    )
    from extstats_advisor_research.datasets import power7

    def fake_extract(path: Path, data_root: Path | None, *, split: str) -> dict[str, object]:
        events.append("test-workload-opened")
        path.write_text(json.dumps({"queries": []}), encoding="utf-8")
        return {
            "workload_id": "arecel_power7_test_v1",
            "sha256": "a" * 64,
            "query_count": 10_000,
        }

    monkeypatch.setattr(power7, "extract_workload", fake_extract)
    monkeypatch.setattr(
        live,
        "_load_exact",
        lambda *args, **kwargs: events.append("strict-unseen-opened") or {},
    )
    result = live.resolve_power7_rq1b_evaluation_inputs_after_seal(
        research_root=ROOT,
        producer_sha="a49b279c50f59b9fe243d1c30e2ca1bf0606dfea",
        design_artifact=artifact,
        design_runtime_directory=tmp_path,
    )
    assert result["evaluation_split"] == "test"
    assert events[0] == "design-artifact-validated"
    assert events.index("baseline-opened") > events.index("design-artifact-validated")
    assert events.index("strict-unseen-opened") > events.index("design-artifact-validated")


def test_formal_runner_reloads_canonical_design_before_evaluation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"
    preflight_path = tmp_path / live.PREFLIGHT_PATH
    preflight_path.parent.mkdir(parents=True)
    preflight_path.write_text(json.dumps({"research_commit_sha": producer}), encoding="utf-8")
    sealed = _sealed_design(monkeypatch)
    observed: dict[str, object] = {}

    monkeypatch.setattr(live, "validate_power7_rq1b_preflight", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "verify_power7_formal_tree", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_validate_managed_lab_dsns", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        live,
        "prepare_frozen_advisor_launcher",
        lambda **kwargs: {"launcher_path": str(tmp_path / "runtime" / "frozen-extstats-advisor")},
    )
    monkeypatch.setattr(live, "prepare_power7_rq1b_formal_labs", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_stop_formal_roles", lambda: True)

    def fake_design(**kwargs: object) -> dict[str, object]:
        observed["design_advisor_command"] = kwargs["advisor_command"]
        destination = tmp_path / live.DESIGN_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(sealed), encoding="utf-8")
        return {
            "design_artifact": {"wrong": "in-memory placeholder"},
            "runtime_directory": tmp_path / "runtime",
        }

    def fake_evaluation(**kwargs: object) -> dict[str, object]:
        observed["design_artifact"] = kwargs["design_artifact"]
        observed["evaluation_advisor_command"] = kwargs["advisor_command"]
        return {"status": "mock-success"}

    monkeypatch.setattr(live, "execute_power7_rq1b_design_live", fake_design)
    monkeypatch.setattr(live, "execute_power7_rq1b_evaluation_live", fake_evaluation)
    result = live.run_power7_rq1b_formal(
        research_root=tmp_path,
        preflight_path=live.PREFLIGHT_PATH,
        stock_dsn="unused",
        planner_dsn="unused",
        advisor_root=tmp_path / "advisor",
        patched_postgres_root=tmp_path / "patched",
        stock_postgres_root=tmp_path / "stock",
    )
    assert result["status"] == "mock-success"
    assert observed["design_artifact"] == sealed
    assert observed["design_artifact"] is not sealed
    assert observed["design_advisor_command"] == observed["evaluation_advisor_command"]
    assert Path(str(observed["design_advisor_command"])).is_absolute()


def test_formal_labs_reinitialize_and_verify_both_roles_before_design(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from extstats_advisor_research import postgres_lab

    events: list[str] = []

    monkeypatch.setattr(
        live,
        "verify_frozen_systems_v2",
        lambda *args, **kwargs: {
            "advisor_commit_sha": live.FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": live.FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_commit_sha": live.FROZEN_STOCK_POSTGRES_SHA,
        },
    )

    def reinit(role: str) -> dict[str, object]:
        events.append(f"reinit:{role}")
        return {"role": role}

    def status(role: str) -> dict[str, object]:
        events.append(f"status:{role}")
        spec = postgres_lab.role_spec(role)
        sha = (
            live.FROZEN_STOCK_POSTGRES_SHA if role == "stock" else live.FROZEN_PATCHED_POSTGRES_SHA
        )
        return {
            "running": True,
            "socket_exists": True,
            "socket": str(spec.socket),
            "port": spec.port,
            "database": spec.database,
            "identity": {"source_commit_sha": sha},
            "server_version": "PostgreSQL 16.14 (mock)",
        }

    def doctor(role: str) -> dict[str, object]:
        events.append(f"doctor:{role}")
        return {
            "ok": True,
            "patched_backend": {
                "ok": True,
                "reference_source_commit": live.FROZEN_PATCHED_POSTGRES_SHA,
            }
            if role == "patched"
            else {},
        }

    monkeypatch.setattr(postgres_lab, "reinit_role", reinit)
    monkeypatch.setattr(postgres_lab, "status_role", status)
    monkeypatch.setattr(postgres_lab, "doctor_role", doctor)
    result = live.prepare_power7_rq1b_formal_labs(
        advisor_root=Path("advisor"),
        patched_postgres_root=Path("patched"),
        stock_postgres_root=Path("stock"),
    )

    assert result["status"] == "ready"
    assert events[:4] == ["reinit:stock", "status:stock", "reinit:patched", "status:patched"]
    assert events[-2:] == ["doctor:stock", "doctor:patched"]


def test_formal_lab_preparation_does_not_retry_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from extstats_advisor_research import postgres_lab

    monkeypatch.setattr(
        live,
        "verify_frozen_systems_v2",
        lambda *args, **kwargs: {
            "advisor_commit_sha": live.FROZEN_ADVISOR_SHA,
            "patched_postgres_commit_sha": live.FROZEN_PATCHED_POSTGRES_SHA,
            "stock_postgres_commit_sha": live.FROZEN_STOCK_POSTGRES_SHA,
        },
    )
    calls: list[str] = []

    def fail_once(role: str) -> None:
        calls.append(role)
        raise RuntimeError("mock reinit failure")

    monkeypatch.setattr(postgres_lab, "reinit_role", fail_once)
    with pytest.raises(RuntimeError, match="mock reinit failure"):
        live.prepare_power7_rq1b_formal_labs(
            advisor_root=Path("advisor"),
            patched_postgres_root=Path("patched"),
            stock_postgres_root=Path("stock"),
        )
    assert calls == ["stock"]


def test_formal_preparation_failure_stops_before_design_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"
    preflight_path = tmp_path / live.PREFLIGHT_PATH
    preflight_path.parent.mkdir(parents=True)
    preflight_path.write_text(json.dumps({"research_commit_sha": producer}), encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(live, "validate_power7_rq1b_preflight", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "verify_power7_formal_tree", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_validate_managed_lab_dsns", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        live,
        "prepare_frozen_advisor_launcher",
        lambda **kwargs: (
            events.append("launcher")
            or {"launcher_path": str(tmp_path / "runtime" / "frozen-extstats-advisor")}
        ),
    )
    monkeypatch.setattr(
        live,
        "prepare_power7_rq1b_formal_labs",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("prep failed")),
    )
    monkeypatch.setattr(live, "_stop_formal_roles", lambda: events.append("cleanup") or True)
    monkeypatch.setattr(
        live,
        "execute_power7_rq1b_design_live",
        lambda **kwargs: pytest.fail("design started after lab preparation failure"),
    )
    with pytest.raises(RuntimeError, match="prep failed"):
        live.run_power7_rq1b_formal(
            research_root=tmp_path,
            preflight_path=live.PREFLIGHT_PATH,
            stock_dsn="unused",
            planner_dsn="unused",
            advisor_root=tmp_path / "advisor",
            patched_postgres_root=tmp_path / "patched",
            stock_postgres_root=tmp_path / "stock",
        )
    assert events == ["launcher", "cleanup"]


def test_launcher_failure_prevents_lab_preparation_and_cleans_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    producer = "a49b279c50f59b9fe243d1c30e2ca1bf0606dfea"
    preflight_path = tmp_path / live.PREFLIGHT_PATH
    preflight_path.parent.mkdir(parents=True)
    preflight_path.write_text(json.dumps({"research_commit_sha": producer}), encoding="utf-8")
    events: list[str] = []
    monkeypatch.setattr(live, "validate_power7_rq1b_preflight", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "verify_power7_formal_tree", lambda *args, **kwargs: {})
    monkeypatch.setattr(live, "_validate_managed_lab_dsns", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        live,
        "prepare_frozen_advisor_launcher",
        lambda **kwargs: (_ for _ in ()).throw(live.RQ1BValidationError("launcher probe failed")),
    )
    monkeypatch.setattr(
        live,
        "prepare_power7_rq1b_formal_labs",
        lambda *args, **kwargs: pytest.fail("labs started after launcher failure"),
    )
    monkeypatch.setattr(live, "_stop_formal_roles", lambda: events.append("cleanup") or True)
    with pytest.raises(live.RQ1BValidationError, match="launcher probe failed"):
        live.run_power7_rq1b_formal(
            research_root=tmp_path,
            preflight_path=live.PREFLIGHT_PATH,
            stock_dsn="unused",
            planner_dsn="unused",
            advisor_root=tmp_path / "advisor",
            patched_postgres_root=tmp_path / "patched",
            stock_postgres_root=tmp_path / "stock",
        )
    assert events == ["cleanup"]


def test_managed_lab_dsns_reject_wrong_endpoints() -> None:
    from extstats_advisor_research import postgres_lab

    stock = postgres_lab.role_spec("stock")
    patched = postgres_lab.role_spec("patched")
    good_stock = f"host={stock.socket} port={stock.port} dbname={stock.database}"
    good_patched = f"host={patched.socket} port={patched.port} dbname={patched.database}"
    assert live._validate_managed_lab_dsns(stock_dsn=good_stock, planner_dsn=good_patched)[
        "actual"
    ]["stock"]["port"] == str(stock.port)
    with pytest.raises(live.RQ1BValidationError, match="stock DSN"):
        live._validate_managed_lab_dsns(
            stock_dsn="host=/tmp/other port=55432 dbname=extstats_stock",
            planner_dsn=good_patched,
        )


def test_sandbox_catalog_contract_uses_shared_managed_logical_database() -> None:
    from extstats_advisor_research import postgres_lab

    stock = postgres_lab.role_spec("stock")
    patched = postgres_lab.role_spec("patched")
    snapshot_catalog = stock.database
    assert snapshot_catalog == patched.database
    assert stock.port != patched.port
    assert stock.socket != patched.socket
