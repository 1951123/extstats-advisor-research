from __future__ import annotations

import json
from pathlib import Path

from extstats_advisor_research.provenance import semantic_digest, sha256_file

ROOT = Path(__file__).resolve().parents[1]
ATTEMPT_ROOT = ROOT / (
    "experiments/arecel-power7/rq1-workload-generalization-v1/failed-attempts/attempt-001"
)
EXPECTED_PREFLIGHT_DIGEST = "84e398a709f82a3b7e7fd46715c36870d0a3832037c4d944043da6530e5f2152"
EXPECTED_PREFLIGHT_SHA256 = "359e08d725ae1247a124356b2246f55368b40b0a35e4fb6981cb3789017c094f"
EXPECTED_FAILURE_DIGEST = "2802214e945c1365559e921d19f4f00670de4b3dc1464dad4fb9fca2266e1917"
ATTEMPT_002_FAILURE_DIGEST = "898159a74c5339d789a3c93aa22e546e9a6b91a7def137461d748ef32e24d284"
ATTEMPT_002_PREFLIGHT_DIGEST = "7870735375944c200b19bc1805481232d80e24cd694e4cba6f01c6b6819df662"
ATTEMPT_002_PREFLIGHT_SHA256 = "d5aaca47830af7c8c14fa22e9b3006b79ce3ca35b6f23edf48d81f8398b1108e"
ATTEMPT_003_FAILURE_DIGEST = "7ead61187975a9406f42348cc23b95b6bac2d4131f551cbc3793bb4989ee3875"
ATTEMPT_003_PREFLIGHT_DIGEST = "93b5b10ee65ded47daaadf7aacc6dfd1922b2e5cf7b2270ca10360bf51616efe"
ATTEMPT_003_PREFLIGHT_SHA256 = "39cf669f84818e6c4287e1e470a9a5f0b4884c2bac34a66208db84f26fb4474f"
ATTEMPT_004_FAILURE_DIGEST = "d809f9e15f4eb2bb33aa8a421b8e8c2ff690ad372eb245b0b555495d30ac638d"
ATTEMPT_004_PREFLIGHT_DIGEST = "a4952db2ffc6ff22ebeb7535c8990536b2c9e1f41144fca0741d8f26d84a300e"
ATTEMPT_004_PREFLIGHT_SHA256 = "84807c59e11ae09f3866b1fb2092537499c8d12135ce87123890f207c1c1bb2f"


def test_attempt_001_preflight_archive_is_byte_identical_and_non_evidence() -> None:
    source = (
        ROOT / "experiments/arecel-power7/rq1-workload-generalization-v1/rq1b-preflight-v1.json"
    )
    archived = ATTEMPT_ROOT / "rq1b-preflight-v1.json"
    failure_path = ATTEMPT_ROOT / "failure-v1.json"

    assert not source.exists()
    assert sha256_file(archived) == EXPECTED_PREFLIGHT_SHA256
    preflight = json.loads(archived.read_text(encoding="utf-8"))
    assert (
        semantic_digest(
            {key: value for key, value in preflight.items() if key != "semantic_digest"}
        )
        == EXPECTED_PREFLIGHT_DIGEST
    )

    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["status"] == "non-evidence-infrastructure-failure"
    assert failure["formal_invocation_count"] == 1
    assert failure["retry_performed"] is False
    assert failure["evidence_eligibility"] is False
    assert failure["preflight"]["semantic_digest"] == EXPECTED_PREFLIGHT_DIGEST
    assert (
        semantic_digest({key: value for key, value in failure.items() if key != "semantic_digest"})
        == EXPECTED_FAILURE_DIGEST
    )


def test_attempt_002_archive_is_byte_identical_and_pre_execution_non_evidence() -> None:
    archived = ATTEMPT_ROOT.parent / "attempt-002/rq1b-preflight-v1.json"
    failure_path = ATTEMPT_ROOT.parent / "attempt-002/failure-v1.json"
    assert sha256_file(archived) == ATTEMPT_002_PREFLIGHT_SHA256
    preflight = json.loads(archived.read_text(encoding="utf-8"))
    assert (
        semantic_digest(
            {key: value for key, value in preflight.items() if key != "semantic_digest"}
        )
        == ATTEMPT_002_PREFLIGHT_DIGEST
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["attempt_index"] == 2
    assert failure["status"] == "non-evidence-pre-execution-invocation-failure"
    assert failure["failure_stage"] == "managed-dsn-endpoint-validation"
    assert failure["evidence_eligibility"] is False
    assert failure["scientific_execution"]["explain_count"] == 0
    assert (
        semantic_digest({key: value for key, value in failure.items() if key != "semantic_digest"})
        == failure["semantic_digest"]
        == ATTEMPT_002_FAILURE_DIGEST
    )


def test_attempt_003_archive_is_byte_identical_and_tool_resolution_non_evidence() -> None:
    archived = ATTEMPT_ROOT.parent / "attempt-003/rq1b-preflight-v1.json"
    failure_path = ATTEMPT_ROOT.parent / "attempt-003/failure-v1.json"
    assert sha256_file(archived) == ATTEMPT_003_PREFLIGHT_SHA256
    preflight = json.loads(archived.read_text(encoding="utf-8"))
    assert (
        semantic_digest(
            {key: value for key, value in preflight.items() if key != "semantic_digest"}
        )
        == ATTEMPT_003_PREFLIGHT_DIGEST
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["attempt_index"] == 3
    assert failure["status"] == "non-evidence-pre-execution-tool-resolution-failure"
    assert failure["failure_stage"] == "design-stage-snapshot-command-launch"
    assert failure["failure_class"] == "frozen-advisor-console-script-not-resolvable"
    assert failure["evidence_eligibility"] is False
    assert failure["infrastructure"]["managed_lifecycle_validation_passed"] is True
    assert failure["scientific_execution"]["explain_count"] == 0
    assert all(
        value is False
        for key, value in failure["scientific_execution"].items()
        if key != "explain_count"
    )
    assert (
        semantic_digest({key: value for key, value in failure.items() if key != "semantic_digest"})
        == failure["semantic_digest"]
        == ATTEMPT_003_FAILURE_DIGEST
    )


def test_attempt_004_archive_is_byte_identical_and_environment_non_evidence() -> None:
    archived = ATTEMPT_ROOT.parent / "attempt-004/rq1b-preflight-v1.json"
    failure_path = ATTEMPT_ROOT.parent / "attempt-004/failure-v1.json"
    assert sha256_file(archived) == ATTEMPT_004_PREFLIGHT_SHA256
    preflight = json.loads(archived.read_text(encoding="utf-8"))
    assert (
        semantic_digest(
            {key: value for key, value in preflight.items() if key != "semantic_digest"}
        )
        == ATTEMPT_004_PREFLIGHT_DIGEST
    )
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["attempt_index"] == 4
    assert failure["status"] == "non-evidence-pre-execution-environment-failure"
    assert failure["failure_stage"] == "launcher-probe"
    assert failure["failure_class"] == "frozen-advisor-runtime-dependency-missing"
    assert failure["missing_dependency"] == "pyarrow"
    assert failure["evidence_eligibility"] is False
    assert failure["retry_performed"] is False
    assert failure["scientific_execution"]["explain_count"] == 0
    assert all(
        value is False
        for key, value in failure["scientific_execution"].items()
        if key != "explain_count"
    )
    assert (
        semantic_digest({key: value for key, value in failure.items() if key != "semantic_digest"})
        == failure["semantic_digest"]
        == ATTEMPT_004_FAILURE_DIGEST
    )
