from __future__ import annotations

import json
from pathlib import Path

from extstats_advisor_research.provenance import semantic_digest, sha256_file


ROOT = Path(__file__).resolve().parents[1]
ATTEMPT_ROOT = ROOT / (
    "experiments/arecel-power7/rq1-workload-generalization-v1/"
    "failed-attempts/attempt-001"
)
EXPECTED_PREFLIGHT_DIGEST = "84e398a709f82a3b7e7fd46715c36870d0a3832037c4d944043da6530e5f2152"
EXPECTED_PREFLIGHT_SHA256 = "359e08d725ae1247a124356b2246f55368b40b0a35e4fb6981cb3789017c094f"
EXPECTED_FAILURE_DIGEST = "2802214e945c1365559e921d19f4f00670de4b3dc1464dad4fb9fca2266e1917"


def test_attempt_001_preflight_archive_is_byte_identical_and_non_evidence() -> None:
    source = ROOT / "experiments/arecel-power7/rq1-workload-generalization-v1/rq1b-preflight-v1.json"
    archived = ATTEMPT_ROOT / "rq1b-preflight-v1.json"
    failure_path = ATTEMPT_ROOT / "failure-v1.json"

    assert source.read_bytes() == archived.read_bytes()
    assert sha256_file(archived) == EXPECTED_PREFLIGHT_SHA256
    preflight = json.loads(archived.read_text(encoding="utf-8"))
    assert semantic_digest(preflight) == EXPECTED_PREFLIGHT_DIGEST

    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["status"] == "non-evidence-infrastructure-failure"
    assert failure["formal_invocation_count"] == 1
    assert failure["retry_performed"] is False
    assert failure["evidence_eligibility"] is False
    assert failure["preflight"]["semantic_digest"] == EXPECTED_PREFLIGHT_DIGEST
    assert semantic_digest(
        {key: value for key, value in failure.items() if key != "semantic_digest"}
    ) == EXPECTED_FAILURE_DIGEST

