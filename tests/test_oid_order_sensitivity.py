from __future__ import annotations

import copy
import types
from pathlib import Path

import pytest

from extstats_advisor_research.oid_order_sensitivity import (
    DATASET_SPECS,
    PREFLIGHT_PATH,
    PROTOCOL_PATH,
    CommandFailure,
    ExecutionState,
    _load_sealed_snapshot,
    _validate_query_records,
    _validate_synthetic_witness,
    build_readiness_artifact,
    deterministic_orders,
    validate_preflight,
    validate_protocol,
    validate_readiness_artifact,
    write_failure_artifact,
)
from extstats_advisor_research.provenance import read_json, semantic_digest, sha256_file

ROOT = Path(__file__).resolve().parents[1]


def test_order_roster_is_deterministic_and_unique() -> None:
    reference = ["a", "b", "c", "d", "e"]
    first = deterministic_orders(reference)
    assert first == deterministic_orders(reference)
    assert len(first) == 7
    assert len({tuple(item["order"]) for item in first}) == 7
    assert first[0]["kind"] == "reference"
    assert first[1]["order"] == list(reversed(reference))
    assert [item["seed"] for item in first[2:]] == [17, 29, 43, 71, 101]


def test_published_protocol_and_valid_only_preflight_validate() -> None:
    protocol = read_json(ROOT / PROTOCOL_PATH)
    preflight = read_json(ROOT / PREFLIGHT_PATH)
    assert validate_protocol(protocol)["status"] == "valid"
    assert validate_preflight(preflight, root=ROOT)["status"] == "valid"
    assert len(preflight["datasets"]) == 4
    for dataset in preflight["datasets"]:
        assert len(dataset["valid_workload"]["queries"]) == 10_000
        assert dataset["valid_truth"]["query_count"] == 10_000
        assert dataset["valid_workload"]["queries"][0].keys() == {
            "index",
            "query_id",
            "source_query_sha256",
            "sql_sha256",
        }
    serialized = str(preflight)
    assert "test workload" not in serialized
    assert "strict-unseen" not in serialized


def test_published_validation_context_does_not_extract_external_workloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import extstats_advisor_research.oid_order_sensitivity as oid

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("external workload extraction was invoked")

    for spec in DATASET_SPECS:
        monkeypatch.setattr(spec.dataset_module, "extract_workload", forbidden)
    for spec in DATASET_SPECS:
        _, _, bindings = oid._read_valid_context(ROOT, spec.dataset_id)
        assert len([key for key in bindings if not key.startswith(("source:", "truth:"))]) == 10_000


def test_formal_source_validation_remains_strict_without_raw_mount() -> None:
    import extstats_advisor_research.oid_order_sensitivity as oid

    with pytest.raises(FileNotFoundError, match="missing audited canonical workload"):
        oid._canonical_valid_records(Path("/tmp/nonexistent-oid-data"), "census13", "0" * 64)


def test_attempt_two_readiness_binds_original_records_without_execution() -> None:
    value = build_readiness_artifact(
        root=ROOT,
        implementation_commit_sha="a" * 40,
        verification={"offline_tests": True, "formal_execution": False},
    )
    assert validate_readiness_artifact(value, root=ROOT)["status"] == "valid"
    assert value["formal_invocation_count"] == 0
    assert value["scientific_execution"]["attempt_002_started"] is False
    mutated = copy.deepcopy(value)
    mutated["implementation_commit_sha"] = "not-a-sha"
    mutated["semantic_digest"] = semantic_digest(
        {key: item for key, item in mutated.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="implementation binding"):
        validate_readiness_artifact(mutated, root=ROOT)


def test_protocol_mutation_fails_closed() -> None:
    protocol = read_json(ROOT / PROTOCOL_PATH)
    mutated = copy.deepcopy(protocol)
    mutated["datasets"][0]["reference_order"] = list(
        reversed(mutated["datasets"][0]["reference_order"])
    )
    mutated["semantic_digest"] = semantic_digest(
        {key: value for key, value in mutated.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="permutation schedule drift"):
        validate_protocol(mutated)


def test_all_four_dataset_specs_are_distinct() -> None:
    assert [spec.cli_name for spec in DATASET_SPECS] == [
        "census13",
        "forest10",
        "power7",
        "dmv11",
    ]
    assert len({spec.output_root for spec in DATASET_SPECS}) == 4


def test_frozen_oid_artifacts_have_expected_identity() -> None:
    protocol = ROOT / PROTOCOL_PATH
    preflight = ROOT / PREFLIGHT_PATH
    failure = (
        ROOT / "experiments/oid-order-sensitivity-v1/failed-attempts/attempt-001/failure-v1.json"
    )
    assert (
        sha256_file(protocol) == "4babe7177a968a9416fbe7bf9b856d53e619285db42bfef74179fd3ffc714ec1"
    )
    assert (
        sha256_file(preflight) == "432f61a97c8bd32ca021f2e6fdc95109111c066e4af711bc8d10d08e7a1133f0"
    )
    assert (
        sha256_file(failure) == "59d389ec118e59ce99a1f02f132dffb3a3bd8dee1cbad3e7f51c00a8b700b1e4"
    )


def test_strict_protocol_binding_rejects_recomputed_membership_mutation() -> None:
    protocol = read_json(ROOT / PROTOCOL_PATH)
    mutated = copy.deepcopy(protocol)
    mutated["datasets"][0]["selected_definitions"] = []
    mutated["semantic_digest"] = semantic_digest(
        {key: value for key, value in mutated.items() if key != "semantic_digest"}
    )
    with pytest.raises(ValueError, match="selected definitions drift"):
        validate_protocol(mutated, root=ROOT)


def test_failure_writer_is_atomic_append_only_and_redacts_secrets(tmp_path: Path) -> None:
    preflight_path = ROOT / PREFLIGHT_PATH
    preflight = read_json(preflight_path)
    state = ExecutionState(invocation_id="attempt-002-test")
    state.transition("launcher-probe")
    failure_path = tmp_path / "failure-v1.json"
    write_failure_artifact(
        failure_path,
        producer_sha="a" * 40,
        preflight_path=preflight_path,
        preflight=preflight,
        state=state,
        exception=CommandFailure(
            ["tool", "password=secret"], 2, "stdout", "stderr password=secret", 0.1
        ),
    )
    payload = failure_path.read_text()
    assert "secret" not in payload
    with pytest.raises(FileExistsError):
        write_failure_artifact(
            failure_path,
            producer_sha="a" * 40,
            preflight_path=preflight_path,
            preflight=preflight,
            state=state,
            exception=RuntimeError("second write"),
        )


def test_query_validator_recomputes_raw_observations_and_rejects_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import extstats_advisor_research.oid_order_sensitivity as oid

    monkeypatch.setattr(oid, "QUERY_COUNT", 2)
    records = [
        {
            "dataset_id": "fixture",
            "permutation_id": "reference",
            "execution_arm": "controlled-hypothetical",
            "query_id": "q0",
            "sql_sha256": "1" * 64,
            "source_query_sha256": "2" * 64,
            "truth": 10,
            "plan_rows": 20,
            "qerror": 2.0,
            "explain_sha256": "3" * 64,
        },
        {
            "dataset_id": "fixture",
            "permutation_id": "reference",
            "execution_arm": "controlled-hypothetical",
            "query_id": "q1",
            "sql_sha256": "4" * 64,
            "source_query_sha256": "5" * 64,
            "truth": 20,
            "plan_rows": 10,
            "qerror": 2.0,
            "explain_sha256": "6" * 64,
        },
    ]
    path = tmp_path / "per-query.jsonl.gz"
    digest = oid._write_gzip_jsonl(path, records)
    arm = {"per_query_path": path.name, "per_query_sha256": digest}
    sql = {"q0": "1" * 64, "q1": "4" * 64}
    truth = {
        "source:q0": "2" * 64,
        "source:q1": "5" * 64,
        "truth:q0": "10",
        "truth:q1": "20",
    }
    validated, _ = _validate_query_records(
        root=tmp_path,
        arm=arm,
        expected_dataset_id="fixture",
        expected_permutation_id="reference",
        expected_execution_arm="controlled-hypothetical",
        expected_sql=sql,
        expected_truth=truth,
    )
    assert validated == records
    records[0]["qerror"] = 99.0
    oid._write_gzip_jsonl(path, records)
    arm["per_query_sha256"] = sha256_file(path)
    with pytest.raises(ValueError, match="q-error"):
        _validate_query_records(
            root=tmp_path,
            arm=arm,
            expected_dataset_id="fixture",
            expected_permutation_id="reference",
            expected_execution_arm="controlled-hypothetical",
            expected_sql=sql,
            expected_truth=truth,
        )


def test_synthetic_validator_requires_mechanism_and_cleanup() -> None:
    value = {
        "format_version": "oid-order-overlapping-mcv-witness-v1",
        "fixture_id": "oid-order-overlapping-mcv-witness-v1",
        "static_mechanism_precondition": {
            "tie_precondition_source_level": True,
            "runtime_tie_observation": "not-directly-instrumented",
        },
        "arms": {
            arm_id: {"exact_root_plan_rows_match": True}
            for arm_id in ("a-only", "b-only", "a-then-b", "b-then-a", "none")
        },
        "physical_hypothetical_exact_match_count": 5,
        "physical_hypothetical_arm_count": 5,
        "post_cleanup": True,
    }
    _validate_synthetic_witness(value)
    mutated = copy.deepcopy(value)
    mutated["post_cleanup"] = False
    with pytest.raises(ValueError, match="cleanup"):
        _validate_synthetic_witness(mutated)


def test_snapshot_truth_gate_requires_reloaded_sealed_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    import extstats_advisor_research.oid_order_sensitivity as oid

    class Snapshot:
        semantic_digest = "snapshot-digest"

    bundle = types.ModuleType("extstats_advisor.snapshot.bundle")
    bundle.load_snapshot = lambda path: Snapshot()
    monkeypatch.setitem(sys.modules, "extstats_advisor.snapshot.bundle", bundle)
    assert _load_sealed_snapshot(tmp_path / "snapshot").semantic_digest == "snapshot-digest"

    class UnsealedSnapshot:
        semantic_digest = ""

    bundle.load_snapshot = lambda path: UnsealedSnapshot()
    with pytest.raises(ValueError, match="no semantic digest"):
        oid._load_sealed_snapshot(tmp_path / "unsealed")


def test_oid_formal_cli_requires_explicit_invocation_identity() -> None:
    from extstats_advisor_research.cli import _parser

    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "oid-order-sensitivity",
                "run",
                "--stock-dsn",
                "dbname=stock",
                "--planner-dsn",
                "dbname=patched",
                "--data-root",
                "/tmp/data",
            ]
        )
